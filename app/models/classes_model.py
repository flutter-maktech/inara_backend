"""
app/models/classes_model.py
============================
Pydantic models for the standalone Classes entity.
Classes are completely independent from Courses.
"""

from pydantic import BaseModel, Field, field_validator
from typing import Optional, List, Literal
from datetime import datetime
from prisma.enums import ClassStatus, BookingStatus


# ============================================
# CLASS MODELS
# ============================================

class ClassBase(BaseModel):
    """Shared fields that mirror the Classes DB schema."""
    title: str
    description: Optional[str] = None
    difficulty: str
    gender: str
    price: float = 0.0
    isFree: bool = False
    duration: str                           # e.g. "8:00 AM to 10:00 AM"
    scheduledAt: datetime
    maxParticipants: int
    availableSeat: Optional[int] = 0
    location: str
    locationMapLink: Optional[str] = None
    latitude: Optional[float] = Field(None, description="GPS latitude coordinate")
    longitude: Optional[float] = Field(None, description="GPS longitude coordinate")
    phone: str
    imageUrl: str


class ClassCreate(ClassBase):
    """Payload for creating a new class."""
    instructorId: str
    order: int = 0
    # Cancellation fields intentionally excluded from creation


class ClassUpdate(BaseModel):
    """All fields optional — PATCH semantics."""
    title: Optional[str] = None
    description: Optional[str] = None
    status: Optional[ClassStatus] = None
    difficulty: Optional[str] = None
    gender: Optional[str] = None
    price: Optional[float] = None
    isFree: Optional[bool] = None
    duration: Optional[str] = None
    scheduledAt: Optional[datetime] = None
    maxParticipants: Optional[int] = None
    availableSeat: Optional[int] = None
    location: Optional[str] = None
    locationMapLink: Optional[str] = None
    latitude: Optional[float] = Field(None, description="GPS latitude coordinate")
    longitude: Optional[float] = Field(None, description="GPS longitude coordinate")
    phone: Optional[str] = None
    imageUrl: Optional[str] = None
    instructorId: Optional[str] = None
    order: Optional[int] = None


class ClassResponse(ClassBase):
    """Full class response returned by the API."""
    id: str
    status: ClassStatus
    instructorId: str
    order: int
    cancelledAt: Optional[datetime] = None
    cancellationReason: Optional[str] = None
    createdAt: datetime
    updatedAt: datetime

    # Computed / joined
    bookedSeats: Optional[int] = 0
    instructor: Optional[dict] = None

    # ── User-personalised pricing ─────────────────────────────────────────
    # effectivePrice: the price this specific authenticated user sees/pays
    # if they pay with Wallet or Gateway.
    #   • 0.0  → class is free (price=0 / isFree=True), OR covered by an
    #             active MEMBERSHIP (membership coverage auto-applies).
    #   • original price → covered by an active PACKAGE. Package coverage
    #             does NOT discount the displayed price — spending a package
    #             session is an active choice made at booking time, not an
    #             automatic discount. See availablePackages below.
    #   • None → caller is Admin/Manager/Instructor — raw price is shown,
    #             no personal coverage check is performed.
    #
    # coverageType: explains the relationship between effectivePrice and price.
    #   • "package"    → one or more owned packages cover this class. Price is
    #                     UNCHANGED; see availablePackages for the picker.
    #   • "membership" → covered by an active purchased membership (price → 0)
    #   • "free"       → class.price == 0 or class.isFree == True
    #   • "none"       → no coverage, full price applies
    #   • None         → not computed (admin / non-user caller)
    effectivePrice: Optional[float] = None
    coverageType: Optional[str] = None   # "package" | "membership" | "free" | "none" | None

    # availablePackages: populated only when coverageType == "package". Each
    # entry is {membershipId, packageName, sessionsRemaining, expiresAt} —
    # pass membershipId back as `membership_id` on POST /payments/bookings/pay
    # with payment_method="PACKAGE" to spend a session from that instance.
    availablePackages: Optional[List[dict]] = []

    class Config:
        from_attributes = True


class ClassWithBookings(ClassResponse):
    """Class response enriched with its bookings list."""
    bookings: List[dict] = []


class ClassListResponse(BaseModel):
    """Paginated class list."""
    classes: List[ClassResponse]
    total: int
    page: int
    pageSize: int
    totalPages: int


# ============================================
# NOTIFICATION & CANCELLATION
# ============================================

class SendNotification(BaseModel):
    """
    Payload for sending a push notification to all confirmed participants
    of a class.

    Channel selection (pick one or use sendViaBoth):
      • sendViaApp       — In-app notification (DB record, mobile push).
      • sendViaWhatsApp  — WhatsApp Business Cloud API message.
      • sendViaBoth      — Both channels simultaneously (overrides the above).

    Optional fields:
      • title            — Custom notification title shown in the notification
                           centre. Defaults to "Update: <class title>" when omitted.
      • notificationType — Severity / icon hint for the mobile client.
                           One of: INFO | REMINDER | WARNING | URGENT.
                           Defaults to INFO.
    """
    classId: str
    message: str
    sendViaApp: bool = True
    sendViaWhatsApp: bool = False
    sendViaBoth: bool = False

    # Optional enrichment — defaults are applied in the service layer
    title: Optional[str] = Field(
        None,
        description='Custom notification title. Defaults to "Update: <class title>".',
    )
    notificationType: Literal["INFO", "REMINDER", "WARNING", "URGENT"] = Field(
        "INFO",
        description="Notification severity / icon hint for the mobile client.",
    )


class CancelClass(BaseModel):
    classId: str
    reason: str
    notifyParticipants: bool = True
    refundPolicy: Optional[str] = "full"   # full | partial | none


# ============================================
# SEARCH & FILTER
# ============================================

class ClassSearchParams(BaseModel):
    """Query parameters for listing / filtering classes."""
    search: Optional[str] = None
    instructorId: Optional[str] = None
    difficulty: Optional[str] = None
    gender: Optional[str] = None
    status: Optional[ClassStatus] = None
    dateFrom: Optional[datetime] = None
    dateTo: Optional[datetime] = None

    # Human-friendly date filter: "2-24-2026", "02/24/2026", "2026-02-24"
    scheduledAt: Optional[str] = Field(
        None,
        description='Filter by exact date. Accepts: "2-24-2026", "02/24/2026", "2026-02-24"'
    )

    @field_validator("scheduledAt", mode="before")
    @classmethod
    def parse_scheduled_date(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        v = v.strip()
        FORMATS = ["%m-%d-%Y", "%m/%d/%Y", "%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y"]
        for fmt in FORMATS:
            try:
                datetime.strptime(v, fmt)
                return v
            except ValueError:
                continue
        raise ValueError(
            f'Invalid date "{v}". Use a format like "2-24-2026", "02/24/2026", or "2026-02-24".'
        )

    page: int = Field(1, ge=1)
    pageSize: int = Field(10, ge=1, le=100)
    sortBy: str = "scheduledAt"
    sortOrder: str = "asc"