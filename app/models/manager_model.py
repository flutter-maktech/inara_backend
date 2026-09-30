"""
app/models/manager_model.py
============================
Pydantic schemas for the Managers section — Admin-only oversight of
MANAGER-role accounts.

Managers = Users with role == MANAGER.

Structurally this mirrors member_model.py / instructor_model.py (the
"staff/profile" family of models in this codebase, which use camelCase
response keys), rather than the newer snake_case "booking analytics"
family used by attendance_model.py / dashboard_model.py.

Note on scope: unlike Instructors, Managers carry no `speciality` field
(that's instructor-domain terminology) and no repurposed "background
image" — this stays to the plain profile fields that actually exist and
matter for a Manager account, keeping the surface simple.
"""

import re
from pydantic import BaseModel, EmailStr, Field, field_validator
from typing import Optional, List
from datetime import datetime


def _validate_dob(v: Optional[str]) -> Optional[str]:
    """Shared DOB validator (DD-MM, year intentionally omitted for privacy)."""
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


# ============================================
# MANAGER LIST (one row per manager in the table)
# ============================================

class ManagerBrief(BaseModel):
    """One row in the Managers table."""
    id: str
    name: str
    email: str
    phone: Optional[str] = None
    avatar: Optional[str] = None
    gender: Optional[str] = None
    bio: Optional[str] = None
    # Day and month of birth only — year intentionally excluded for privacy.
    dateOfBirth: Optional[str] = None
    isActive: bool
    joinedAt: datetime  # createdAt on User

    class Config:
        from_attributes = True


class ManagerListResponse(BaseModel):
    """Paginated manager list."""
    managers: List[ManagerBrief]
    total: int
    page: int
    pageSize: int
    totalPages: int


# ============================================
# MANAGER PROFILE (GET by id)
# ============================================

class ManagerProfile(BaseModel):
    """Full manager profile returned by GET /managers/{manager_id}."""
    id: str
    name: str
    email: str
    phone: Optional[str] = None
    avatar: Optional[str] = None
    gender: Optional[str] = None
    bio: Optional[str] = None
    dateOfBirth: Optional[str] = None
    isActive: bool
    isVerified: bool
    joinedAt: datetime

    class Config:
        from_attributes = True


# ============================================
# UPDATE MANAGER (Admin only — PATCH)
# ============================================

class UpdateManagerRequest(BaseModel):
    """
    Fields an Admin can update on a manager's profile.
    All fields are optional — send only what needs to change.
    """
    name: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[EmailStr] = None
    gender: Optional[str] = None
    avatar: Optional[str] = None
    bio: Optional[str] = None
    # Day and month of birth only — year intentionally excluded for privacy.
    dateOfBirth: Optional[str] = None

    @field_validator("dateOfBirth")
    @classmethod
    def validate_date_of_birth(cls, v: Optional[str]) -> Optional[str]:
        return _validate_dob(v)


class UpdatedManagerSnapshot(BaseModel):
    """Compact post-update snapshot, returned inline so no follow-up GET is needed."""
    id: str
    email: str
    name: Optional[str] = None
    phone: Optional[str] = None
    gender: Optional[str] = None
    avatar: Optional[str] = None
    bio: Optional[str] = None
    dateOfBirth: Optional[str] = None
    isActive: bool
    joinedAt: datetime

    class Config:
        from_attributes = True


class UpdateManagerResponse(BaseModel):
    """Response returned after a successful PATCH /managers/{manager_id}."""
    message: str
    managerId: str
    updatedFields: List[str]
    manager: "UpdatedManagerSnapshot"


# Resolve the forward reference so Pydantic can build UpdateManagerResponse
UpdateManagerResponse.model_rebuild()