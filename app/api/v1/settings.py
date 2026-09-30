"""
app/api/v1/settings.py
=======================
User Settings & Profile Utility endpoints.

Exposed under /api/v1/settings — all endpoints require authentication.
Every endpoint is strictly user-scoped: callers can only read/modify
their own data.

Endpoints
─────────
  PATCH /settings/notifications/app          Toggle in-app notifications ON/OFF  (A)
  PATCH /settings/notifications/whatsapp     Toggle WhatsApp notifications ON/OFF (B)
  GET   /settings/bookings                   All booked classes + courses          (C)
  GET   /settings/bookings/upcoming          Upcoming class bookings only          (D)
  POST  /settings/bookings/cancel-class      Cancel a class booking by ID          (E)
  POST  /settings/bookings/cancel-course     Cancel a course booking by ID         (F)

Design notes
────────────
* Notification toggle endpoints use `getattr` to read the current flag so the
  code is forward-compatible: when the DB column is missing today the read
  returns the default (True / opted-in) without crashing. The PATCH writes the
  new value directly to the User row — no migration needed as long as the
  column exists in the Prisma schema.

* Endpoints E and F delegate to BookingPaymentService.cancel_booking() — the
  same battle-tested method used by POST /payments/bookings/cancel and
  POST /payments/courses/cancel. This ensures identical refund logic, seat
  release, and notifications with zero code duplication.

* Upcoming bookings (D) returns class bookings whose class.scheduledAt is in
  the future. Course bookings are excluded from "upcoming" because course
  scheduling semantics differ (multi-session, ongoing). They appear in the
  full booking list (C) instead.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from app.api.v1.dependencies import get_current_active_user
from app.db.db_client import prisma
from app.services.booking_payment_service import BookingPaymentService, CANCELLATION_WINDOW_HOURS

logger = logging.getLogger(__name__)
router = APIRouter()


# ══════════════════════════════════════════════════════════════════════════════
# PYDANTIC MODELS
# ══════════════════════════════════════════════════════════════════════════════

class NotificationToggleResponse(BaseModel):
    """Returned by notification-preference toggle endpoints."""
    channel: str          # "app" | "whatsapp"
    enabled: bool         # the new value after the update
    message: str


class BookingCancelRequest(BaseModel):
    """Request body for cancelling a booking by ID."""
    booking_id: str
    reason: Optional[str] = None


# ══════════════════════════════════════════════════════════════════════════════
# A — TOGGLE IN-APP NOTIFICATIONS
# ══════════════════════════════════════════════════════════════════════════════

@router.patch(
    "/notifications/app",
    response_model=NotificationToggleResponse,
    summary="Toggle In-App Notifications",
    description="""
Enable or disable **in-app** push notifications for the authenticated user.

When disabled, the notification service skips creating `Notification` rows for
this user — the inbox will no longer receive new items until re-enabled.

**Body:**
```json
{ "enabled": false }
```
""",
    tags=["Settings"],
)
async def toggle_app_notifications(
    enabled: bool = Query(..., description="true = enable in-app notifications, false = disable"),
    current_user=Depends(get_current_active_user),
) -> NotificationToggleResponse:
    """
    Toggle the user's in-app notification preference.

    Writes `appNotificationsEnabled` on the User row. The notification
    dispatcher reads this flag before persisting any in-app notification.
    """
    await prisma.user.update(
        where={"id": current_user.id},
        data={"appNotificationsEnabled": enabled},
    )

    state = "enabled" if enabled else "disabled"
    logger.info(
        "In-app notifications %s for user=%s", state, current_user.id
    )

    return NotificationToggleResponse(
        channel="app",
        enabled=enabled,
        message=f"In-app notifications have been {state}.",
    )


# ══════════════════════════════════════════════════════════════════════════════
# B — TOGGLE WHATSAPP NOTIFICATIONS
# ══════════════════════════════════════════════════════════════════════════════

@router.patch(
    "/notifications/whatsapp",
    response_model=NotificationToggleResponse,
    summary="Toggle WhatsApp Notifications",
    description="""
Enable or disable **WhatsApp** notifications for the authenticated user.

When disabled, the notification service skips the WhatsApp Business Cloud API
call for this user. No WhatsApp messages will be sent until re-enabled.

**Body:**
```json
{ "enabled": false }
```
""",
    tags=["Settings"],
)
async def toggle_whatsapp_notifications(
    enabled: bool = Query(..., description="true = enable WhatsApp notifications, false = disable"),
    current_user=Depends(get_current_active_user),
) -> NotificationToggleResponse:
    """
    Toggle the user's WhatsApp notification preference.

    Writes `whatsappNotificationsEnabled` on the User row. The notification
    dispatcher reads this flag before sending any WhatsApp message.
    """
    await prisma.user.update(
        where={"id": current_user.id},
        data={"whatsappNotificationsEnabled": enabled},
    )

    state = "enabled" if enabled else "disabled"
    logger.info(
        "WhatsApp notifications %s for user=%s", state, current_user.id
    )

    return NotificationToggleResponse(
        channel="whatsapp",
        enabled=enabled,
        message=f"WhatsApp notifications have been {state}.",
    )


# ══════════════════════════════════════════════════════════════════════════════
# C — GET ALL BOOKINGS (classes + courses)
# ══════════════════════════════════════════════════════════════════════════════

@router.get(
    "/bookings",
    summary="My Bookings — All (Classes + Courses)",
    description="""
Returns all bookings made by the authenticated user — both class bookings and
course bookings — newest first.

Each item carries a `booking_type` discriminator (`"CLASS"` or `"COURSE"`) and
the relevant details of the booked entity.

**Optional filters:**
- `status` — `PENDING | CONFIRMED | CANCELLED | ATTENDED | MISSED`
- `booking_type` — `CLASS | COURSE` (omit for both)
- `page`, `page_size` — pagination
""",
    tags=["Settings"],
)
async def get_my_bookings(
    page: int = Query(1, ge=1, description="Page number (1-indexed)"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page"),
    status_filter: Optional[str] = Query(
        None, alias="status",
        description="Filter by booking status: PENDING | CONFIRMED | CANCELLED | ATTENDED | MISSED",
    ),
    booking_type: Optional[str] = Query(
        None,
        description="Filter by type: CLASS | COURSE (omit for both)",
    ),
    current_user=Depends(get_current_active_user),
):
    """
    Return the authenticated user's full booking list (classes + courses).

    Sorted newest first. Use the `booking_type` filter to narrow to class-only
    or course-only bookings.
    """
    user_id = current_user.id
    booking_type_upper = booking_type.upper() if booking_type else None

    if booking_type_upper and booking_type_upper not in ("CLASS", "COURSE"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="booking_type must be 'CLASS' or 'COURSE'",
        )

    valid_statuses = {"PENDING", "CONFIRMED", "CANCELLED", "ATTENDED", "MISSED"}
    if status_filter and status_filter.upper() not in valid_statuses:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"status must be one of: {', '.join(sorted(valid_statuses))}",
        )

    all_items = []

    # ── Class bookings ────────────────────────────────────────────────────────
    if booking_type_upper in (None, "CLASS"):
        class_where = {"userId": user_id, "classId": {"not": None}}
        if status_filter:
            class_where["status"] = status_filter.upper()

        class_bookings = await prisma.booking.find_many(
            where=class_where,
            include={"classes": {"include": {"instructor": True}}},
            order={"bookedAt": "desc"},
        )

        for b in class_bookings:
            cls = getattr(b, "classes", None)
            instructor = getattr(cls, "instructor", None) if cls else None
            all_items.append({
                "booking_id":    b.id,
                "booking_type":  "CLASS",
                "status":        str(b.status),
                "booked_at":     b.bookedAt.isoformat() if b.bookedAt else None,
                "cancelled_at":  b.cancelledAt.isoformat() if b.cancelledAt else None,
                "amount_paid":   b.amountPaid,
                "payment_method": str(b.paymentMethod) if b.paymentMethod else None,
                "notes":         b.notes,
                # Class-specific fields
                "class_id":      cls.id if cls else None,
                "class_title":   cls.title if cls else None,
                "scheduled_at":  cls.scheduledAt.isoformat() if cls and cls.scheduledAt else None,
                "duration":      cls.duration if cls else None,
                "location":      cls.location if cls else None,
                "difficulty":    cls.difficulty if cls else None,
                "instructor_name": instructor.name if instructor else None,
                "instructor_avatar": instructor.avatar if instructor else None,
                # Course-specific fields — null for class bookings
                "course_id":     None,
                "course_title":  None,
            })

    # ── Course bookings ───────────────────────────────────────────────────────
    if booking_type_upper in (None, "COURSE"):
        course_where = {"userId": user_id, "courseId": {"not": None}}
        if status_filter:
            course_where["status"] = status_filter.upper()

        course_bookings = await prisma.booking.find_many(
            where=course_where,
            include={"course": {"include": {"instructor": True}}},
            order={"bookedAt": "desc"},
        )

        for b in course_bookings:
            course = getattr(b, "course", None)
            instructor = getattr(course, "instructor", None) if course else None
            all_items.append({
                "booking_id":    b.id,
                "booking_type":  "COURSE",
                "status":        str(b.status),
                "booked_at":     b.bookedAt.isoformat() if b.bookedAt else None,
                "cancelled_at":  b.cancelledAt.isoformat() if b.cancelledAt else None,
                "amount_paid":   b.amountPaid,
                "payment_method": str(b.paymentMethod) if b.paymentMethod else None,
                "notes":         b.notes,
                # Class-specific fields — null for course bookings
                "class_id":      None,
                "class_title":   None,
                "scheduled_at":  None,
                "duration":      None,
                "location":      None,
                "difficulty":    None,
                "instructor_name": instructor.name if instructor else None,
                "instructor_avatar": instructor.avatar if instructor else None,
                # Course-specific fields
                "course_id":     course.id if course else None,
                "course_title":  course.title if course else None,
            })

    # Sort combined list newest first
    all_items.sort(key=lambda x: x.get("booked_at") or "", reverse=True)

    total = len(all_items)
    skip = (page - 1) * page_size
    paged = all_items[skip: skip + page_size]
    total_pages = max(1, (total + page_size - 1) // page_size) if total else 1

    return {
        "items":       paged,
        "total":       total,
        "page":        page,
        "page_size":   page_size,
        "total_pages": total_pages,
    }


# ══════════════════════════════════════════════════════════════════════════════
# D — GET UPCOMING CLASS BOOKINGS
# ══════════════════════════════════════════════════════════════════════════════

@router.get(
    "/bookings/upcoming",
    summary="My Upcoming Bookings (Classes + Courses)",
    description="""
Returns the authenticated user's **upcoming** confirmed bookings — both
**class** and **course** bookings — whose `scheduledAt` is **in the future**
from now, with status `CONFIRMED` or `PENDING`.

Sorted by scheduled date ascending (soonest first) so the user sees what's
coming next at the top.

Each item carries a `booking_type` discriminator (`"CLASS"` or `"COURSE"`)
and the relevant details of the booked entity.

**Optional filters:**
- `page`, `page_size` — pagination
""",
    tags=["Settings"],
)
async def get_my_upcoming_bookings(
    page: int = Query(1, ge=1, description="Page number (1-indexed)"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page"),
    current_user=Depends(get_current_active_user),
):
    """
    Return upcoming bookings (classes AND courses) for the authenticated user.

    A booking is "upcoming" when:
      - booking.status is CONFIRMED or PENDING
      - the entity's scheduledAt > now (the class/course hasn't happened yet)

    Both class and course bookings are included and sorted together by
    scheduledAt ascending — the next item appears first.
    """
    user_id = current_user.id
    now = datetime.now(timezone.utc)

    upcoming = []

    # ── Upcoming CLASS bookings ───────────────────────────────────────────────
    class_bookings = await prisma.booking.find_many(
        where={
            "userId":  user_id,
            "classId": {"not": None},
            "status":  {"in": ["CONFIRMED", "PENDING"]},
        },
        include={"classes": {"include": {"instructor": True}}},
        order={"bookedAt": "desc"},
    )

    for b in class_bookings:
        cls = getattr(b, "classes", None)
        if not cls or not cls.scheduledAt:
            continue

        scheduled = cls.scheduledAt
        if scheduled.tzinfo is None:
            scheduled = scheduled.replace(tzinfo=timezone.utc)
        if scheduled <= now:
            continue

        instructor = getattr(cls, "instructor", None)
        upcoming.append({
            "booking_id":      b.id,
            "booking_type":    "CLASS",
            "status":          str(b.status),
            "booked_at":       b.bookedAt.isoformat() if b.bookedAt else None,
            "amount_paid":     b.amountPaid,
            "payment_method":  str(b.paymentMethod) if b.paymentMethod else None,
            "notes":           b.notes,
            # Class-specific fields
            "class_id":        cls.id,
            "class_title":     cls.title,
            "class_image_url": cls.imageUrl if cls.imageUrl else None,
            "scheduled_at":    cls.scheduledAt.isoformat() if cls.scheduledAt else None,
            "duration":        cls.duration,
            "location":        cls.location,
            "difficulty":      cls.difficulty,
            "available_seat":  cls.availableSeat,
            "instructor_name":   instructor.name   if instructor else None,
            "instructor_avatar": instructor.avatar if instructor else None,
            # Course-specific fields — null for class bookings
            "course_id":       None,
            "course_title":    None,
            "course_image_url": None,
            # Cancellation policy reminder — always shown so the UI can display it
            "cancellation_rule": (
                f"Cancellations made within {CANCELLATION_WINDOW_HOURS} hours of the start "
                f"time are non-refundable."
            ),
        })

    # ── Upcoming COURSE bookings ──────────────────────────────────────────────
    course_bookings = await prisma.booking.find_many(
        where={
            "userId":   user_id,
            "courseId": {"not": None},
            "status":   {"in": ["CONFIRMED", "PENDING"]},
        },
        include={"course": {"include": {"instructor": True}}},
        order={"bookedAt": "desc"},
    )

    for b in course_bookings:
        course = getattr(b, "course", None)
        if not course or not course.scheduledAt:
            continue

        scheduled = course.scheduledAt
        if scheduled.tzinfo is None:
            scheduled = scheduled.replace(tzinfo=timezone.utc)
        if scheduled <= now:
            continue

        instructor = getattr(course, "instructor", None)
        upcoming.append({
            "booking_id":      b.id,
            "booking_type":    "COURSE",
            "status":          str(b.status),
            "booked_at":       b.bookedAt.isoformat() if b.bookedAt else None,
            "amount_paid":     b.amountPaid,
            "payment_method":  str(b.paymentMethod) if b.paymentMethod else None,
            "notes":           b.notes,
            # Class-specific fields — null for course bookings
            "class_id":        None,
            "class_title":     None,
            "class_image_url": None,
            "difficulty":      course.difficulty,
            "available_seat":  course.availableSeat,
            "instructor_name":   instructor.name   if instructor else None,
            "instructor_avatar": instructor.avatar if instructor else None,
            # Course-specific fields
            "course_id":       course.id,
            "course_title":    course.title,
            "course_image_url": course.imageUrl if course.imageUrl else None,
            "scheduled_at":    course.scheduledAt.isoformat() if course.scheduledAt else None,
            "duration":        course.duration,
            "location":        course.location,
            # Cancellation policy reminder
            "cancellation_rule": (
                f"Cancellations made within {CANCELLATION_WINDOW_HOURS} hours of the start "
                f"time are non-refundable."
            ),
        })

    # Sort combined list by scheduled_at ascending — soonest item first
    upcoming.sort(key=lambda x: x.get("scheduled_at") or "")

    total = len(upcoming)
    skip = (page - 1) * page_size
    paged = upcoming[skip: skip + page_size]
    total_pages = max(1, (total + page_size - 1) // page_size) if total else 1

    return {
        "items":       paged,
        "total":       total,
        "page":        page,
        "page_size":   page_size,
        "total_pages": total_pages,
    }


# ══════════════════════════════════════════════════════════════════════════════
# E — CANCEL CLASS BOOKING BY ID
# ══════════════════════════════════════════════════════════════════════════════

@router.post(
    "/bookings/cancel-class",
    summary="Cancel Class Booking",
    description="""
Cancel a **class** booking by its ID.

Applies the same cancellation and refund policy as
`POST /payments/bookings/cancel`:

- **≥ 3 hours before class start** → full refund to original payment source
- **< 3 hours before class start** → NO refund (non-refundable)

The cancellation rule is included in the response body for UI display.

**Request:**
```json
{ "booking_id": "...", "reason": "optional reason text" }
```
""",
    tags=["Settings"],
)
async def cancel_class_booking(
    data: BookingCancelRequest,
    current_user=Depends(get_current_active_user),
):
    """
    Cancel a class booking — same logic as POST /payments/bookings/cancel.

    Delegates to BookingPaymentService.cancel_booking() which handles:
      - Ownership verification (user can only cancel their own bookings)
      - Refund policy enforcement (≥ 3h → refund, < 3h → no refund)
      - Seat release on the class
      - In-app + WhatsApp notification (best-effort)
    """
    return await BookingPaymentService.cancel_booking(
        user_id=current_user.id,
        booking_id=data.booking_id,
        reason=data.reason,
    )


# ══════════════════════════════════════════════════════════════════════════════
# F — CANCEL COURSE BOOKING BY ID
# ══════════════════════════════════════════════════════════════════════════════

@router.post(
    "/bookings/cancel-course",
    summary="Cancel Course Booking",
    description="""
Cancel a **course** booking by its ID.

Applies the same cancellation and refund policy as
`POST /payments/courses/cancel`:

- **≥ 3 hours before course start** → full refund to original payment source
- **< 3 hours before course start** → NO refund (non-refundable)

**Request:**
```json
{ "booking_id": "...", "reason": "optional reason text" }
```
""",
    tags=["Settings"],
)
async def cancel_course_booking(
    data: BookingCancelRequest,
    current_user=Depends(get_current_active_user),
):
    """
    Cancel a course booking — same logic as POST /payments/courses/cancel.

    Delegates to BookingPaymentService.cancel_booking() which handles:
      - Ownership verification (user can only cancel their own bookings)
      - Refund policy enforcement (≥ 3h → refund, < 3h → no refund)
      - Seat release on the course
      - In-app + WhatsApp notification (best-effort)
    """
    return await BookingPaymentService.cancel_booking(
        user_id=current_user.id,
        booking_id=data.booking_id,
        reason=data.reason,
    )