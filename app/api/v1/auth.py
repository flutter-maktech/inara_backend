"""
app/api/v1/auth.py
==================
Authentication endpoints:
  POST /register          → register + send OTP
  POST /verify-otp        → verify email OTP
  POST /login             → returns access_token + refresh_token
  POST /refresh           → rotates refresh token, returns new access_token
  POST /logout            → revokes refresh token
  POST /resend-otp        → resend verification OTP
  POST /forgot-password   → send password-reset OTP
  POST /reset-password    → validate OTP then apply new password
"""

from fastapi import APIRouter, Depends, HTTPException, status
from datetime import datetime, timedelta

from app.db.db_client import prisma
from app.core import security
from app.core.config import settings
from app.core.email import send_otp_email
from app.core.service_keys import is_service_account_email
from app.models.auth import (
    LoginRequest, RegisterRequest,
    Token, AccessToken,
    OTPVerifyRequest,
    PasswordResetRequest, PasswordResetConfirm,
    RefreshTokenRequest, LogoutRequest,
)
from app.models.user import UserResponse
from fastapi.security import OAuth2PasswordRequestForm

router = APIRouter()


# ── Internal helpers ──────────────────────────────────────────────────────────

async def _store_refresh_token(email: str, token: str) -> None:
    """Persist a refresh token in the Otp table (type=REFRESH_TOKEN)."""
    expires_at = datetime.utcnow() + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)
    await prisma.otp.create(
        data={
            "email": email,
            "code": token,
            "type": "REFRESH_TOKEN",
            "expiresAt": expires_at,
        }
    )


async def _revoke_refresh_token(token: str) -> None:
    """Delete a refresh token from the DB (revoke / rotate)."""
    record = await prisma.otp.find_first(
        where={"code": token, "type": "REFRESH_TOKEN"}
    )
    if record:
        await prisma.otp.delete(where={"id": record.id})


async def _revoke_all_refresh_tokens(email: str) -> None:
    """Revoke ALL refresh tokens for a user (e.g. on logout-all-devices)."""
    await prisma.otp.delete_many(
        where={"email": email, "type": "REFRESH_TOKEN"}
    )


# ── Register ──────────────────────────────────────────────────────────────────

@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def register(data: RegisterRequest):
    """
    Register a new user and send an email verification OTP.

    **dateOfBirth** is optional and accepts only day + month in `DD-MM` format
    (e.g. `"25-12"` for 25th December). The year is never stored.
    """
    user = await prisma.user.find_unique(where={"email": data.email})
    if user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already registered"
        )

    hashed_password = security.get_password_hash(data.password)
    user = await prisma.user.create(
        data={
            "email": data.email,
            "passwordHash": hashed_password,
            "name": data.name,
            "phone": data.phone,
            "gender": data.gender,
            "dateOfBirth": data.dateOfBirth,   # DD-MM string or None
            "isVerified": False,
        }
    )

    otp_code = security.generate_otp()
    expires_at = datetime.utcnow() + timedelta(minutes=10)
    await prisma.otp.create(
        data={
            "email": data.email,
            "code": otp_code,
            "type": "VERIFICATION",
            "expiresAt": expires_at,
        }
    )
    send_otp_email(data.email, otp_code, "verification")
    return user


# ── Verify OTP ────────────────────────────────────────────────────────────────

@router.post("/verify-otp", status_code=status.HTTP_200_OK)
async def verify_otp(data: OTPVerifyRequest):
    """Verify the email OTP and mark the user as verified."""
    otp = await prisma.otp.find_first(
        where={
            "email": data.email,
            "code": data.code,
            "type": "VERIFICATION",
            "expiresAt": {"gt": datetime.utcnow()},
        }
    )
    if not otp:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired OTP"
        )

    await prisma.user.update(
        where={"email": data.email},
        data={"isVerified": True}
    )
    await prisma.otp.delete(where={"id": otp.id})
    return {"message": "Email verified successfully"}


# ── Login ─────────────────────────────────────────────────────────────────────

@router.post("/login", response_model=Token)
async def login(form_data: OAuth2PasswordRequestForm = Depends()):
    """
    Authenticate with email + password.
    Returns a short-lived access_token and a long-lived refresh_token.
    """
    user = await prisma.user.find_unique(where={"email": form_data.username})

    if not user or not security.verify_password(form_data.password, user.passwordHash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password"
        )
    if not user.isVerified:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Email not verified"
        )
    if is_service_account_email(user.email):
        # Service accounts authenticate ONLY with their read-only API key.
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Service accounts cannot sign in with a password"
        )

    token_data = {"sub": user.email}
    access_token  = security.create_access_token(data=token_data)
    refresh_token = security.create_refresh_token(data=token_data)

    await _store_refresh_token(user.email, refresh_token)

    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
    }


# ── Refresh ───────────────────────────────────────────────────────────────────

@router.post("/refresh", response_model=Token)
async def refresh_tokens(data: RefreshTokenRequest):
    """
    Exchange a valid refresh token for a brand-new access + refresh token pair.
    The old refresh token is immediately revoked (rotation strategy).
    This prevents replay attacks — a stolen token can only be used once.
    """
    payload = security.decode_refresh_token(data.refresh_token)
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token"
        )

    email = payload.get("sub")
    if not email:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Malformed token"
        )

    db_token = await prisma.otp.find_first(
        where={
            "email": email,
            "code": data.refresh_token,
            "type": "REFRESH_TOKEN",
            "expiresAt": {"gt": datetime.utcnow()},
        }
    )
    if not db_token:
        await _revoke_all_refresh_tokens(email)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token has been revoked. Please log in again."
        )

    user = await prisma.user.find_unique(where={"email": email})
    if not user or not user.isActive:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found or deactivated"
        )

    await prisma.otp.delete(where={"id": db_token.id})

    token_data    = {"sub": email}
    access_token  = security.create_access_token(data=token_data)
    refresh_token = security.create_refresh_token(data=token_data)
    await _store_refresh_token(email, refresh_token)

    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
    }


# ── Logout ────────────────────────────────────────────────────────────────────

@router.post("/logout", status_code=status.HTTP_200_OK)
async def logout(data: LogoutRequest):
    """
    Revoke the supplied refresh token.
    The client must discard both tokens after calling this endpoint.
    """
    await _revoke_refresh_token(data.refresh_token)
    return {"message": "Logged out successfully"}


# ── Resend OTP ────────────────────────────────────────────────────────────────

@router.post("/resend-otp", status_code=status.HTTP_200_OK)
async def resend_otp(email: str):
    """Resend email verification OTP."""
    user = await prisma.user.find_unique(where={"email": email})
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user.isVerified:
        return {"message": "User already verified"}

    otp_code = security.generate_otp()
    expires_at = datetime.utcnow() + timedelta(minutes=10)
    await prisma.otp.create(
        data={
            "email": email,
            "code": otp_code,
            "type": "VERIFICATION",
            "expiresAt": expires_at,
        }
    )
    send_otp_email(email, otp_code, "verification")
    return {"message": "OTP resent successfully"}


# ── Forgot Password ───────────────────────────────────────────────────────────

@router.post("/forgot-password", status_code=status.HTTP_200_OK)
async def forgot_password(data: PasswordResetRequest):
    """
    Send a password-reset OTP to the user's email.

    - Deletes any existing PASSWORD_RESET OTPs for the email to prevent
      stale codes from being used.
    - Issues a fresh OTP valid for 10 minutes.
    - Always returns 200 (generic message) to avoid user enumeration.
    """
    user = await prisma.user.find_unique(where={"email": data.email})
    if not user or is_service_account_email(user.email):
        # Return a generic 200 to avoid leaking whether an email exists
        # (service accounts never get password-reset codes)
        return {"message": "If that email is registered, a password-reset code has been sent."}

    # Revoke any previous unused password-reset OTPs for this email
    await prisma.otp.delete_many(
        where={"email": data.email, "type": "PASSWORD_RESET"}
    )

    otp_code = security.generate_otp()
    expires_at = datetime.utcnow() + timedelta(minutes=10)
    await prisma.otp.create(
        data={
            "email": data.email,
            "code": otp_code,
            "type": "PASSWORD_RESET",
            "expiresAt": expires_at,
        }
    )
    send_otp_email(data.email, otp_code, "password_reset")
    return {"message": "If that email is registered, a password-reset code has been sent."}


# ── Reset Password ────────────────────────────────────────────────────────────

@router.post("/reset-password", status_code=status.HTTP_200_OK)
async def reset_password(data: PasswordResetConfirm):
    """
    Validate the password-reset OTP, then apply the new password and
    revoke all existing refresh tokens so every active session is
    invalidated after a password change.

    Requires:
      - `email`        : the account email
      - `otp_code`     : the 6-digit code sent to that email
      - `new_password` : the new password to set (min 8 characters)
    """
    # Validate the OTP
    otp = await prisma.otp.find_first(
        where={
            "email": data.email,
            "code": data.otp_code,
            "type": "PASSWORD_RESET",
            "expiresAt": {"gt": datetime.utcnow()},
        }
    )
    if not otp:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired password-reset code"
        )

    # Ensure the user exists
    user = await prisma.user.find_unique(where={"email": data.email})
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )

    # Consume (delete) the OTP immediately — single-use
    await prisma.otp.delete(where={"id": otp.id})

    # Apply the new password
    hashed_password = security.get_password_hash(data.new_password)
    await prisma.user.update(
        where={"email": data.email},
        data={"passwordHash": hashed_password}
    )

    # Invalidate all existing sessions
    await _revoke_all_refresh_tokens(data.email)

    return {"message": "Password reset successfully. Please log in again."}