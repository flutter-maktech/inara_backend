"""
app/models/courses_model.py
============================
Pydantic models for the standalone Course entity.
Courses are completely independent from Classes.
"""

from pydantic import BaseModel, Field
from typing import Optional, List, Literal
from datetime import datetime
from prisma.enums import CourseStatus, BookingStatus


# ============================================
# COURSE MODELS
# ============================================

class CourseBase(BaseModel):
    """Shared fields that mirror the Course DB schema."""
    title: str
    description: str
    difficulty: str
    gender: str
    price: float = 0.0
    isFree: bool = False
    duration: str                           # e.g. "8:00 AM to 10:00 AM"
    scheduledAt: datetime                   # Start date/time of the course
    endDate: Optional[datetime] = None      # End date/time of the course (Issue #3: new field)
    maxParticipants: int
    availableSeat: Optional[int] = 0
    location: str
    locationMapLink: Optional[str] = None
    latitude: Optional[float] = Field(None, description="GPS latitude coordinate")
    longitude: Optional[float] = Field(None, description="GPS longitude coordinate")
    phone: str
    imageUrl: str


class CourseCreate(CourseBase):
    """Payload for creating a new course."""
    instructorId: str
    order: int = 0


class CourseUpdate(BaseModel):
    """All fields optional — PATCH semantics."""
    title: Optional[str] = None
    description: Optional[str] = None
    status: Optional[CourseStatus] = None
    difficulty: Optional[str] = None
    gender: Optional[str] = None
    price: Optional[float] = None
    isFree: Optional[bool] = None
    duration: Optional[str] = None
    scheduledAt: Optional[datetime] = None     # Start date/time
    endDate: Optional[datetime] = None         # End date/time (Issue #3: new field)
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


class CourseResponse(CourseBase):
    """Full course response returned by the API."""
    id: str
    status: CourseStatus
    instructorId: str
    order: int
    cancelledAt: Optional[datetime] = None
    cancellationReason: Optional[str] = None
    createdAt: datetime
    updatedAt: datetime

    # Computed / joined
    bookedSeats: Optional[int] = 0
    instructor: Optional[dict] = None

    class Config:
        from_attributes = True


class CourseWithDetails(CourseResponse):
    """Course response enriched with bookings list."""
    bookings: List[dict] = []


class CourseListResponse(BaseModel):
    """Paginated course list."""
    courses: List[CourseResponse]
    total: int
    page: int
    pageSize: int
    totalPages: int


# ============================================
# NOTIFICATION & CANCELLATION
# ============================================

class SendCourseNotification(BaseModel):
    """
    Payload for sending a push notification to all confirmed enrollments
    of a course.

    Channel selection (pick one or use sendViaBoth):
      • sendViaApp       — In-app notification (DB record, mobile push).
      • sendViaWhatsApp  — WhatsApp Business Cloud API message.
      • sendViaBoth      — Both channels simultaneously (overrides the above).

    Optional fields:
      • title            — Custom notification title shown in the notification
                           centre. Defaults to "Update: <course title>" when omitted.
      • notificationType — Severity / icon hint for the mobile client.
                           One of: INFO | REMINDER | WARNING | URGENT.
                           Defaults to INFO.
    """
    courseId: str
    message: str
    sendViaApp: bool = True
    sendViaWhatsApp: bool = False
    sendViaBoth: bool = False

    # Optional enrichment — defaults are applied in the service layer
    title: Optional[str] = Field(
        None,
        description='Custom notification title. Defaults to "Update: <course title>".',
    )
    notificationType: Literal["INFO", "REMINDER", "WARNING", "URGENT"] = Field(
        "INFO",
        description="Notification severity / icon hint for the mobile client.",
    )


class CancelCourse(BaseModel):
    courseId: str
    reason: str
    notifyParticipants: bool = True
    refundPolicy: Optional[str] = "full"   # full | partial | none


# ============================================
# SEARCH & FILTER
# ============================================

class CourseSearchParams(BaseModel):
    """Query parameters for listing / filtering courses."""
    search: Optional[str] = None
    instructorId: Optional[str] = None
    difficulty: Optional[str] = None
    gender: Optional[str] = None
    minPrice: Optional[float] = None
    maxPrice: Optional[float] = None
    page: int = Field(1, ge=1)
    pageSize: int = Field(10, ge=1, le=100)
    sortBy: str = "scheduledAt"
    sortOrder: str = "asc"


# ============================================
# ENROLLMENT (booking) BRIEF
# ============================================

class BookingBrief(BaseModel):
    """Brief booking info returned inside CourseWithDetails."""
    id: str
    userId: str
    userName: str
    userEmail: str
    userPhone: Optional[str] = None
    bookedAt: datetime
    status: str

    class Config:
        from_attributes = True