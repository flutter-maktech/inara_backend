"""
app/models/user.py
==================
Pydantic schemas for user profile endpoints.
"""

import re
from pydantic import BaseModel, EmailStr, field_validator
from typing import Optional
from datetime import datetime


def _validate_dob(v: Optional[str]) -> Optional[str]:
    """Shared validator for the DD-MM date-of-birth field."""
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


class UserBase(BaseModel):
    email: EmailStr
    name: Optional[str] = None
    bio: Optional[str] = None
    avatar: Optional[str] = None


class UserCreate(UserBase):
    password: str
    role: str = "USER"
    phone: Optional[str] = None
    gender: Optional[str] = None
    # Day and month of birth only — year intentionally excluded for privacy.
    # Format: "DD-MM" e.g. "25-12" for 25th December.
    dateOfBirth: Optional[str] = None

    @field_validator("dateOfBirth")
    @classmethod
    def validate_date_of_birth(cls, v: Optional[str]) -> Optional[str]:
        return _validate_dob(v)


class UserUpdate(BaseModel):
    name: Optional[str] = None
    bio: Optional[str] = None
    avatar: Optional[str] = None
    phone: Optional[str] = None
    gender: Optional[str] = None
    # Users may update their birthday (day + month only).
    dateOfBirth: Optional[str] = None

    @field_validator("dateOfBirth")
    @classmethod
    def validate_date_of_birth(cls, v: Optional[str]) -> Optional[str]:
        return _validate_dob(v)


class UserResponse(UserBase):
    id: str
    role: str
    isActive: bool
    isVerified: bool
    phone: Optional[str] = None
    gender: Optional[str] = None
    avatar: Optional[str] = None
    bio: Optional[str] = None
    # DD-MM string or None — returned as-is from the database.
    dateOfBirth: Optional[str] = None
    createdAt: datetime

    class Config:
        from_attributes = True