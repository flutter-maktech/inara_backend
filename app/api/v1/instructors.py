"""
Instructors API Router
======================
Instructors = Users with role == INSTRUCTOR.

Endpoints:
  GET    /instructors                         → Paginated instructor list + search
  GET    /instructors/export/excel            → Export instructor list to Excel
  GET    /instructors/{instructor_id}         → Instructor profile + upcoming classes
  PATCH  /instructors/{instructor_id}         → Edit instructor details
  DELETE /instructors/{instructor_id}         → Deactivate (default) or permanently
                                                 delete (force=true) an instructor
"""

from fastapi import APIRouter, Depends, Query, Response, Form, UploadFile, File
from typing import Optional
from pydantic import EmailStr
from app.core.cloudinary_service import upload_image

from app.models.instructor_model import (
    InstructorListResponse,
    InstructorProfile,
    InstructorUpdate,
)
from app.services.instructor_service import InstructorService
from app.api.v1.dependencies import get_current_active_user

router = APIRouter()


# ─────────────────────────────────────────────────────────────────
# EXPORT — must be defined BEFORE
# ─────────────────────────────────────────────────────────────────

@router.get("/export/excel")
async def export_instructors_to_excel(
    search: Optional[str] = None,
    current_user=Depends(get_current_active_user)
):
    """
    Export full instructor list to Excel (.xlsx).

    **UI Reference:** Download button on Instructors page (Image 5)
    **Access:** Admin / Manager only
    """
    excel_file = await InstructorService.export_instructors_to_excel(
        caller_id=current_user.id,
        search=search
    )
    return Response(
        content=excel_file.read(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=instructors_export.xlsx"}
    )


# ─────────────────────────────────────────────────────────────────
# GET ALL INSTRUCTORS
# ─────────────────────────────────────────────────────────────────

@router.get("", response_model=InstructorListResponse)
async def get_all_instructors(
    search: Optional[str] = None,
    page: int = 1,
    pageSize: int = 10,
    current_user=Depends(get_current_active_user)
):
    """
    Returns paginated instructor list.

    **Columns:** Name/Speciality, Total Classes, Favoured By, Total Students, Joined
    **Features:** Search by instructor name
    **Access:** Admin / Manager only
    """
    return await InstructorService.get_all_instructors(
        caller_id=current_user.id,
        search=search,
        page=page,
        page_size=pageSize
    )


# ─────────────────────────────────────────────────────────────────
# GET INSTRUCTOR PROFILE BY ID
# ─────────────────────────────────────────────────────────────────

@router.get("/{instructor_id}", response_model=InstructorProfile)
async def get_instructor_profile(
    instructor_id: str,
    current_user=Depends(get_current_active_user)
):
    """
    Full instructor profile (eye icon on instructor row).

    **Returns:**
    - Name, phone, email, speciality, bio
    - Profile image + background image
    - Upcoming classes (next 10, ordered by date)

    **UI Reference:** Instructor Details side panel (Image 7)
    **Access:** Any authenticated user
    """
    return await InstructorService.get_instructor_profile(instructor_id)


# ─────────────────────────────────────────────────────────────────
# UPDATE INSTRUCTOR
# ─────────────────────────────────────────────────────────────────

@router.patch("/{instructor_id}", response_model=InstructorProfile)
async def update_instructor(
instructor_id: str,
    
    # ── Text fields ──────────────────────────────────────
    name: Optional[str] = Form(None),
    phone: Optional[str] = Form(None),
    email: Optional[EmailStr] = Form(None),
    speciality: Optional[str] = Form(None),
    bio: Optional[str] = Form(None),
    
    # ── Image files ──────────────────────────────────────
    avatar: UploadFile = File(default=None),
    backgroundImage: UploadFile = File(default=None),
    
    current_user=Depends(get_current_active_user)
):
    # Avatar upload
    avatar_url = None
    if avatar and avatar.filename:
        avatar_url = await upload_image(avatar, folder="instructors/avatars")
    
    # Background image upload
    background_url = None
    if backgroundImage and backgroundImage.filename:
        background_url = await upload_image(backgroundImage, folder="instructors/backgrounds")
    
    update_obj = InstructorUpdate(
        name=name,
        phone=phone,
        email=email,
        speciality=speciality,
        bio=bio,
        avatar=avatar_url,
        backgroundImage=background_url,
    )
    
    return await InstructorService.update_instructor(
        caller_id=current_user.id,
        instructor_id=instructor_id,
        data=update_obj
    )


# ─────────────────────────────────────────────────────────────────
# DELETE INSTRUCTOR (Admin only)
# ─────────────────────────────────────────────────────────────────

@router.delete("/{instructor_id}")
async def delete_instructor(
    instructor_id: str,
    force: bool = Query(
        False,
        description=(
            "false (default) = deactivate the instructor (safe, always works, "
            "preserves their class/course history). "
            "true = permanently delete — only allowed when they have zero "
            "classes and zero courses on record; otherwise returns 409."
        ),
    ),
    current_user=Depends(get_current_active_user)
):
    """
    Deactivate, or permanently delete, an instructor account.

    **Default behaviour (`force=false`):** deactivates the instructor
    (`isActive=False`). They disappear from active instructor listings and
    can no longer log in. Every class/course they've ever taught — and
    every other member's booking/review/wishlist tied to those — is left
    completely intact. Safe to call any time; idempotent if already
    deactivated.

    **`force=true`:** permanently deletes the account. Only succeeds when
    the instructor has **zero** classes and **zero** courses on record —
    otherwise responds `409 Conflict` explaining how many exist, since
    Postgres itself won't allow deleting a user who is still referenced as
    an instructor anywhere. Use the default (deactivate) for any instructor
    with teaching history.

    **UI Reference:** Delete action on Instructors page (Image 1)
    **Access:** Admin only
    """
    return await InstructorService.delete_instructor(
        caller_id=current_user.id,
        instructor_id=instructor_id,
        force=force,
    )