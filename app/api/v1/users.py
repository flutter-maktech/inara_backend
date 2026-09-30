"""
app/api/v1/users.py
===================
User profile endpoints:
  GET    /users/me               → Get current user profile
  GET    /users/attendance_stats → "My Practice" attendance statistics
  PATCH  /users/update           → Update profile (name, bio, phone, gender, avatar, dateOfBirth)
  PATCH  /users/update-password  → Change account password
  DELETE /users                  → Permanently delete your own account (USER role only)
"""

from fastapi import APIRouter, Depends, status, HTTPException, File, Form, Query, UploadFile
from app.core.cloudinary_service import upload_image
from app.core.security import verify_password, get_password_hash
from app.core.account_deletion_service import purge_and_delete_user
from typing import Literal, Optional
from app.db.db_client import prisma
from app.models.user import UserResponse, UserUpdate
from app.models.attendance_model import UserAttendanceStatsResponse
from app.services.attendance_service import AttendanceService
from app.api.v1.dependencies import get_current_active_user
from prisma.enums import UserRole
from pydantic import BaseModel, Field

router = APIRouter()


# ============================================
# GET CURRENT USER
# ============================================

@router.get("/me", response_model=UserResponse, status_code=status.HTTP_200_OK)
async def read_user_me(current_user: UserResponse = Depends(get_current_active_user)):
    """
    Return the authenticated user's full profile.

    **Includes:**
    - id, email, name, bio, phone, gender, avatar
    - dateOfBirth (DD-MM format, e.g. "25-12" — year is never stored)
    - role, isActive, isVerified, createdAt
    """
    return current_user


# ============================================
# ATTENDANCE STATISTICS ("My Practice")
# ============================================

@router.get(
    "/attendance_stats",
    response_model=UserAttendanceStatsResponse,
    status_code=status.HTTP_200_OK,
    summary="My Practice — Attendance Statistics",
    description="""
    **Attendance statistics for the authenticated user's own practice history.**

    A class booking counts toward these stats once it has actually gone
    through (status `CONFIRMED` or `ATTENDED` — never `CANCELLED`, and never
    a still-pending checkout) **and** its scheduled date/time has already
    passed. You can't have practiced a class that hasn't happened yet.

    **Query parameter:**
    - `period` — `month` (this calendar month so far), `year` (this calendar
      year so far), or `all` (since you joined). Defaults to `all`.

    **Response includes:**
    - `total_classes`      — classes attended within the selected period
    - `hours_on_the_mat`   — total practice time, formatted (e.g. "13h 45m")
    - `most_practiced`     — the class you've attended most often
    - `most_often_with`    — the instructor you've practiced with most often
    - `weekly_average`     — average classes per week within the period
    - `member_since`       — your join date
    - `monthly_trend`      — trailing 6 months of class counts, for a practice graph

    **UI Reference:** "My Practice" / Attendance Statistics screen
    **Access:** Any authenticated user — always your own data only.
    """,
)
async def get_attendance_stats(
    period: Literal["month", "year", "all"] = Query(
        "all", description="Filter window: 'month', 'year', or 'all'"
    ),
    current_user: UserResponse = Depends(get_current_active_user),
):
    return await AttendanceService.get_attendance_stats(
        user_id=current_user.id,
        period=period,
    )


# ============================================
# UPDATE PROFILE
# ============================================

@router.patch("/update", response_model=UserResponse, status_code=status.HTTP_200_OK)
async def update_user(
    name: Optional[str] = Form(None),
    bio: Optional[str] = Form(None),
    phone: Optional[str] = Form(None),
    gender: Optional[str] = Form(None),
    # Day + month only — year intentionally excluded for privacy.
    # Format: "DD-MM" e.g. "25-12" for 25th December.
    dateOfBirth: Optional[str] = Form(
        None,
        description="Birthday in DD-MM format (day and month only). E.g. '25-12' for 25th December."
    ),
    avatar: UploadFile = File(default=None),
    current_user: UserResponse = Depends(get_current_active_user),
):
    """
    Update the authenticated user's profile.

    All fields are optional — send only what needs to change.

    **Fields:**
    - `name`         : Display name
    - `bio`          : Short bio / about text
    - `phone`        : Phone number
    - `gender`       : Gender
    - `dateOfBirth`  : Birthday in DD-MM format (day + month only, e.g. "25-12")
    - `avatar`       : Profile picture (uploaded as multipart file)

    **Note:** This endpoint uses `multipart/form-data` to support avatar uploads.
    """
    avatar_url = None
    if avatar and avatar.filename:
        avatar_url = await upload_image(avatar, folder="users/avatars")

    update_obj = UserUpdate(
        name=name,
        bio=bio,
        phone=phone,
        gender=gender,
        dateOfBirth=dateOfBirth,
        avatar=avatar_url,
    )

    updated_user = await prisma.user.update(
        where={"id": current_user.id},
        data=update_obj.model_dump(exclude_unset=True, exclude_none=True),
    )
    return updated_user


# ============================================
# UPDATE / RESET PASSWORD
# ============================================

class PasswordUpdate(BaseModel):
    currentPassword: str
    newPassword: str
    confirmPassword: str


@router.patch(
    "/update-password",
    status_code=status.HTTP_200_OK,
    summary="Update Account Password",
    description="""
    **Update your account password from Settings.**

    Requires the current password for verification, then sets the new password.

    **Fields:**
    - `currentPassword` — your existing password
    - `newPassword` — the new password you want to set
    - `confirmPassword` — must match newPassword exactly

    **Rules:**
    - New password must be at least 8 characters
    - New password must differ from the current password
    """,
)
async def update_password(
    data: PasswordUpdate,
    current_user: UserResponse = Depends(get_current_active_user),
):
    # 1. Confirm new passwords match
    if data.newPassword != data.confirmPassword:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="New password and confirm password do not match.",
        )

    # 2. Enforce minimum length
    if len(data.newPassword) < 8:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="New password must be at least 8 characters long.",
        )

    # 3. Fetch the full user record (includes passwordHash)
    db_user = await prisma.user.find_unique(where={"id": current_user.id})
    if not db_user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")

    # 4. Verify current password
    if not verify_password(data.currentPassword, db_user.passwordHash):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Current password is incorrect.",
        )

    # 5. Prevent reusing the same password
    if verify_password(data.newPassword, db_user.passwordHash):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="New password must be different from the current password.",
        )

    # 6. Hash and save the new password
    new_hash = get_password_hash(data.newPassword)
    await prisma.user.update(
        where={"id": current_user.id},
        data={"passwordHash": new_hash},
    )

    return {"message": "Password updated successfully."}


# ============================================
# DELETE OWN ACCOUNT (USER role only)
# ============================================

class AccountDeleteRequest(BaseModel):
    password: str = Field(
        ...,
        description="Your current password — required to confirm this irreversible action.",
    )


@router.delete(
    "",
    status_code=status.HTTP_200_OK,
    summary="Delete My Account",
    description="""
    **Permanently delete your own account.**

    ⚠️ **This action is irreversible.** Your bookings, memberships, cart
    items, orders, wallet (and its transaction history), payment logs,
    reviews, wishlists, and notifications are all permanently removed
    along with your profile, in a single atomic operation.

    **Requires:** your current password in the request body, to confirm the
    request truly came from you — the same safeguard already used by
    `PATCH /users/update-password`.

    **Access:** USER accounts only. Staff accounts (Admin, Manager,
    Instructor) are not deleted through this self-service endpoint — an
    administrator manages those via `DELETE /api/v1/instructors/{id}` and
    `DELETE /api/v1/managers/{id}`.
    """,
)
async def delete_own_account(
    data: AccountDeleteRequest,
    current_user: UserResponse = Depends(get_current_active_user),
):
    # 1. Only USER-role accounts may self-delete through this endpoint.
    #    Staff roles are intentionally out of scope here — see docstring.
    if current_user.role != UserRole.USER:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only user accounts can delete their own account through this endpoint.",
        )

    # 2. Confirm identity with the current password before doing anything
    #    irreversible — mirrors the check already used in update_password.
    db_user = await prisma.user.find_unique(where={"id": current_user.id})
    if not db_user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")

    if not verify_password(data.password, db_user.passwordHash):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Password is incorrect.",
        )

    # 3. Purge every row that would otherwise block the delete via an
    #    ON DELETE RESTRICT foreign key, then delete the account itself —
    #    all inside a single atomic transaction. See account_deletion_service.py
    #    for exactly which tables this covers and why it's necessary.
    await purge_and_delete_user(current_user.id)

    return {
        "message": "Your account has been permanently deleted.",
        "userId": current_user.id,
    }