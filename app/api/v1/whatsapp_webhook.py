"""
app/api/v1/whatsapp_webhook.py
==============================
Meta WhatsApp Business Cloud API Webhook & Notification Dispatch Router.

Endpoints
─────────
  GET   /whatsapp/webhook    – Meta webhook verification handshake (hub.challenge)
  POST  /whatsapp/webhook    – Meta webhook event receiver (status receipts & inbound messages)
  POST  /whatsapp/send-test  – Authenticated test endpoint to verify message dispatch
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, Field

from app.api.v1.dependencies import get_current_active_user
from app.core.config import settings
from app.core.notification_service import sanitize_phone_number, send_whatsapp_direct

logger = logging.getLogger(__name__)
router = APIRouter()


class WhatsAppTestSendRequest(BaseModel):
    phone: str = Field(..., description="Recipient phone number (e.g. +97452013171 or local 8 digits)")
    message: Optional[str] = Field(None, description="Free-text message body (used within 24h service window)")
    title: Optional[str] = Field("Inara Yoga", description="Optional title or headline")
    template_name: Optional[str] = Field(None, description="Approved template name (e.g. hello_world)")
    template_language: str = Field("en_US", description="Template language code (e.g. en, en_US)")


# ════════════════════════════════════════════════════════════════════════════
# META WEBHOOK VERIFICATION (GET)
# ════════════════════════════════════════════════════════════════════════════

@router.get(
    "/webhook",
    summary="Meta WhatsApp Webhook Verification",
    response_class=Response,
)
async def verify_webhook(
    hub_mode: Optional[str] = Query(None, alias="hub.mode"),
    hub_verify_token: Optional[str] = Query(None, alias="hub.verify_token"),
    hub_challenge: Optional[str] = Query(None, alias="hub.challenge"),
):
    """
    Handles Meta's webhook verification handshake.
    When configuring the callback URL in Meta App Dashboard, Meta sends a GET request
    with `hub.mode=subscribe`, `hub.verify_token`, and `hub.challenge`.
    If the verify token matches WHATSAPP_WEBHOOK_VERIFY_TOKEN, we echo hub.challenge with 200 OK.
    """
    configured_token = getattr(settings, "WHATSAPP_WEBHOOK_VERIFY_TOKEN", "inara_waba_verify_2026")

    if hub_mode == "subscribe" and hub_verify_token == configured_token:
        logger.info("WhatsApp webhook verified successfully.")
        return Response(content=hub_challenge or "", media_type="text/plain", status_code=status.HTTP_200_OK)

    logger.warning(
        "WhatsApp webhook verification failed. Received mode=%s, token=%s",
        hub_mode,
        hub_verify_token,
    )
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Verification token mismatch or invalid mode",
    )


# ════════════════════════════════════════════════════════════════════════════
# META WEBHOOK EVENT RECEIVER (POST)
# ════════════════════════════════════════════════════════════════════════════

@router.post(
    "/webhook",
    summary="Meta WhatsApp Webhook Events",
)
async def receive_webhook(request: Request):
    """
    Receives real-time delivery updates and inbound customer messages from Meta.
    Always returns HTTP 200 so Meta does not retry indefinitely.
    """
    try:
        payload = await request.json()
    except Exception:
        return {"status": "ok", "message": "empty or non-json body"}

    entry_list = payload.get("entry", [])
    for entry in entry_list:
        for change in entry.get("changes", []):
            value = change.get("value", {})

            # 1. Message delivery status receipts (sent, delivered, read, failed)
            statuses = value.get("statuses", [])
            for st in statuses:
                recipient_id = st.get("recipient_id")
                msg_status = st.get("status")
                msg_id = st.get("id")
                errors = st.get("errors")
                if errors:
                    logger.warning(
                        "WhatsApp delivery failed for %s (msg_id: %s): %s",
                        recipient_id, msg_id, errors
                    )
                else:
                    logger.info(
                        "WhatsApp message %s to %s status: %s",
                        msg_id, recipient_id, msg_status
                    )

            # 2. Inbound messages from users
            messages = value.get("messages", [])
            for msg in messages:
                sender = msg.get("from")
                msg_type = msg.get("type")
                logger.info("Inbound WhatsApp message received from %s (type: %s)", sender, msg_type)

    return {"status": "ok"}


# ════════════════════════════════════════════════════════════════════════════
# SEND TEST NOTIFICATION (ADMIN / MANAGER ONLY)
# ════════════════════════════════════════════════════════════════════════════

@router.post(
    "/send-test",
    summary="Send Test WhatsApp Message",
)
async def send_test_message(
    data: WhatsAppTestSendRequest,
    current_user=Depends(get_current_active_user),
):
    """
    Trigger a direct WhatsApp message to test the WABA Cloud API connection.
    Requires Admin or Manager role.
    """
    user_role = getattr(current_user, "role", "")
    if user_role not in ("ADMIN", "SUPERADMIN", "MANAGER"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only administrators and managers can send test WhatsApp notifications.",
        )

    res = await send_whatsapp_direct(
        phone=data.phone,
        message=data.message or "This is a test notification from Inara Yoga WhatsApp integration.",
        title=data.title or "Inara Yoga",
        template_name=data.template_name,
        template_language=data.template_language,
    )

    if not res.get("success"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"WhatsApp send failed: {res.get('error')}",
        )

    return {
        "status": "success",
        "message": f"WhatsApp message dispatched to {sanitize_phone_number(data.phone)}",
        "data": res.get("data"),
    }
