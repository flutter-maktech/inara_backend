"""
app/models/auth.py
==================
Pydantic schemas for authentication endpoints.
"""

import re
from pydantic import BaseModel, EmailStr, field_validator
from typing import Optional


# ── Token Responses ───────────────────────────────────────────────────────────

class Token(BaseModel):
    """Response returned on successful login."""
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class AccessToken(BaseModel):
    """Response returned on successful token refresh."""
    access_token: str
    token_type: str = "bearer"


# ── Token Internals ───────────────────────────────────────────────────────────

class TokenData(BaseModel):
    email: Optional[str] = None


# ── Request Bodies ────────────────────────────────────────────────────────────

class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str
    name: Optional[str] = None
    phone: Optional[str] = None
    gender: Optional[str] = None
    # Day and month of birth only — year intentionally excluded for privacy.
    # Format: "DD-MM" e.g. "25-12" for 25th December.
    dateOfBirth: Optional[str] = None

    @field_validator("dateOfBirth")
    @classmethod
    def validate_date_of_birth(cls, v: Optional[str]) -> Optional[str]:
        """Validate that dateOfBirth is in DD-MM format with realistic values."""
        if v is None:
            return v
        if not re.fullmatch(r"\d{2}-\d{2}", v):
            raise ValueError("dateOfBirth must be in DD-MM format, e.g. '25-12'")
        day, month = int(v[:2]), int(v[3:])
        if not (1 <= month <= 12):
            raise ValueError("Month must be between 01 and 12")
        if not (1 <= day <= 31):
            raise ValueError("Day must be between 01 and 31")
        return v


class OTPVerifyRequest(BaseModel):
    email: EmailStr
    code: str


class PasswordResetRequest(BaseModel):
    email: EmailStr


class PasswordResetConfirm(BaseModel):
    """
    Payload for POST /auth/reset-password.

    Fields:
      - email        : the account email address
      - otp_code     : the 6-digit code received via email (from forgot-password)
      - new_password : the new password to set (min 8 characters enforced at service layer)
    """
    email: EmailStr
    otp_code: str
    new_password: str


class RefreshTokenRequest(BaseModel):
    """Request body for POST /auth/refresh."""
    refresh_token: str


class LogoutRequest(BaseModel):
    """Request body for POST /auth/logout."""
    refresh_token: str