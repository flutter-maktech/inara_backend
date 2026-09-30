"""
app/core/config.py
==================
Production-hardened settings for the Inara backend.
"""

from __future__ import annotations

import json
from typing import Any, List, Optional
from urllib.parse import urlparse

from pydantic import field_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):

    # ── Application ───────────────────────────────────
    APP_NAME: str = "Inara"
    DEBUG: bool = True  # Never default True in production
    API_V1_PREFIX: str = "/api/v1"

    # ── Database ──────────────────────────────────────
    DATABASE_URL: str

    # ── Security ──────────────────────────────────────
    SECRET_KEY: str
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_DAYS: int = 10

    # ── CORS ──────────────────────────────────────────
    ALLOWED_ORIGINS: str = "http://localhost:3000"

    # ── Email (SMTP) ──────────────────────────────────
    SMTP_TLS: bool = True
    SMTP_PORT: Optional[int] = 587
    SMTP_HOST: Optional[str] = None
    SMTP_USER: Optional[str] = None
    SMTP_PASSWORD: Optional[str] = None
    EMAILS_FROM_EMAIL: Optional[str] = None
    EMAILS_FROM_NAME: Optional[str] = None

    # ── Cloudinary ────────────────────────────────────
    CLOUDINARY_CLOUD_NAME: str
    CLOUDINARY_API_KEY: str
    CLOUDINARY_API_SECRET: str

    # ── MyFatoorah Payment Gateway ────────────────────
    MYFATOORAH_BASE_URL: str = "https://apitest.myfatoorah.com"
    MYFATOORAH_API_KEY: str = ""
    MYFATOORAH_WEBHOOK_SECRET: str = ""

    PAYMENT_SUCCESS_URL: str = "http://localhost:8000/api/v1/payments/callback/success"
    PAYMENT_ERROR_URL: str = "http://localhost:8000/api/v1/payments/callback/error"

    # ── Mobile deep-link redirect ─────────────────────
    APP_DEEP_LINK_SCHEME: str = "inara"
    APP_FALLBACK_URL: str = "https://inara-backend.maktechapp.cloud"

    # ── App Store links (used in invitation acceptance flow) ──────────
    IOS_APP_URL: str = "https://apps.apple.com/app/inara-yoga"
    ANDROID_APP_URL: str = "https://play.google.com/store/apps/details?id=com.inara.yoga"

    # ── Dashboard sign-in URL ──────────────────────────
    # Where newly onboarded team members (Admin/Manager/Instructor) land
    # after accepting an invitation, to log in to the web dashboard.
    # Previously hardcoded in roleMatrix.py and roleMatrix_service.py —
    # now sourced from .env so it can differ per environment/deployment.
    DASHBOARD_LOGIN_URL: str = "https://inara-dashboard.app.maktechapp.cloud/"

    # ── WhatsApp Business Cloud API ───────────────────
    # Phone Number ID  : 1176121902245206
    # App Access Token : 1622669328846991|vUvf0qINepwsujMLJUI5GoCzow0
    # WABA Account ID  : 27116250234729298
    # App ID           : 1622669328846991
    # Test phone       : +1 (555) 647-2522
    WHATSAPP_PHONE_NUMBER_ID: Optional[str] = None
    WHATSAPP_ACCESS_TOKEN: Optional[str] = None
    WHATSAPP_API_VERSION: str = "v18.0"
    WHATSAPP_BUSINESS_ACCOUNT_ID: Optional[str] = None  # informational / webhook verify

    # ── Read-only Service API Keys ────────────────────
    # Static, GET-only credentials for machine-to-machine reporting.
    # See app/core/service_keys.py and app/core/service_key_middleware.py.
    SERVICE_KEY_ENABLED: bool = True
    SERVICE_KEY_ENV_LABEL: str = "live"            # "live" on production, "stg" on staging
    SERVICE_KEY_HEADER_NAME: str = "X-API-Key"
    SERVICE_KEY_RATE_LIMIT_PER_MINUTE: int = 60    # per key, per worker process
    SERVICE_KEY_CACHE_TTL_SECONDS: int = 30        # a revocation takes effect within this window
    SERVICE_KEY_ALLOWED_PATHS: str = ""            # optional semicolon-separated regex override of the default allowlist
    SERVICE_KEY_ALLOWED_IPS: str = ""              # optional comma-separated client IPs; empty = no IP restriction
    SERVICE_KEY_REDACT_PERSONAL_DATA: bool = True  # strip e-mail / phone / date-of-birth / address from key responses

    # ── Server ────────────────────────────────────────
    BACKEND_PORT: int = 8000

    # ── Validators ────────────────────────────────────

    @field_validator("DATABASE_URL", mode="before")
    @classmethod
    def fix_database_url_scheme(cls, v: Any) -> str:
        """Prisma requires postgresql:// — silently normalise postgres:// URLs."""
        if isinstance(v, str) and v.startswith("postgres://"):
            return v.replace("postgres://", "postgresql://", 1)
        return v

    @field_validator("ALLOWED_ORIGINS", mode="before")
    @classmethod
    def parse_allowed_origins(cls, v: Any) -> str:
        """Accept JSON array, comma-separated string, or list — normalise to comma string."""
        if isinstance(v, list):
            return ",".join(str(o).strip() for o in v if str(o).strip())
        if not isinstance(v, str):
            return "http://localhost:3000"
        v_clean = v.strip()
        if not v_clean:
            return "http://localhost:3000"
        if v_clean.startswith("["):
            try:
                parsed = json.loads(v_clean)
                if isinstance(parsed, list):
                    return ",".join(str(o).strip() for o in parsed if str(o).strip())
            except json.JSONDecodeError:
                v_clean = v_clean.strip("[]").strip()
        clean = v_clean.strip('"').strip("'")
        return clean if clean else "http://localhost:3000"

    # ── Helpers ───────────────────────────────────────

    def get_allowed_origins(self) -> List[str]:
        """Return a clean list of CORS origins, trimmed of trailing slashes & quotes."""
        if not self.ALLOWED_ORIGINS:
            return ["http://localhost:3000"]
        raw = self.ALLOWED_ORIGINS.split(",") if "," in self.ALLOWED_ORIGINS else [self.ALLOWED_ORIGINS]
        cleaned: List[str] = []
        for origin in raw:
            o = origin.strip().strip('"').strip("'").rstrip("/")
            if o:
                cleaned.append(o)
        return cleaned or ["http://localhost:3000"]

    def get_trusted_hosts(self) -> List[str]:
        """Derive TrustedHostMiddleware host list from CORS origins."""
        hosts: List[str] = []
        for origin in self.get_allowed_origins():
            parsed = urlparse(origin)
            if parsed.hostname:
                hosts.append(parsed.hostname)
        # Always allow localhost for health checks from the container itself
        hosts.extend(["localhost", "127.0.0.1"])
        return list(dict.fromkeys(hosts))  # de-dupe, preserve order

    def whatsapp_is_configured(self) -> bool:
        """
        Returns True only when all required WhatsApp credentials are present.
        Use this as a guard before calling WhatsApp-specific logic.
        """
        return bool(self.WHATSAPP_ACCESS_TOKEN and self.WHATSAPP_PHONE_NUMBER_ID)

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "case_sensitive": True,
        "extra": "ignore",
    }


settings = Settings()