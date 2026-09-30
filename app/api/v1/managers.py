"""
Managers API Router
====================
Managers = Users with role == MANAGER.

Admin-only oversight panel for Manager accounts — Managers themselves
continue to self-service their own profile via the existing, role-agnostic
GET /api/v1/users/me and PATCH /api/v1/users/update endpoints; this router
is exclusively how an ADMIN views, edits, exports, and deletes *other*
Manager accounts. Managers are created via the existing team-invitation
flow (POST /api/v1/role-matrix/invitations with role=MANAGER) — there is
no separate "create manager" endpoint here.

Endpoints:
  GET    /managers                    → Paginated manager list + search
  GET    /managers/export/excel       → Export manager list to Excel
  GET    /managers/{manager_id}       → Full manager profile
  PATCH  /managers/{manager_id}       → Edit manager profile
  DELETE /managers/{manager_id}       → Permanently delete a manager account

Access: Admin only, for every endpoint in this router.
"""

from fastapi import APIRouter, Depends, File, Form, Response, UploadFile, status
from typing import Optional
from pydantic import EmailStr

from app.core.cloudinary_service import upload_image
from app.models.manager_model import (
    ManagerListResponse,
    ManagerProfile,
    UpdateManagerRequest,
    UpdateManagerResponse,
)
from app.services.manager_service import ManagerService
from app.api.v1.dependencies import get_current_active_user

router = APIRouter()


# ─────────────────────────────────────────────────────────────────
# EXPORT — must be defined BEFORE /{manager_id} to avoid routing conflict
# ─────────────────────────────────────────────────────────────────

@router.get("/export/excel")
async def export_managers_to_excel(
    search: Optional[str] = None,
    current_user=Depends(get_current_active_user)
):
    """
    Export full manager list to Excel (.xlsx).

    **UI Reference:** Download button on Managers page
    **Access:** Admin only
    """
    excel_file = await ManagerService.export_managers_to_excel(
        caller_id=current_user.id,
        search=search
    )
    return Response(
        content=excel_file.read(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=managers_export.xlsx"}
    )


# ─────────────────────────────────────────────────────────────────
# GET ALL MANAGERS
# ─────────────────────────────────────────────────────────────────

@router.get("", response_model=ManagerListResponse)
async def get_all_managers(
    search: Optional[str] = None,
    page: int = 1,
    pageSize: int = 10,
    current_user=Depends(get_current_active_user)
):
    """
    Returns paginated manager list.

    **Features:** Search by manager name or email
    **Access:** Admin only
    """
    return await ManagerService.get_all_managers(
        caller_id=current_user.id,
        search=search,
        page=page,
        page_size=pageSize
    )


# ─────────────────────────────────────────────────────────────────
# GET MANAGER PROFILE BY ID
# ─────────────────────────────────────────────────────────────────

@router.get("/{manager_id}", response_model=ManagerProfile)
async def get_manager_profile(
    manager_id: str,
    current_user=Depends(get_current_active_user)
):
    """
    Full manager profile (eye icon on manager row).

    **Access:** Admin only
    """
    return await ManagerService.get_manager_profile(
        caller_id=current_user.id,
        manager_id=manager_id
    )


# ─────────────────────────────────────────────────────────────────
# UPDATE MANAGER (Admin only)
# ─────────────────────────────────────────────────────────────────

@router.patch("/{manager_id}", response_model=UpdateManagerResponse, status_code=status.HTTP_200_OK)
async def update_manager(
    manager_id: str,
    name: Optional[str] = Form(default=None, description="Display name"),
    phone: Optional[str] = Form(default=None, description="Phone number"),
    email: Optional[EmailStr] = Form(default=None, description="Login email"),
    gender: Optional[str] = Form(default=None, description="Gender"),
    bio: Optional[str] = Form(default=None, description="Short bio / about text"),
    dateOfBirth: Optional[str] = Form(
        default=None,
        description="Birthday in DD-MM format (day + month only, e.g. '25-12')"
    ),
    avatar: UploadFile = File(default=None),
    current_user=Depends(get_current_active_user)
):
    """
    Edit a manager's profile data.

    **Content-Type:** `multipart/form-data`

    **Form fields (all optional — send only what needs to change):**
    | Field         | Type   | Description                                |
    |---------------|--------|---------------------------------------------|
    | `name`        | string | Display name                                |
    | `phone`       | string | Phone number                                |
    | `email`       | string | Login email (must be unique)                |
    | `gender`      | string | Gender                                      |
    | `bio`         | string | Short bio / about text                      |
    | `dateOfBirth` | string | Birthday in DD-MM format (e.g. `"25-12"`)   |
    | `avatar`      | file   | Avatar image — JPEG/PNG/WEBP/GIF, max 5 MB  |

    **Behaviour:** PATCH semantics — only supplied (non-null) fields are
    written to DB. Returns the full updated snapshot — no follow-up GET
    required.

    **Access:** Admin only
    """
    avatar_url: Optional[str] = None
    if avatar and avatar.filename:
        avatar_url = await upload_image(avatar, folder="managers/avatars")

    data = UpdateManagerRequest(
        name=name,
        phone=phone,
        email=email,
        gender=gender,
        avatar=avatar_url,
        bio=bio,
        dateOfBirth=dateOfBirth,
    )

    return await ManagerService.update_manager(
        caller_id=current_user.id,
        manager_id=manager_id,
        data=data
    )


# ─────────────────────────────────────────────────────────────────
# DELETE MANAGER (Admin only)
# ─────────────────────────────────────────────────────────────────

@router.delete("/{manager_id}", status_code=status.HTTP_200_OK)
async def delete_manager(
    manager_id: str,
    current_user=Depends(get_current_active_user)
):
    """
    Permanently delete a manager's account.

    ⚠️ **This action is irreversible.** Any bookings, wallet, orders, and
    other personal data the manager accumulated as an app user are also
    removed.

    **Access:** Admin only
    """
    return await ManagerService.delete_manager(
        caller_id=current_user.id,
        manager_id=manager_id
    )