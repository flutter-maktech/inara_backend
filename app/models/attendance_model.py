"""
app/models/attendance_model.py
===============================
Pydantic schemas for GET /users/attendance_stats.

Naming convention note: this endpoint lives in the same "booking analytics"
family as app/models/dashboard_model.py and the /settings/bookings* endpoints
(app/api/v1/settings.py, app/api/v1/payments.py) — both of which already use
snake_case response keys. This file follows that established convention
rather than the camelCase used by the older profile/CRUD models
(user.py, member_model.py, instructor_model.py).
"""

from pydantic import BaseModel, Field
from typing import List, Literal, Optional
from datetime import datetime


class MonthlyPracticePoint(BaseModel):
    """
    One point on the practice trend line (mirrors the small chart in the
    'My Practice' mockup — DEC / JAN / FEB / MAR ... with a class count).

    Always covers the trailing 6 calendar months up to and including the
    current one, independent of the `period` filter — the filter changes
    the headline stats below; the trend line gives constant recent context.
    This is an additive convenience field beyond the 5 required stats, to
    directly support the graph shown in the attached reference image.
    """
    month: str          # short label, e.g. "Jan"
    year: int
    class_count: int = 0


class UserAttendanceStatsResponse(BaseModel):
    """
    Response for GET /users/attendance_stats.

    A class booking counts toward these stats when:
      • booking.status is CONFIRMED or ATTENDED (i.e. actually went through —
        matches the same convention used everywhere else in this codebase,
        e.g. dashboard_service.py, classes_service.py, courses_service.py)
      • it has NOT been cancelled
      • the class's scheduledAt has already passed (you can't have practiced
        a class that hasn't happened yet)

    `period` narrows the window the headline figures are computed over;
    `monthly_trend` is always the trailing 6 months regardless of `period`.
    """
    period: Literal["month", "year", "all"]
    period_label: str  # "This Month" | "This Year" | "All Time"

    total_classes: int = Field(..., description="Total attended classes within the selected period")

    hours_on_the_mat: str = Field(..., description='Formatted total practice time, e.g. "13h 45m"')
    total_minutes_practiced: int = Field(..., description="Same figure as hours_on_the_mat, in raw minutes")

    most_practiced: Optional[str] = Field(None, description="Class title attended most often in the period")
    most_practiced_count: int = Field(0, description="How many times most_practiced was attended")

    most_often_with: Optional[str] = Field(None, description="Instructor name practiced with most often")
    most_often_with_avatar: Optional[str] = None
    most_often_with_count: int = Field(0, description="How many classes were taken with most_often_with")

    weekly_average: float = Field(..., description="Average classes per week, elapsed-time-adjusted, within the period")

    member_since: datetime = Field(..., description="Account join date (User.createdAt)")

    monthly_trend: List[MonthlyPracticePoint] = Field(
        default_factory=list,
        description="Trailing 6 months of class counts, for the practice trend chart",
    )

    class Config:
        from_attributes = True