"""
app/models/member_model.py
==========================
Pydantic schemas for the Members section.
"""

import re
from pydantic import BaseModel, EmailStr, Field, field_validator
from typing import Literal, Optional, List
from datetime import datetime


# ─────────────────────────────────────────────────────────────────────────────
# Shared DOB validator (DD-MM, year intentionally omitted for privacy)
# ─────────────────────────────────────────────────────────────────────────────

def _validate_dob(v: Optional[str]) -> Optional[str]:
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
# MEMBER STATS (Summary counts at top of UI)
# ============================================

class MemberStats(BaseModel):
    """Aggregate stats cards shown at top of Members page"""
    totalMembers: int
    totalMaleMembers: int
    totalFemaleMembers: int


# ============================================
# MEMBER LIST (Brief details for table row)
# ============================================

class MembershipBrief(BaseModel):
    """Compact membership/package info shown in the Members table column"""
    name: str
    status: str


class MemberBrief(BaseModel):
    """
    One row in the Members table.
    Mirrors exactly what the UI shows:
      - Avatar + name + email
      - Membership/Packages (active membership or package name)
      - Bookings (X Classes, Y courses)
      - Joined date
      - Last Booking date
      - Gender
      - Date of Birth (DD-MM, year omitted for privacy)
    """
    id: str
    name: str
    email: str
    avatar: Optional[str] = None
    gender: Optional[str] = None
    phone: Optional[str] = None
    # Day and month of birth only — year intentionally excluded for privacy.
    # Format: "DD-MM" e.g. "25-12" for 25th December. None if not provided.
    dateOfBirth: Optional[str] = None
    joinedAt: datetime                        # createdAt on User
    lastBookingAt: Optional[datetime] = None  # most recent booking date
    totalClassBookings: int = 0
    totalCourseBookings: int = 0              # courses via class bookings
    activeMembership: Optional[MembershipBrief] = None

    class Config:
        from_attributes = True


class MemberListResponse(BaseModel):
    """Paginated member list"""
    members: List[MemberBrief]
    total: int
    page: int
    pageSize: int
    totalPages: int


# ============================================
# UPDATE MEMBER PROFILE (Admin/Manager — PATCH)
# ============================================

class UpdateMemberRequest(BaseModel):
    """
    Fields an Admin or Manager can update on a member's profile.
    All fields are optional — send only what needs to change.
    """
    name: Optional[str] = None
    phone: Optional[str] = None
    gender: Optional[str] = None
    avatar: Optional[str] = None
    bio: Optional[str] = None
    # Day and month of birth only — year intentionally excluded for privacy.
    # Format: "DD-MM" e.g. "25-12" for 25th December.
    dateOfBirth: Optional[str] = None

    @field_validator("dateOfBirth")
    @classmethod
    def validate_date_of_birth(cls, v: Optional[str]) -> Optional[str]:
        return _validate_dob(v)


class UpdateMemberResponse(BaseModel):
    """
    Response returned after a successful PATCH /members/{member_id}.

    Returns a rich, developer-friendly payload so the frontend can
    immediately reflect the updated state without a follow-up GET.

    Fields:
      - message        : Human-readable success confirmation.
      - memberId       : ID of the updated member.
      - updatedFields  : List of field names that were actually written
                         (only fields sent in the request body).
      - member         : Full current state of the member's profile after
                         the update — id, name, email, phone, gender,
                         avatar, bio, dateOfBirth, joinedAt.
    """
    message: str
    memberId: str
    updatedFields: List[str]
    member: "UpdatedMemberSnapshot"


class UpdatedMemberSnapshot(BaseModel):
    """
    Compact snapshot of a member's profile returned inside UpdateMemberResponse.
    Contains every field that UpdateMemberRequest can touch, plus identity
    fields (id, email, joinedAt) so the client can unambiguously bind the
    response to the correct record.
    """
    id: str
    email: str
    name: Optional[str] = None
    phone: Optional[str] = None
    gender: Optional[str] = None
    avatar: Optional[str] = None
    bio: Optional[str] = None
    # DD-MM string or None — year intentionally omitted for privacy.
    dateOfBirth: Optional[str] = None
    joinedAt: datetime

    class Config:
        from_attributes = True


# Resolve the forward reference so Pydantic can build UpdateMemberResponse
UpdateMemberResponse.model_rebuild()


# ============================================
# MEMBER PROFILE (Detailed view — eye icon)
# ============================================

class ActiveMembershipDetail(BaseModel):
    """Active membership card in member profile"""
    id: str
    name: str
    status: str
    expiresAt: Optional[datetime] = None
    totalClassBookings: int = 0


class ClassBookingRow(BaseModel):
    """One row in 'List of Classes' table on member profile"""
    classId: str
    className: str
    bookingsCount: int
    lastBookedAt: Optional[datetime] = None


class CourseBookingRow(BaseModel):
    """One row in 'List of Courses' table on member profile"""
    courseId: str
    courseName: str
    bookingsCount: int
    lastBookedAt: Optional[datetime] = None


class PurchaseHistoryRow(BaseModel):
    """One row in 'Purchase History' table on member profile"""
    orderId: str
    orderNumber: str
    productName: str
    quantity: int
    price: float
    orderDate: datetime


class MemberProfile(BaseModel):
    """
    Full member profile returned by GET /members/{member_id}.
    Maps to the Member Profile UI screen.
    """
    id: str
    name: str
    email: str
    phone: Optional[str] = None
    avatar: Optional[str] = None
    gender: Optional[str] = None
    bio: Optional[str] = None
    dateOfBirth: Optional[str] = None  # DD-MM string
    joinedAt: datetime
    lastBookingAt: Optional[datetime] = None

    # Active membership card
    activeMembership: Optional[ActiveMembershipDetail] = None

    # Favourite class / instructor (derived from most-booked)
    favouriteClass: Optional[str] = None
    favouriteInstructor: Optional[str] = None

    # Paginated sub-tables (first page returned inline)
    classesList: List[ClassBookingRow] = []
    classesTotal: int = 0

    coursesList: List[CourseBookingRow] = []
    coursesTotal: int = 0

    purchaseHistory: List[PurchaseHistoryRow] = []
    purchasesTotal: int = 0


# ============================================
# NOTIFICATION
# ============================================

class SendNotificationRequest(BaseModel):
    """
    Admin sends a notification to all members (or a selected subset).

    Channel selection:
      • "app"       — In-app notification (DB record, mobile push).
      • "whatsapp"  — WhatsApp Business Cloud API message.
      • "both"      — Both channels simultaneously.

    Optional fields:
      • title            — Custom notification title shown in the notification
                           centre. Defaults to "Notification from INARA" when omitted.
      • notificationType — Severity / icon hint for the mobile client.
                           One of: INFO | REMINDER | WARNING | URGENT.
                           Defaults to INFO.
      • memberIds        — Restrict delivery to specific members.
                           Empty list (default) → send to ALL active members.
    """
    message: str
    channel: str = Field(
        default="both",
        description="Delivery channel: 'app', 'whatsapp', or 'both'",
    )
    # Optional: if empty list → send to ALL members
    memberIds: List[str] = []

    # Optional enrichment — defaults are applied in the service layer
    title: Optional[str] = Field(
        None,
        description="Custom notification title. Defaults to 'Notification from INARA'.",
    )
    notificationType: Literal["INFO", "REMINDER", "WARNING", "URGENT"] = Field(
        "INFO",
        description="Notification severity / icon hint for the mobile client.",
    )


class SendNotificationResponse(BaseModel):
    message: str
    sentTo: int           # count of recipients targeted
    channel: str
    sentViaApp: int = 0
    sentViaWhatsApp: int = 0
    skippedApp: int = 0
    skippedWhatsApp: int = 0
    failed: int = 0


# ============================================
# MEMBER EXPORT PARAMS
# ============================================

class MemberExportParams(BaseModel):
    """Filter params used when exporting sub-lists from profile"""
    memberId: str
    page: int = Field(1, ge=1)
    pageSize: int = Field(10, ge=1, le=100)


# ============================================
# MEMBER INVITATION MODELS
# ============================================

class InviteMemberRequest(BaseModel):
    """
    Admin invites a new user (USER role) to join the app as a member.
    The invited person will receive an email with a link to:
      1. Complete their profile (name, phone, password).
      2. Choose and purchase a membership plan.
    """
    email: EmailStr
    # Optional: pre-fill the invitee's display name in the email
    name: Optional[str] = None


class MemberInvitationResponse(BaseModel):
    """Single member invitation record."""
    id: str
    email: str
    name: Optional[str] = None
    status: str
    inviteToken: str
    invitedBy: str
    invitedByName: Optional[str] = None
    expiresAt: datetime
    acceptedAt: Optional[datetime] = None
    createdAt: datetime

    class Config:
        from_attributes = True


class MemberInvitationListResponse(BaseModel):
    """Paginated list of member invitations."""
    invitations: List[MemberInvitationResponse]
    total: int
    page: int
    pageSize: int
    totalPages: int


class AcceptMemberInvitationRequest(BaseModel):
    """
    Payload sent when the invitee accepts their member invitation.

    Revised workflow (Invite → Accept → Complete Details → Done):
      - inviteToken  : secure token from the invitation email link
      - name         : invitee's full name
      - phone        : phone number (optional)
      - gender       : "Male" | "Female" | "Prefer not to say" (optional)
      - password     : chosen password (min 8 chars)

    NOTE: Membership selection has been intentionally removed from this flow.
    Members are directed to the app to discover and purchase a membership there.
    """
    inviteToken: str
    name: str = Field(..., min_length=1, description="Full name")
    phone: Optional[str] = None
    gender: Optional[str] = Field(
        None,
        description="Gender: 'Male', 'Female', or 'Prefer not to say'",
    )
    password: str = Field(..., min_length=8, description="Password (min 8 characters)")


class AcceptMemberInvitationResponse(BaseModel):
    """
    Response after a successful member invitation acceptance.

    Returns app store links and a success message so the client (or the
    HTML acceptance page) can direct the new member straight to the app.
    """
    message: str
    userId: str
    email: str
    name: str
    iosAppUrl: str
    androidAppUrl: str