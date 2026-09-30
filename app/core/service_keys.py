"""
app/core/service_keys.py
========================
Read-only Service API Keys — core logic.

A service key is a static credential for machine-to-machine reporting (for
example an AI agent or a scheduled job). It belongs to no person:

  • The key is linked to a dedicated *service account* — a real `User` row
    whose e-mail ends in `@service.inara.internal`. Using a real row means every
    existing service-layer check (`_require_admin(user_id)` and similar) keeps
    working without changes.
  • The service account can never sign in with a password (blocked in
    `auth.py`), and receives no notifications.
  • The raw key is shown once, when it is created. Only its SHA-256 hash is
    stored, in the existing `Otp` table (type = "SERVICE_API_KEY"). No
    schema change is needed.

Enforcement is done by `app/core/service_key_middleware.py`, which runs before
routing:
  1. Key validity     → 401 if missing, unknown, expired, revoked or for the wrong environment.
  2. Method gate      → 403 for anything other than GET / HEAD.
  3. Path allowlist   → 403 for any path not explicitly allowlisted.
  4. IP allowlist     → 403 if SERVICE_KEY_ALLOWED_IPS is set and the caller's IP is not on it.
  5. Rate limit       → 429 + Retry-After when the per-key budget is used up.
  6. Redaction        → e-mail, phone, date-of-birth and address fields are
                         stripped from every JSON response sent to a key.

Key format:  inara_ro_<env>_<43 url-safe chars>
             e.g. inara_ro_live_Vb3k...  (production)
                  inara_ro_stg_9QfZ...   (staging)
"""

from __future__ import annotations

import hashlib
import re
import secrets
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Pattern, Tuple

from app.core.config import settings
from app.db.db_client import prisma


# ════════════════════════════════════════════════════════════════════════════
# CONSTANTS
# ════════════════════════════════════════════════════════════════════════════

SERVICE_KEY_OTP_TYPE: str = "SERVICE_API_KEY"
SERVICE_ACCOUNT_EMAIL_DOMAIN: str = "service.inara.internal"
SERVICE_KEY_PREFIX: str = "inara_ro_"

# Only these HTTP methods are ever allowed with a service key.
READ_ONLY_METHODS = frozenset({"GET", "HEAD"})

# Default allowlist: regex patterns matched against the FULL request path
# (API prefix included). Anything not matched here returns 403.
#
# Scoped to the reporting endpoints agreed with the client (Inara Yoga,
# 20 Sep 2026). Explicitly EXCLUDED: /users/*, /role-matrix/*, /auth/*, all
# notify routes, every payment-initiating route, per-caller "my ..." routes
# (they would return the service account's own, empty data), single-record
# profile routes and Excel exports (they carry contact details).
_P = re.escape(settings.API_V1_PREFIX)
DEFAULT_ALLOWED_PATH_PATTERNS: List[str] = [
    # Headline metrics, booking detail and the full report
    rf"^{_P}/analytics/(overview|revenue|traffic|bookings|admin/full-report|date-formats|health)/?$",
    # Attendance: booking date, member, class, status
    rf"^{_P}/payments/bookings/all-history/?$",
    # Membership base and joining history
    rf"^{_P}/members/?$",
    rf"^{_P}/members/stats/?$",
    # Schedule, capacity, seats, price
    rf"^{_P}/classes/?$",
    rf"^{_P}/classes/[^/]+/bookings/?$",
    rf"^{_P}/courses/?$",
    rf"^{_P}/courses/[^/]+/enrollments/?$",
    # Catalogue and uptake
    rf"^{_P}/memberships/?$",
    rf"^{_P}/packages/?$",
    # Retail sales
    rf"^{_P}/store/products/?$",
    rf"^{_P}/order/orders/?$",
    rf"^{_P}/order/products/?$",
    # Teaching load
    rf"^{_P}/instructors/?$",
    rf"^{_P}/managers/?$",
]


# ════════════════════════════════════════════════════════════════════════════
# PURE HELPERS  (no DB, importable anywhere)
# ════════════════════════════════════════════════════════════════════════════

def build_service_account_email(name: str) -> str:
    """'AI Reporting' → 'ai-reporting@service.inara.internal'"""
    slug = re.sub(r"[^a-z0-9]+", "-", (name or "").strip().lower()).strip("-") or "reporting"
    return f"{slug}@{SERVICE_ACCOUNT_EMAIL_DOMAIN}"


def is_service_account_email(email: Optional[str]) -> bool:
    return bool(email) and str(email).lower().endswith("@" + SERVICE_ACCOUNT_EMAIL_DOMAIN)


def generate_raw_key(env_label: Optional[str] = None) -> str:
    """Create a new high-entropy key (256 bits). It is shown once and never stored."""
    env = (env_label or settings.SERVICE_KEY_ENV_LABEL).strip().lower()
    return f"{SERVICE_KEY_PREFIX}{env}_{secrets.token_urlsafe(32)}"


def hash_key(raw_key: str) -> str:
    """SHA-256 hex digest. The key is random and high-entropy, so a fast hash is appropriate."""
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def key_env_label(raw_key: str) -> Optional[str]:
    """Extract the environment label from 'inara_ro_<env>_<secret>'."""
    if not raw_key or not raw_key.startswith(SERVICE_KEY_PREFIX):
        return None
    rest = raw_key[len(SERVICE_KEY_PREFIX):]
    env, sep, secret = rest.partition("_")
    return env if (sep and secret) else None


def key_fingerprint(raw_or_hash: str) -> str:
    """Short, non-secret identifier for logs and CLI output."""
    h = raw_or_hash if re.fullmatch(r"[0-9a-f]{64}", raw_or_hash or "") else hash_key(raw_or_hash)
    return h[:12]


def _compile_allowlist() -> List[Pattern[str]]:
    override = (settings.SERVICE_KEY_ALLOWED_PATHS or "").strip()
    patterns = [p.strip() for p in override.split(";") if p.strip()] if override else DEFAULT_ALLOWED_PATH_PATTERNS
    return [re.compile(p) for p in patterns]


_ALLOWLIST: List[Pattern[str]] = _compile_allowlist()


def is_path_allowed(path: str) -> bool:
    return any(p.match(path or "") for p in _ALLOWLIST)


def _parse_ip_allowlist() -> List[str]:
    return [ip.strip() for ip in (settings.SERVICE_KEY_ALLOWED_IPS or "").split(",") if ip.strip()]


def is_ip_allowed(client_ip: Optional[str]) -> bool:
    allowed = _parse_ip_allowlist()
    if not allowed:
        return True                      # allowlisting not enabled
    return bool(client_ip) and client_ip in allowed


# ════════════════════════════════════════════════════════════════════════════
# PERSONAL-DATA REDACTION  (applied to every JSON response sent to a key)
# ════════════════════════════════════════════════════════════════════════════
#
# The client asked for names / stable IDs, gender and join date only, and
# explicitly NOT e-mail addresses, telephone numbers or dates of birth.
# Any JSON key whose normalised name (lower-case, "_" and "-" removed)
# CONTAINS one of the fragments below, or EQUALS one of the exact names, is
# removed from the response wherever it appears (at any nesting depth).
# E-mail addresses embedded inside other string values are masked as well.

_REDACT_KEY_FRAGMENTS = ("email", "phone", "mobile", "whatsapp")
_REDACT_KEY_EXACT = frozenset({"dateofbirth", "dob", "birthdate", "birthday", "address"})
_EMAIL_IN_TEXT = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
REDACTED_EMAIL_PLACEHOLDER = "[redacted]"


def _is_redacted_key(key: str) -> bool:
    k = re.sub(r"[_\-\s]", "", str(key)).lower()
    return k in _REDACT_KEY_EXACT or any(f in k for f in _REDACT_KEY_FRAGMENTS)


def redact_personal_data(value):
    """Return a copy of a decoded-JSON value with personal contact data removed."""
    if isinstance(value, dict):
        return {k: redact_personal_data(v) for k, v in value.items() if not _is_redacted_key(k)}
    if isinstance(value, list):
        return [redact_personal_data(v) for v in value]
    if isinstance(value, str) and "@" in value:
        return _EMAIL_IN_TEXT.sub(REDACTED_EMAIL_PLACEHOLDER, value)
    return value


# ════════════════════════════════════════════════════════════════════════════
# PRINCIPAL RESOLUTION  (DB-backed, with a short in-process cache)
# ════════════════════════════════════════════════════════════════════════════

@dataclass
class ServicePrincipal:
    key_hash: str
    key_id: str            # Otp row id
    user: object           # Prisma User row of the service account


_cache: Dict[str, Tuple[float, Optional[ServicePrincipal]]] = {}
_cache_lock = threading.Lock()


def invalidate_cache() -> None:
    with _cache_lock:
        _cache.clear()


async def resolve_service_principal(raw_key: str) -> Optional[ServicePrincipal]:
    """
    Return the ServicePrincipal for a raw key, or None if the key is invalid,
    expired, revoked, for a different environment, or its account is inactive.
    Results are cached for SERVICE_KEY_CACHE_TTL_SECONDS, so a revocation
    takes effect within that window.
    """
    if not raw_key or key_env_label(raw_key) != settings.SERVICE_KEY_ENV_LABEL.strip().lower():
        return None

    key_hash = hash_key(raw_key)
    now = time.monotonic()
    with _cache_lock:
        hit = _cache.get(key_hash)
        if hit and hit[0] > now:
            return hit[1]

    principal: Optional[ServicePrincipal] = None
    record = await prisma.otp.find_first(
        where={
            "code": key_hash,
            "type": SERVICE_KEY_OTP_TYPE,
            "expiresAt": {"gt": datetime.now(timezone.utc)},
        }
    )
    if record and is_service_account_email(record.email):
        user = await prisma.user.find_unique(where={"email": record.email})
        if user is not None and user.isActive:
            principal = ServicePrincipal(key_hash=key_hash, key_id=record.id, user=user)

    with _cache_lock:
        _cache[key_hash] = (now + max(0, settings.SERVICE_KEY_CACHE_TTL_SECONDS), principal)
    return principal


# ════════════════════════════════════════════════════════════════════════════
# RATE LIMITER  (fixed 60-second window, per key, per worker process)
# ════════════════════════════════════════════════════════════════════════════

class FixedWindowRateLimiter:
    """
    Simple in-memory limiter. Returns (allowed, remaining, retry_after_seconds).

    Note: the counters live in each uvicorn worker process. With `--workers 2`
    the effective ceiling is up to 2 × limit. Set the per-worker value
    accordingly, or move this to Redis if exact global limits are needed.
    """

    def __init__(self, limit_per_minute: int):
        self.limit = max(1, int(limit_per_minute))
        self._windows: Dict[str, Tuple[int, int]] = {}
        self._lock = threading.Lock()

    def hit(self, bucket: str) -> Tuple[bool, int, int]:
        now = time.time()
        window = int(now // 60)
        with self._lock:
            w, count = self._windows.get(bucket, (window, 0))
            if w != window:
                w, count = window, 0
            if count >= self.limit:
                retry_after = max(1, int(60 - (now % 60)))
                self._windows[bucket] = (w, count)
                return False, 0, retry_after
            count += 1
            self._windows[bucket] = (w, count)
            return True, self.limit - count, 0


rate_limiter = FixedWindowRateLimiter(settings.SERVICE_KEY_RATE_LIMIT_PER_MINUTE)


# ════════════════════════════════════════════════════════════════════════════
# ADMIN OPERATIONS  (used by the CLI in app/Scripts/service_keys.py)
# ════════════════════════════════════════════════════════════════════════════

# Backdated so the service account never counts as a "new member" in any
# analytics period (dashboard charts count users by createdAt).
_SERVICE_ACCOUNT_CREATED_AT = datetime(2000, 1, 1, tzinfo=timezone.utc)


async def ensure_service_account(name: str):
    """Create the service-account User row if it does not exist, and return it."""
    from app.core.security import get_password_hash   # lazy import

    email = build_service_account_email(name)
    user = await prisma.user.find_unique(where={"email": email})
    if user:
        return user
    return await prisma.user.create(
        data={
            "email": email,
            # Random, discarded secret: nobody knows it, and password login is
            # also blocked for service accounts in auth.py.
            "passwordHash": get_password_hash(secrets.token_urlsafe(48)),
            "name": f"{name.strip() or 'Reporting'} (read-only service)",
            "role": "ADMIN",            # can SEE admin-level data; writes are blocked by the middleware
            "isActive": True,
            "isVerified": True,
            "appNotificationsEnabled": False,
            "whatsappNotificationsEnabled": False,
            "createdAt": _SERVICE_ACCOUNT_CREATED_AT,
        }
    )


async def issue_key(name: str, valid_days: int = 365) -> Tuple[str, str]:
    """Create a key for the named service account. Returns (raw_key, fingerprint)."""
    user = await ensure_service_account(name)
    raw = generate_raw_key()
    await prisma.otp.create(
        data={
            "email": user.email,
            "code": hash_key(raw),
            "type": SERVICE_KEY_OTP_TYPE,
            "expiresAt": datetime.now(timezone.utc) + timedelta(days=max(1, valid_days)),
        }
    )
    invalidate_cache()
    return raw, key_fingerprint(raw)


async def list_keys() -> list:
    rows = await prisma.otp.find_many(where={"type": SERVICE_KEY_OTP_TYPE})
    return [
        {
            "fingerprint": key_fingerprint(r.code),
            "account": r.email,
            "createdAt": r.createdAt,
            "expiresAt": r.expiresAt,
        }
        for r in rows
    ]


async def revoke_key(fingerprint: str) -> int:
    """Revoke every key whose fingerprint (first 12 hex chars of its hash) matches."""
    rows = await prisma.otp.find_many(where={"type": SERVICE_KEY_OTP_TYPE})
    n = 0
    for r in rows:
        if r.code.startswith(fingerprint):
            await prisma.otp.delete(where={"id": r.id})
            n += 1
    invalidate_cache()
    return n


__all__ = [
    "SERVICE_KEY_OTP_TYPE", "SERVICE_ACCOUNT_EMAIL_DOMAIN", "SERVICE_KEY_PREFIX",
    "READ_ONLY_METHODS", "DEFAULT_ALLOWED_PATH_PATTERNS",
    "build_service_account_email", "is_service_account_email",
    "generate_raw_key", "hash_key", "key_env_label", "key_fingerprint",
    "is_path_allowed", "is_ip_allowed", "redact_personal_data",
    "ServicePrincipal", "resolve_service_principal", "invalidate_cache",
    "FixedWindowRateLimiter", "rate_limiter",
    "ensure_service_account", "issue_key", "list_keys", "revoke_key",
]
