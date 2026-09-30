from pydantic import BaseModel, EmailStr
from typing import Optional, List
from datetime import datetime


# ============================================
# SHARED BRIEF — used for both upcoming classes & courses
# ============================================

class UpcomingClassBrief(BaseModel):
    """Compact card for an upcoming Class in the Instructor Details panel."""
    id: str
    title: str
    difficulty: Optional[str] = None
    scheduledAt: Optional[datetime] = None
    duration: Optional[str] = None
    price: float = 0.0
    gender: Optional[str] = None
    availableSpots: int = 0
    totalSpots: Optional[int] = None
    instructorName: Optional[str] = None
    instructorAvatar: Optional[str] = None


class UpcomingCourseBrief(BaseModel):
    """Compact card for an upcoming Course in the Instructor Details panel."""
    id: str
    title: str
    difficulty: Optional[str] = None
    scheduledAt: Optional[datetime] = None
    duration: Optional[str] = None
    price: float = 0.0
    gender: Optional[str] = None
    availableSpots: int = 0
    totalSpots: Optional[int] = None
    instructorName: Optional[str] = None
    instructorAvatar: Optional[str] = None


# ============================================
# INSTRUCTOR LIST (one row per instructor in the table)
# ============================================

class InstructorBrief(BaseModel):
    """
    Full detail row for the Instructors table.
    Columns: Name/Speciality · Total Classes · Total Courses ·
             Favoured By · Total Students · Joined · ACTIONS
    """
    id: str
    name: str
    email: str
    phone: Optional[str] = None
    avatar: Optional[str] = None
    backgroundImage: Optional[str] = None
    speciality: Optional[str] = None
    bio: Optional[str] = None
    totalClasses: int = 0       # standalone Classes entity count
    totalCourses: int = 0       # standalone Courses entity count
    favouredBy: int = 0         # unique users who wishlisted any class or course
    totalStudents: int = 0      # unique users who booked any class or course
    joinedAt: datetime

    class Config:
        from_attributes = True


class InstructorListResponse(BaseModel):
    """Paginated instructor list."""
    instructors: List[InstructorBrief]
    total: int
    page: int
    pageSize: int
    totalPages: int


# ============================================
# INSTRUCTOR PROFILE (eye icon — full detail panel)
# ============================================

class InstructorProfile(BaseModel):
    """
    Full instructor profile returned by GET /instructors/{id}.
    Maps to the Instructor Details side panel in the UI.
    Upcoming classes and courses are kept in separate lists.
    """
    id: str
    name: str
    email: str
    phone: Optional[str] = None
    avatar: Optional[str] = None
    backgroundImage: Optional[str] = None
    speciality: Optional[str] = None
    bio: Optional[str] = None
    joinedAt: datetime
    totalClasses: int = 0
    totalCourses: int = 0
    favouredBy: int = 0
    totalStudents: int = 0
    upcomingClasses: List[UpcomingClassBrief] = []
    upcomingCourses: List[UpcomingCourseBrief] = []

    class Config:
        from_attributes = True


# ============================================
# INSTRUCTOR UPDATE
# ============================================

class InstructorUpdate(BaseModel):
    """
    Fields editable via the Edit Details modal.
    All optional — PATCH semantics.
    """
    name: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[EmailStr] = None
    speciality: Optional[str] = None
    bio: Optional[str] = None
    avatar: Optional[str] = None
    backgroundImage: Optional[str] = None