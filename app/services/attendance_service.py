"""
app/services/attendance_service.py
====================================
Business logic for GET /users/attendance_stats — the "My Practice" screen.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"ATTENDED" RULE (per spec)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"if a class is booked (and not subsequently cancelled) then it is attended
in the scheduled date/time & it will be included within the statistics
only after the scheduled date/time"

Concretely, a class booking counts toward these stats when ALL of:
  1. booking.status is CONFIRMED or ATTENDED — the same "did this booking
     really go through" convention used everywhere else in this codebase
     (dashboard_service.py, classes_service.py, courses_service.py all
     filter on status IN [CONFIRMED, ATTENDED]). PENDING bookings represent
     a checkout that was started but never completed (e.g. an abandoned
     MyFatoorah gateway session) and are excluded; MISSED is an explicit
     no-show and is excluded — neither represents real practice time.
  2. it has not been cancelled (implied by #1 — CANCELLED is never in the
     counted-statuses list).
  3. the class's scheduledAt is at or before "now" — you cannot have
     practiced a class that has not happened yet, regardless of how far in
     advance it was booked.

Only CLASS bookings are counted (not Courses) — the spec's wording and the
reference mockup both talk about "classes" ("20 Classes", "Most Practiced",
weekly class average), consistent with a yoga-studio "My Practice" screen.
"""

from __future__ import annotations

import re
from collections import Counter
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from fastapi import HTTPException, status

from app.db.db_client import prisma
from app.models.attendance_model import MonthlyPracticePoint, UserAttendanceStatsResponse
from prisma.enums import BookingStatus

# "Really happened" bookings — see module docstring.
_COUNTED_STATUSES = [BookingStatus.CONFIRMED, BookingStatus.ATTENDED]

_PERIOD_LABELS = {
    "month": "This Month",
    "year": "This Year",
    "all": "All Time",
}

# Canonical `duration` format used across the app: "8:00 AM to 10:00 AM"
# (see classes_model.py / courses_model.py docstrings and seed data).
_TIME_RANGE_RE = re.compile(
    r"(\d{1,2}):(\d{2})\s*([AaPp][Mm])\s*to\s*(\d{1,2}):(\d{2})\s*([AaPp][Mm])"
)
# Defensive fallbacks in case a class was ever entered with a plain numeric duration.
_HOURS_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:hour|hr)s?", re.IGNORECASE)
_MINUTES_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:min|minute)s?", re.IGNORECASE)


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _make_aware(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _parse_duration_to_minutes(duration: Optional[str]) -> int:
    """
    Parse a class's `duration` string into whole minutes.

    Primary format: a time range, e.g. "8:00 AM to 10:00 AM" → 120.
    Falls back to a plain "90 min" / "1.5 hour" style value if present.
    Unparseable/blank input returns 0 rather than raising — one malformed
    class should never break a user's whole stats view.
    """
    if not duration:
        return 0
    text = duration.strip()

    m = _TIME_RANGE_RE.search(text)
    if m:
        sh, sm, sap, eh, em, eap = m.groups()
        start_minutes = (int(sh) % 12 + (12 if sap.lower() == "pm" else 0)) * 60 + int(sm)
        end_minutes = (int(eh) % 12 + (12 if eap.lower() == "pm" else 0)) * 60 + int(em)
        delta = end_minutes - start_minutes
        if delta <= 0:
            delta += 24 * 60  # defensive: tolerate an overnight-spanning entry
        return delta

    m = _HOURS_RE.search(text)
    if m:
        return round(float(m.group(1)) * 60)

    m = _MINUTES_RE.search(text)
    if m:
        return round(float(m.group(1)))

    return 0


def _format_minutes(total_minutes: int) -> str:
    """13h 45m / 6h 0m / 45m — matches the reference mockup's display format."""
    total_minutes = max(0, total_minutes)
    hours, minutes = divmod(total_minutes, 60)
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def _month_bounds(now: datetime) -> datetime:
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _year_bounds(now: datetime) -> datetime:
    return now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)


def _add_months(dt: datetime, months: int) -> datetime:
    """
    Shift a datetime by whole calendar months. Only ever called here on
    values produced by _month_bounds() (always day=1), so there's no need
    to handle "day doesn't exist in target month" edge cases.
    """
    month_index = dt.month - 1 + months
    year = dt.year + month_index // 12
    month = month_index % 12 + 1
    return dt.replace(year=year, month=month)


def _resolve_period_start(period: str, now: datetime, member_since: datetime) -> datetime:
    """
    Lower bound for the selected period. Upper bound is always `now` — you
    can't have attended a class that hasn't happened yet, no matter which
    filter tab is selected.
    """
    if period == "month":
        return _month_bounds(now)
    if period == "year":
        return _year_bounds(now)
    return member_since  # "all" — bounded by join date, not open-ended


def _weekly_average(count: int, period_start: datetime, now: datetime) -> float:
    """
    classes / week, using time ELAPSED so far within the period (not the
    period's full nominal length). On day 5 of "This Month" this divides
    by ~5 days, not by all 30/31 — dividing by the full month would
    understate the true rate before the month is over.
    """
    elapsed_days = max((now - period_start).total_seconds() / 86400, 1.0)
    weeks = elapsed_days / 7
    return round(count / weeks, 1) if weeks > 0 else 0.0


class AttendanceService:
    """Computes 'My Practice' attendance statistics for a single user."""

    @staticmethod
    async def get_attendance_stats(user_id: str, period: str) -> UserAttendanceStatsResponse:
        if period not in _PERIOD_LABELS:
            period = "all"

        user = await prisma.user.find_unique(where={"id": user_id})
        if not user:
            # Defensive — the caller already resolved this user from a valid
            # JWT, but an account deleted mid-session should fail loudly.
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

        now = _now_utc()
        member_since = _make_aware(user.createdAt)
        period_start = _resolve_period_start(period, now, member_since)

        # ── Single query: every counted class booking in the period, with
        #    the class + its instructor joined in — no N+1. ────────────────
        bookings = await prisma.booking.find_many(
            where={
                "userId": user_id,
                "classId": {"not": None},
                "status": {"in": _COUNTED_STATUSES},
                "classes": {
                    "is": {"scheduledAt": {"lte": now, "gte": period_start}}
                },
            },
            include={"classes": {"include": {"instructor": True}}},
        )

        total_classes = 0
        total_minutes = 0
        title_counter: Counter = Counter()
        instructor_counter: Counter = Counter()
        instructor_lookup: Dict[str, Dict[str, Optional[str]]] = {}

        for b in bookings:
            cls = getattr(b, "classes", None)
            if not cls:
                continue
            total_classes += 1
            total_minutes += _parse_duration_to_minutes(cls.duration)

            if cls.title:
                title_counter[cls.title] += 1

            instructor = getattr(cls, "instructor", None)
            if instructor:
                instructor_counter[instructor.id] += 1
                instructor_lookup[instructor.id] = {
                    "name": instructor.name,
                    "avatar": instructor.avatar,
                }

        most_practiced, most_practiced_count = (None, 0)
        if title_counter:
            most_practiced, most_practiced_count = title_counter.most_common(1)[0]

        most_often_with, most_often_with_avatar, most_often_with_count = (None, None, 0)
        if instructor_counter:
            top_instructor_id, most_often_with_count = instructor_counter.most_common(1)[0]
            info = instructor_lookup.get(top_instructor_id, {})
            most_often_with = info.get("name")
            most_often_with_avatar = info.get("avatar")

        # ── Trend line: trailing 6 calendar months, independent of `period` ──
        monthly_trend = await AttendanceService._build_monthly_trend(user_id, now)

        return UserAttendanceStatsResponse(
            period=period,
            period_label=_PERIOD_LABELS[period],
            total_classes=total_classes,
            hours_on_the_mat=_format_minutes(total_minutes),
            total_minutes_practiced=total_minutes,
            most_practiced=most_practiced,
            most_practiced_count=most_practiced_count,
            most_often_with=most_often_with,
            most_often_with_avatar=most_often_with_avatar,
            most_often_with_count=most_often_with_count,
            weekly_average=_weekly_average(total_classes, period_start, now),
            member_since=member_since,
            monthly_trend=monthly_trend,
        )

    @staticmethod
    async def _build_monthly_trend(user_id: str, now: datetime) -> List[MonthlyPracticePoint]:
        """
        Trailing 6 calendar months (oldest → newest, current month last),
        each with a count of that user's counted class bookings whose class
        fell in that month. One query, aggregated in Python — consistent
        with the N+1-avoidance style used across the rest of the codebase.
        """
        window_start = _add_months(_month_bounds(now), -5)

        bookings = await prisma.booking.find_many(
            where={
                "userId": user_id,
                "classId": {"not": None},
                "status": {"in": _COUNTED_STATUSES},
                "classes": {
                    "is": {"scheduledAt": {"lte": now, "gte": window_start}}
                },
            },
            include={"classes": True},
        )

        buckets: Dict[Tuple[int, int], int] = {}
        ordered_keys: List[Tuple[int, int]] = []
        cursor = window_start
        for _ in range(6):
            key = (cursor.year, cursor.month)
            buckets[key] = 0
            ordered_keys.append(key)
            cursor = _add_months(cursor, 1)

        for b in bookings:
            cls = getattr(b, "classes", None)
            if not cls or not cls.scheduledAt:
                continue
            sched = _make_aware(cls.scheduledAt)
            key = (sched.year, sched.month)
            if key in buckets:
                buckets[key] += 1

        return [
            MonthlyPracticePoint(
                month=datetime(year=y, month=m, day=1).strftime("%b"),
                year=y,
                class_count=buckets[(y, m)],
            )
            for (y, m) in ordered_keys
        ]