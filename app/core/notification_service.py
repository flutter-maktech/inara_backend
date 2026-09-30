"""
app/core/notification_service.py
=================================
Production-grade multi-channel notification dispatcher.

The platform sends notifications through TWO concurrent channels:

  ┌────────────┬─────────────────────────────────────────────────────────────┐
  │ Channel    │ Transport                                                   │
  ├────────────┼─────────────────────────────────────────────────────────────┤
  │ APP        │ Persists a `Notification` row → drives the in-app inbox     │
  │            │ (delivered to the client via the /notifications API).       │
  │ WHATSAPP   │ WhatsApp Business Cloud API → text message to user.phone.   │
  └────────────┴─────────────────────────────────────────────────────────────┘

Design goals
────────────
* The two channels run **concurrently** (`asyncio.gather`). A slow vendor
  on WhatsApp never delays the in-app DB write, and vice versa.
* Per-channel failures are isolated — one bad recipient does NOT abort
  the batch. Every error is caught, logged, and rolled up into the
  returned summary so admins see exactly what happened.
* Per-user opt-out flags (`appNotificationsEnabled`, `whatsappNotificationsEnabled`)
  are read with `getattr(..., True)` so the dispatcher is schema-compatible —
  it works whether or not the User model exposes those columns. When the
  flags are absent (current schema), every user is treated as opted-in.
* Real WhatsApp integration via `httpx` behind a feature flag. When
  WhatsApp credentials are missing the WhatsApp send degrades to a
  no-op (logged) so dev environments never crash.

Public entry points
───────────────────
    dispatch_push_notification(...)     batch, multi-channel
    notify_user(...)                    convenience wrapper for one user
    resolve_channels(...)               normalises (app / whatsapp / both)
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Literal, Optional, Sequence

from app.db.db_client import prisma
from app.core.config import settings

log = logging.getLogger(__name__)

# Must match the Prisma `NotificationType` enum
NotificationType = Literal["INFO", "SUCCESS", "WARNING", "ERROR", "REMINDER"]


# ════════════════════════════════════════════════════════════════════════════
# CHANNEL RESOLVER (backward-compatible)
# ════════════════════════════════════════════════════════════════════════════

def resolve_channels(
    send_via_app: bool,
    send_via_whatsapp: bool,
    send_via_both: bool,
) -> tuple[bool, bool]:
    """
    Normalise the three-flag model used in request bodies into a clean
    (use_app, use_whatsapp) pair.

    Priority: ``send_via_both`` overrides the individual flags when True.

    Examples
    --------
        resolve_channels(True,  False, False)  -> (True,  False)
        resolve_channels(False, True,  False)  -> (False, True)
        resolve_channels(False, False, True)   -> (True,  True)
        resolve_channels(True,  True,  False)  -> (True,  True)
    """
    if send_via_both:
        return True, True
    return send_via_app, send_via_whatsapp


# ════════════════════════════════════════════════════════════════════════════
# MAIN DISPATCHER — batch, multi-channel, concurrent
# ════════════════════════════════════════════════════════════════════════════

async def dispatch_push_notification(
    *,
    users: Sequence[Any],
    title: str,
    message: str,
    notification_type: NotificationType = "INFO",
    send_via_app: bool = True,
    send_via_whatsapp: bool = False,
    action_url: Optional[str] = None,
) -> dict:
    """
    Dispatch a notification to a batch of users across the requested
    channels. The two channels are scheduled as concurrent tasks — a slow
    vendor on one channel cannot delay the other.

    Parameters
    ----------
    users : list[User]
        Prisma User records. Must expose `.id` and `.phone`.
    title, message : str
        Headline + body of the notification.
    notification_type : enum
        One of INFO | SUCCESS | WARNING | ERROR | REMINDER.
    send_via_app, send_via_whatsapp : bool
        Per-channel toggles.
    action_url : str, optional
        Deep-link or URL stored on the in-app notification row.

    Returns
    -------
    dict
        Per-channel counters + a `failed` total. Safe to log / expose to
        admins. Shape::

            {
              "sentViaApp":      int,
              "sentViaWhatsApp": int,
              "skippedApp":      int,
              "skippedWhatsApp": int,
              "failed":          int,
            }
    """
    if not users:
        return _empty_summary()

    counters = _Counter()
    tasks: list[asyncio.Task] = []

    for user in users:
        if send_via_app:
            tasks.append(asyncio.create_task(
                _send_app(user, title, message, notification_type, action_url, counters)
            ))
        if send_via_whatsapp:
            tasks.append(asyncio.create_task(
                _send_whatsapp(user, title, message, counters)
            ))

    if tasks:
        # `return_exceptions=True` is belt-and-braces — each task already
        # catches its own errors, but this prevents a stray raise from
        # poisoning the gather call.
        await asyncio.gather(*tasks, return_exceptions=True)

    return counters.to_dict()


async def notify_user(
    *,
    user_id: str,
    title: str,
    message: str,
    notification_type: NotificationType = "INFO",
    send_via_app: bool = True,
    send_via_whatsapp: bool = False,
    action_url: Optional[str] = None,
) -> dict:
    """
    Convenience wrapper that notifies a single user — used by services
    on lifecycle events (booking confirmed, order paid, etc.).

    Fails silently (returns an empty summary) if the user does not exist,
    so a missing record never breaks the business operation.
    """
    user = await prisma.user.find_unique(where={"id": user_id})
    if not user:
        log.warning("notify_user: user %s not found — skipping notification", user_id)
        return _empty_summary()

    return await dispatch_push_notification(
        users=[user],
        title=title,
        message=message,
        notification_type=notification_type,
        send_via_app=send_via_app,
        send_via_whatsapp=send_via_whatsapp,
        action_url=action_url,
    )


# ════════════════════════════════════════════════════════════════════════════
# PRIVATE PER-CHANNEL SENDERS
# ════════════════════════════════════════════════════════════════════════════

async def _send_app(
    user: Any,
    title: str,
    message: str,
    notification_type: str,
    action_url: Optional[str],
    counters: "_Counter",
) -> None:
    """Persist the notification to the DB (drives the in-app inbox)."""
    # Respect the user's in-app preference. Schema may not define the
    # column today — getattr(..., True) treats absence as opted-in.
    enabled = getattr(user, "appNotificationsEnabled", True)
    if enabled is None:
        enabled = True
    if not enabled:
        counters.skipped_app += 1
        return

    try:
        payload: dict[str, Any] = {
            "userId":  user.id,
            "title":   title,
            "message": message,
            "type":    notification_type,
        }
        if action_url:
            payload["actionUrl"] = action_url
        await prisma.notification.create(data=payload)
        counters.sent_app += 1
    except Exception as exc:
        log.error("In-app notification failed for user %s: %s", user.id, exc)
        counters.failed += 1


async def _send_whatsapp(
    user: Any,
    title: str,
    message: str,
    counters: "_Counter",
) -> None:
    """Send a WhatsApp Business Cloud API text message."""
    enabled = getattr(user, "whatsappNotificationsEnabled", True)
    if enabled is None:
        enabled = True
    if not enabled:
        counters.skipped_whatsapp += 1
        return

    phone: Optional[str] = getattr(user, "phone", None)
    if not phone:
        counters.skipped_whatsapp += 1
        return

    if not _whatsapp_ready():
        # Credentials missing — log and count as skipped so ops can see
        # the backlog at a glance, but don't crash dev environments.
        log.debug("WhatsApp credentials missing — would message %s | title=%r", phone, title)
        counters.skipped_whatsapp += 1
        return

    try:
        await _whatsapp_send(phone=phone, title=title, message=message)
        counters.sent_whatsapp += 1
    except Exception as exc:
        log.error("WhatsApp dispatch failed for %s: %s", phone, exc)
        counters.failed += 1


# ════════════════════════════════════════════════════════════════════════════
# VENDOR INTEGRATION — WhatsApp Business Cloud API
# ════════════════════════════════════════════════════════════════════════════

def _whatsapp_ready() -> bool:
    """True when WhatsApp credentials are configured in settings."""
    return bool(
        getattr(settings, "WHATSAPP_ACCESS_TOKEN", None)
        and getattr(settings, "WHATSAPP_PHONE_NUMBER_ID", None)
    )


async def _whatsapp_send(*, phone: str, title: str, message: str) -> None:
    """
    Real HTTP call to Meta's Graph API.

    Required environment variables (read via Settings):
        WHATSAPP_PHONE_NUMBER_ID   – the business phone number's WABA ID
        WHATSAPP_ACCESS_TOKEN      – permanent system-user access token
        WHATSAPP_API_VERSION       – optional, defaults to "v18.0"
    """
    import httpx  # local import keeps cold-start cheap when WhatsApp is unused

    access_token    = settings.WHATSAPP_ACCESS_TOKEN
    phone_number_id = settings.WHATSAPP_PHONE_NUMBER_ID
    api_version     = getattr(settings, "WHATSAPP_API_VERSION", "v18.0")

    # E.164 digits only — strip "+", spaces, and dashes
    clean_phone = phone.lstrip("+").replace(" ", "").replace("-", "")
    body = f"*{title}*\n\n{message}" if title else message

    url = f"https://graph.facebook.com/{api_version}/{phone_number_id}/messages"

    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(
            url,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type":  "application/json",
            },
            json={
                "messaging_product": "whatsapp",
                "to":   clean_phone,
                "type": "text",
                "text": {"body": body, "preview_url": False},
            },
        )

    # Surface non-2xx so the caller's try/except can log it
    resp.raise_for_status()


# ════════════════════════════════════════════════════════════════════════════
# INTERNAL: per-batch counter
# ════════════════════════════════════════════════════════════════════════════

class _Counter:
    """Mutable per-batch summary container."""

    __slots__ = (
        "sent_app", "sent_whatsapp",
        "skipped_app", "skipped_whatsapp",
        "failed",
    )

    def __init__(self) -> None:
        self.sent_app = 0
        self.sent_whatsapp = 0
        self.skipped_app = 0
        self.skipped_whatsapp = 0
        self.failed = 0

    def to_dict(self) -> dict[str, int]:
        return {
            "sentViaApp":      self.sent_app,
            "sentViaWhatsApp": self.sent_whatsapp,
            "skippedApp":      self.skipped_app,
            "skippedWhatsApp": self.skipped_whatsapp,
            "failed":          self.failed,
        }


def _empty_summary() -> dict[str, int]:
    return _Counter().to_dict()


__all__ = [
    "NotificationType",
    "resolve_channels",
    "dispatch_push_notification",
    "notify_user",
]