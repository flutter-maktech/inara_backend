"""
app/services/history_service.py
=================================
Unified service layer powering the 10 Payment / Booking History endpoints
exposed under /payments/* :

  USER (own data)                                ADMIN / staff (all data)
  ─────────────────────────                      ─────────────────────────
  GET /payments/bookings/history                 GET /payments/bookings/all-history
  GET /payments/memberships/history              GET /payments/memberships/all-history
  GET /payments/packages/history                 GET /payments/packages/all-history
  GET /payments/orders/history                   GET /payments/packages/all-orders

────────────────────────────────────────────────────────────────────────────
DESIGN PRINCIPLES
────────────────────────────────────────────────────────────────────────────
1. **Single source of truth per concern.**
     - `Booking`    → class AND course booking ledger (polymorphic)
     - `Membership` → active/expired user memberships  (also created from packages)
     - `PaymentLog` (module=PACKAGE) → the canonical "package purchase" record;
        the Membership row created from it is joined in for validity dates.
     - `Order`      → store orders
     - `PaymentLog` is consulted on every list to surface the *payment* status
       (SUCCESS / FAILED / INITIATED) next to the entity status.

2. **One pagination envelope** — total / page / page_size / total_pages.

3. **Role-based access control** is enforced inside the service, not the
   router, so the same guard logic can't be bypassed by an alternate caller.

4. **Search & filter** parameters are optional and additive — each endpoint
   builds a Prisma `where` dict by composition.

5. **Defensive serialisation** — every datetime goes through `_iso()` so a
   `None` is preserved as `None` (never crashes), and Decimal-likes round
   to floats safely.

6. **Zero schema changes.** Everything uses the existing models.

7. **Booking polymorphism** — `get_my_bookings_history` and
   `get_all_bookings_history` now return BOTH class and course bookings.
   An optional `booking_type` parameter ("CLASS" | "COURSE") narrows results.
   Each item carries a `booking_type` discriminator field plus the
   appropriate class_* or course_* detail fields.
"""

from __future__ import annotations

import json
import logging
import math
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException, status

from app.db.db_client import prisma
from prisma.enums import (
    BookingStatus,
    MembershipStatus,
    OrderStatus,
    PaymentStatus,
    UserRole,
)

logger = logging.getLogger(__name__)


# ═════════════════════════════════════════════════════════════════════════════
# Internal helpers
# ═════════════════════════════════════════════════════════════════════════════

def _iso(dt: Optional[datetime]) -> Optional[str]:
    """Safely serialize an Optional[datetime] to ISO-8601 string."""
    if dt is None:
        return None
    try:
        return dt.isoformat()
    except Exception:
        return None


def _paginate(page: int, page_size: int) -> Tuple[int, int]:
    """Return (skip, take) — clamps inputs to safe bounds."""
    page = max(1, int(page or 1))
    page_size = max(1, min(int(page_size or 20), 100))
    return (page - 1) * page_size, page_size


def _envelope(items: List[Any], total: int, page: int, page_size: int) -> dict:
    page = max(1, int(page or 1))
    page_size = max(1, min(int(page_size or 20), 100))
    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": math.ceil(total / page_size) if total else 0,
    }


def _serialize_actor(user) -> dict:
    """Project a User record onto the HistoryActor shape."""
    if not user:
        return {"id": "", "name": "Unknown", "email": "", "phone": None, "avatar": None}
    return {
        "id": user.id,
        "name": user.name,
        "email": user.email,
        "phone": user.phone,
        "avatar": user.avatar,
    }



async def _get_package_log_ids_for_user(user_id: str) -> List[str]:
    """
    Return all PaymentLog IDs for a given user where module=PACKAGE.

    These IDs identify package purchases. Membership rows linked to any of
    these IDs are package-activated records and must be excluded from
    membership history to enforce the Membership / Package domain separation.

    Returns an empty list if the user has no package purchases.
    """
    logs = await prisma.paymentlog.find_many(
        where={"userId": user_id, "module": "PACKAGE"},
    )
    return [log.id for log in logs]


async def _payment_status_for(
    *,
    user_id: str,
    module: str,
    payment_log_id: Optional[str] = None,
    reference_id: Optional[str] = None,
) -> Tuple[Optional[str], Optional[str]]:
    """
    Resolve (payment_status, reference_id) from the PaymentLog table.

    Lookup precedence (most → least specific):
      1. `payment_log_id` — direct primary-key lookup, exact match per row.
      2. `reference_id`   — unique-ish reference (e.g. BOOKING-xxx).
      3. Fallback         — most recent log for (user, module). Used only
         when neither of the above is available; not row-accurate, but a
         reasonable best-effort for legacy rows missing the FK link.
    """
    log = None

    if payment_log_id:
        log = await prisma.paymentlog.find_unique(where={"id": payment_log_id})

    if log is None and reference_id:
        log = await prisma.paymentlog.find_first(
            where={"referenceId": reference_id, "module": module}
        )

    if log is None and not payment_log_id and not reference_id:
        log = await prisma.paymentlog.find_first(
            where={"userId": user_id, "module": module},
            order={"createdAt": "desc"},
        )

    if not log:
        return None, reference_id
    return str(log.status), log.referenceId


async def _require_role(user_id: str, allowed: List[str]) -> Any:
    """
    Verify the caller has one of the allowed roles.
    `allowed` accepts string role names ("ADMIN", "MANAGER", "INSTRUCTOR").
    """
    user = await prisma.user.find_unique(where={"id": user_id})
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if str(user.role) not in allowed:
        roles_label = ", ".join(allowed)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Access denied. This endpoint is restricted to: {roles_label}.",
        )
    return user


def _safe_enum(value: Optional[str], enum_cls) -> Optional[Any]:
    """
    Convert a string query param to a Prisma enum member.
    Returns None if value is falsy or unknown (so the filter is simply not applied).
    """
    if not value:
        return None
    try:
        return enum_cls[value.upper()]
    except (KeyError, AttributeError):
        return None


def _serialize_booking(b: Any, entity: Any, entity_type: str, payment_status: Optional[str], ref_id: Optional[str]) -> dict:
    """
    Build the polymorphic booking history dict from a Booking row.

    entity      : the Classes or Course object (may be None for legacy/orphaned rows)
    entity_type : "CLASS" | "COURSE"
    """
    instructor = getattr(entity, "instructor", None) if entity else None

    row: Dict[str, Any] = {
        "booking_id":       b.id,
        "booking_type":     entity_type,
        # Class-specific
        "class_id":         None,
        "class_title":      None,
        "class_image_url":  None,
        "class_scheduled_at": None,
        "class_location":   None,
        # Course-specific
        "course_id":        None,
        "course_title":     None,
        "course_image_url": None,
        "course_scheduled_at": None,
        "course_location":  None,
        # Common
        "instructor_name":  instructor.name if instructor else None,
        "status":           str(b.status),
        "amount_paid":      b.amountPaid,
        "currency":         "QAR",
        "payment_method":   str(b.paymentMethod) if b.paymentMethod else None,
        "payment_status":   payment_status,
        "reference_id":     ref_id,
        "booked_at":        _iso(b.bookedAt) or "",
        "cancelled_at":     _iso(b.cancelledAt),
        "notes":            b.notes,
    }

    if entity_type == "CLASS" and entity:
        row["class_id"] = b.classId
        row["class_title"] = entity.title
        row["class_image_url"] = entity.imageUrl
        row["class_scheduled_at"] = _iso(entity.scheduledAt)
        row["class_location"] = entity.location
    elif entity_type == "COURSE" and entity:
        row["course_id"] = b.courseId
        row["course_title"] = entity.title
        row["course_image_url"] = entity.imageUrl
        row["course_scheduled_at"] = _iso(entity.scheduledAt)
        row["course_location"] = entity.location

    return row


# ═════════════════════════════════════════════════════════════════════════════
# HISTORY SERVICE
# ═════════════════════════════════════════════════════════════════════════════

class HistoryService:
    """
    Unified history service for bookings, memberships, packages and orders.

    Public surface
    ──────────────
      User views:
        - get_my_bookings_history       (class + course, with optional booking_type filter)
        - get_my_memberships_history
        - get_my_packages_history
        - get_my_orders_history

      Admin views:
        - get_all_bookings_history      (Admin / Manager / Instructor)
        - get_all_memberships_history   (Admin / Manager)
        - get_all_packages_history      (Admin / Manager)
        - get_all_orders_history        (Admin / Manager)
    """

    # ════════════════════════════════════════════════════════════════════════
    # 1. BOOKING HISTORY  (polymorphic: CLASS + COURSE)
    # ════════════════════════════════════════════════════════════════════════

    @staticmethod
    async def get_my_bookings_history(
        user_id: str,
        page: int = 1,
        page_size: int = 20,
        status_filter: Optional[str] = None,
        search: Optional[str] = None,
        booking_type: Optional[str] = None,
    ) -> dict:
        """
        User's own bookings (classes + courses). Newest first.

        booking_type : "CLASS" | "COURSE" | None (returns both)
        """
        booking_type_upper = booking_type.upper() if booking_type else None

        # Build two separate queries when no type filter, or one targeted query
        all_items: List[dict] = []

        # ── Class bookings ────────────────────────────────────────────────
        if booking_type_upper in (None, "CLASS"):
            class_where: Dict[str, Any] = {
                "userId": user_id,
                "classId": {"not": None},
            }
            enum_status = _safe_enum(status_filter, BookingStatus)
            if enum_status is not None:
                class_where["status"] = enum_status
            if search:
                class_where["classes"] = {
                    "is": {
                        "OR": [
                            {"title":    {"contains": search, "mode": "insensitive"}},
                            {"location": {"contains": search, "mode": "insensitive"}},
                        ]
                    }
                }

            class_bookings = await prisma.booking.find_many(
                where=class_where,
                include={"classes": {"include": {"instructor": True}}},
                order={"bookedAt": "desc"},
            )
            for b in class_bookings:
                cls = getattr(b, "classes", None)
                payment_status, ref_id = await _payment_status_for(
                    user_id=user_id, module="BOOKING",
                    payment_log_id=b.paymentLogId,
                )
                all_items.append(_serialize_booking(b, cls, "CLASS", payment_status, ref_id))

        # ── Course bookings ───────────────────────────────────────────────
        if booking_type_upper in (None, "COURSE"):
            course_where: Dict[str, Any] = {
                "userId": user_id,
                "courseId": {"not": None},
            }
            enum_status = _safe_enum(status_filter, BookingStatus)
            if enum_status is not None:
                course_where["status"] = enum_status
            if search:
                course_where["course"] = {
                    "is": {
                        "OR": [
                            {"title":    {"contains": search, "mode": "insensitive"}},
                            {"location": {"contains": search, "mode": "insensitive"}},
                        ]
                    }
                }

            course_bookings = await prisma.booking.find_many(
                where=course_where,
                include={"course": {"include": {"instructor": True}}},
                order={"bookedAt": "desc"},
            )
            for b in course_bookings:
                course = getattr(b, "course", None)
                payment_status, ref_id = await _payment_status_for(
                    user_id=user_id, module="BOOKING",
                    payment_log_id=b.paymentLogId,
                )
                all_items.append(_serialize_booking(b, course, "COURSE", payment_status, ref_id))

        # Sort combined results by booked_at descending
        all_items.sort(key=lambda x: x.get("booked_at") or "", reverse=True)

        # Manual pagination over the combined result set
        total = len(all_items)
        skip, take = _paginate(page, page_size)
        paged = all_items[skip: skip + take]

        return _envelope(paged, total, page, page_size)

    @staticmethod
    async def get_all_bookings_history(
        actor_user_id: str,
        page: int = 1,
        page_size: int = 20,
        status_filter: Optional[str] = None,
        search: Optional[str] = None,
        booking_type: Optional[str] = None,
    ) -> dict:
        """
        Admin / Manager / Instructor view of all bookings (class + course).

        Instructors are scoped to bookings for classes/courses they instruct.
        booking_type : "CLASS" | "COURSE" | None (returns both)
        """
        actor = await _require_role(
            actor_user_id,
            allowed=[UserRole.ADMIN.value, UserRole.MANAGER.value, UserRole.INSTRUCTOR.value],
        )
        is_instructor = str(actor.role) == UserRole.INSTRUCTOR.value
        booking_type_upper = booking_type.upper() if booking_type else None

        all_items: List[dict] = []

        # ── Class bookings ────────────────────────────────────────────────
        if booking_type_upper in (None, "CLASS"):
            class_where: Dict[str, Any] = {"classId": {"not": None}}

            if is_instructor:
                class_where["classes"] = {"is": {"instructorId": actor.id}}

            enum_status = _safe_enum(status_filter, BookingStatus)
            if enum_status is not None:
                class_where["status"] = enum_status

            if search:
                search_clauses = [
                    {"classes": {"is": {"title":    {"contains": search, "mode": "insensitive"}}}},
                    {"classes": {"is": {"location": {"contains": search, "mode": "insensitive"}}}},
                    {"user":    {"is": {"name":     {"contains": search, "mode": "insensitive"}}}},
                    {"user":    {"is": {"email":    {"contains": search, "mode": "insensitive"}}}},
                ]
                if "classes" in class_where:
                    class_where = {"AND": [class_where, {"OR": search_clauses}]}
                else:
                    class_where["OR"] = search_clauses

            class_bookings = await prisma.booking.find_many(
                where=class_where,
                include={
                    "user": True,
                    "classes": {"include": {"instructor": True}},
                },
                order={"bookedAt": "desc"},
            )
            for b in class_bookings:
                cls = getattr(b, "classes", None)
                user = getattr(b, "user", None)
                payment_status, ref_id = await _payment_status_for(
                    user_id=b.userId, module="BOOKING",
                    payment_log_id=b.paymentLogId,
                )
                row = _serialize_booking(b, cls, "CLASS", payment_status, ref_id)
                row["user"] = _serialize_actor(user)
                all_items.append(row)

        # ── Course bookings ───────────────────────────────────────────────
        if booking_type_upper in (None, "COURSE"):
            course_where: Dict[str, Any] = {"courseId": {"not": None}}

            if is_instructor:
                course_where["course"] = {"is": {"instructorId": actor.id}}

            enum_status = _safe_enum(status_filter, BookingStatus)
            if enum_status is not None:
                course_where["status"] = enum_status

            if search:
                search_clauses = [
                    {"course": {"is": {"title":    {"contains": search, "mode": "insensitive"}}}},
                    {"course": {"is": {"location": {"contains": search, "mode": "insensitive"}}}},
                    {"user":   {"is": {"name":     {"contains": search, "mode": "insensitive"}}}},
                    {"user":   {"is": {"email":    {"contains": search, "mode": "insensitive"}}}},
                ]
                if "course" in course_where:
                    course_where = {"AND": [course_where, {"OR": search_clauses}]}
                else:
                    course_where["OR"] = search_clauses

            course_bookings = await prisma.booking.find_many(
                where=course_where,
                include={
                    "user": True,
                    "course": {"include": {"instructor": True}},
                },
                order={"bookedAt": "desc"},
            )
            for b in course_bookings:
                course = getattr(b, "course", None)
                user = getattr(b, "user", None)
                payment_status, ref_id = await _payment_status_for(
                    user_id=b.userId, module="BOOKING",
                    payment_log_id=b.paymentLogId,
                )
                row = _serialize_booking(b, course, "COURSE", payment_status, ref_id)
                row["user"] = _serialize_actor(user)
                all_items.append(row)

        # Sort combined results by booked_at descending
        all_items.sort(key=lambda x: x.get("booked_at") or "", reverse=True)

        total = len(all_items)
        skip, take = _paginate(page, page_size)
        paged = all_items[skip: skip + take]

        return _envelope(paged, total, page, page_size)

    # ════════════════════════════════════════════════════════════════════════
    # 2. MEMBERSHIP HISTORY
    # ════════════════════════════════════════════════════════════════════════

    @staticmethod
    async def get_my_memberships_history(
        user_id: str,
        page: int = 1,
        page_size: int = 20,
        status_filter: Optional[str] = None,
        search: Optional[str] = None,
    ) -> dict:
        """User's own memberships. Only userId-bound rows (templates excluded).

        INDEPENDENCE GUARANTEE:
        Packages and Memberships are 100% separate domains. A package purchase
        activates a Membership row (isPaid=True) linked to a PaymentLog with
        module=PACKAGE. This method explicitly excludes those package-activated
        rows so they NEVER appear in the membership history.

        Exclusion strategy (no schema change required):
          1. Collect all PaymentLog IDs for this user where module=PACKAGE.
          2. Exclude Membership rows whose paymentLogId is in that set.
        This is a clean, O(1 extra query) solution — the package log IDs are
        fetched in one call, then used as a NOT-IN filter.
        """
        # ── Step 1: collect PaymentLog IDs that belong to the PACKAGE domain ──
        # These represent package purchases — their linked Membership rows must
        # NEVER appear in the membership history.
        package_log_ids = await _get_package_log_ids_for_user(user_id)

        # ── Step 2: build membership query, excluding package-backed rows ──────
        where: Dict[str, Any] = {"userId": user_id, "isPaid": True}

        # Exclude package-activated membership rows
        if package_log_ids:
            where["paymentLogId"] = {"notIn": package_log_ids}
        else:
            # No package purchases exist → no exclusion needed, but be explicit
            # about what we're looking for: rows with no paymentLogId pointing
            # to the PACKAGE domain (already handled by the empty set branch).
            pass

        enum_status = _safe_enum(status_filter, MembershipStatus)
        if enum_status is not None:
            where["status"] = enum_status

        if search:
            where["OR"] = [
                {"name":        {"contains": search, "mode": "insensitive"}},
                {"description": {"contains": search, "mode": "insensitive"}},
            ]

        total = await prisma.membership.count(where=where)
        skip, take = _paginate(page, page_size)

        memberships = await prisma.membership.find_many(
            where=where,
            order={"enrolledAt": "desc"},
            skip=skip,
            take=take,
        )

        items: List[dict] = []
        for m in memberships:
            payment_status, ref_id = await _payment_status_for(
                user_id=user_id, module="MEMBERSHIP",
                payment_log_id=m.paymentLogId,
            )
            items.append({
                "membership_id":   m.id,
                "name":            m.name,
                "description":     m.description,
                "price":           m.price,
                "currency":        "QAR",
                "duration_days":   m.durationDays,
                "status":          str(m.status),
                "progress":        m.progress,
                "start_date":      _iso(m.startDate),
                "end_date":        _iso(m.endDate),
                "enrolled_at":     _iso(m.enrolledAt),
                "cancelled_at":    _iso(m.cancelledAt),
                "auto_renew":      bool(m.autoRenew),
                "payment_method":  str(m.paymentMethod) if m.paymentMethod else None,
                "payment_status":  payment_status,
                "reference_id":    ref_id,
                "is_paid":         bool(m.isPaid),
            })

        return _envelope(items, total, page, page_size)

    @staticmethod
    async def get_all_memberships_history(
        actor_user_id: str,
        page: int = 1,
        page_size: int = 20,
        status_filter: Optional[str] = None,
        search: Optional[str] = None,
    ) -> dict:
        """Admin / Manager view of all memberships sold across the app.

        INDEPENDENCE GUARANTEE:
        Excludes package-activated membership rows (those whose paymentLogId
        references a PaymentLog with module=PACKAGE). Package history is
        surfaced exclusively via GET /payments/packages/all-history.
        """
        await _require_role(
            actor_user_id,
            allowed=[UserRole.ADMIN.value, UserRole.MANAGER.value],
        )

        # ── Collect ALL package PaymentLog IDs across all users ───────────────
        # These represent package purchases — their linked Membership rows must
        # NEVER appear in the membership history (admin or user view).
        all_package_logs = await prisma.paymentlog.find_many(
            where={"module": "PACKAGE"},
            # Only fetch the id field — minimal data transfer
        )
        all_package_log_ids: List[str] = [log.id for log in all_package_logs]

        # ── Build membership query excluding package-backed rows ───────────────
        where: Dict[str, Any] = {"isPaid": True}

        if all_package_log_ids:
            where["paymentLogId"] = {"notIn": all_package_log_ids}

        enum_status = _safe_enum(status_filter, MembershipStatus)
        if enum_status is not None:
            where["status"] = enum_status

        if search:
            where["OR"] = [
                {"name":        {"contains": search, "mode": "insensitive"}},
                {"description": {"contains": search, "mode": "insensitive"}},
                {"user": {"is": {"name":  {"contains": search, "mode": "insensitive"}}}},
                {"user": {"is": {"email": {"contains": search, "mode": "insensitive"}}}},
            ]

        total = await prisma.membership.count(where=where)
        skip, take = _paginate(page, page_size)

        memberships = await prisma.membership.find_many(
            where=where,
            include={"user": True},
            order={"enrolledAt": "desc"},
            skip=skip,
            take=take,
        )

        items: List[dict] = []
        for m in memberships:
            payment_status, ref_id = await _payment_status_for(
                user_id=m.userId, module="MEMBERSHIP",
                payment_log_id=m.paymentLogId,
            )
            items.append({
                "membership_id":   m.id,
                "name":            m.name,
                "description":     m.description,
                "price":           m.price,
                "currency":        "QAR",
                "duration_days":   m.durationDays,
                "status":          str(m.status),
                "progress":        m.progress,
                "start_date":      _iso(m.startDate),
                "end_date":        _iso(m.endDate),
                "enrolled_at":     _iso(m.enrolledAt),
                "cancelled_at":    _iso(m.cancelledAt),
                "auto_renew":      bool(m.autoRenew),
                "payment_method":  str(m.paymentMethod) if m.paymentMethod else None,
                "payment_status":  payment_status,
                "reference_id":    ref_id,
                "is_paid":         bool(m.isPaid),
                "user":            _serialize_actor(getattr(m, "user", None)),
            })

        return _envelope(items, total, page, page_size)

    # ════════════════════════════════════════════════════════════════════════
    # 3. PACKAGE HISTORY
    # Built off PaymentLog(module=PACKAGE) — that's the authoritative
    # record of every package purchase attempt. We join in the resulting
    # Membership row for validity dates when available.
    # ════════════════════════════════════════════════════════════════════════

    @staticmethod
    async def _build_package_history_item(
        log: Any, include_user: bool = False,
    ) -> dict:
        """Hydrate one PaymentLog (module=PACKAGE) into a package-history row.

        DESIGN: Packages and Memberships are fully independent domain objects.
        The Package history is sourced from the PaymentLog (module=PACKAGE) as
        the single source of truth. Package details are resolved from the Package
        table via the package_id stored in gatewayResponse. Membership data is
        intentionally NOT exposed here — they are separate entities.

        The only membership-adjacent field surfaced is the activation validity
        window (valid_from / valid_until / duration_days) because that
        is inherently a property of the purchased package plan, not of any
        Membership record.
        """
        # ── Resolve package_id and auto_renew from gatewayResponse ──────────
        # gatewayResponse is stored as json.dumps() string; Prisma may return
        # it already parsed as a dict depending on client version.
        raw_meta = log.gatewayResponse or {}
        if isinstance(raw_meta, str):
            try:
                gateway_meta = json.loads(raw_meta)
            except (json.JSONDecodeError, ValueError):
                gateway_meta = {}
        else:
            gateway_meta = raw_meta

        package_id: Optional[str] = None
        auto_renew: Optional[bool] = None
        if isinstance(gateway_meta, dict):
            package_id = gateway_meta.get("package_id")
            raw_ar = gateway_meta.get("auto_renew")
            auto_renew = bool(raw_ar) if raw_ar is not None else None

        # ── Resolve Package record (source of truth for plan details) ────────
        package = None
        if package_id:
            try:
                package = await prisma.package.find_unique(where={"id": package_id})
            except Exception:
                package = None

        # ── Determine validity window from the PaymentLog-linked record ──────
        # For WALLET purchases the activation record (a Membership row created
        # by _activate_package_membership) is linked via paymentLogId.
        # For GATEWAY purchases that are still INITIATED/PENDING there may be
        # no linked record yet — that is expected and handled gracefully.
        # We only pull valid_from / valid_until / duration_days from the
        # linked activation record; we do NOT expose membership_id or
        # membership_status because Packages and Memberships are independent.
        activation = await prisma.membership.find_first(
            where={"userId": log.userId, "paymentLogId": log.id}
        )

        row = {
            "payment_log_id":      log.id,
            "reference_id":        log.referenceId,
            "package_id":          package_id,
            "package_name":        package.name        if package    else None,
            "package_description": package.description if package    else None,
            "amount_paid":         log.amount,
            "currency":            log.currency or "QAR",
            "payment_method":      str(log.paymentMethod),
            "payment_status":      str(log.status),
            "purchased_at":        _iso(log.createdAt) or "",
            # Validity window — sourced from the activation record if present,
            # otherwise the package template duration is used as a fallback.
            "valid_from":          _iso(activation.startDate) if activation else None,
            "valid_until":         _iso(activation.endDate)   if activation else None,
            "duration_days":       (
                activation.durationDays if activation
                else (package.durationDays if package else None)
            ),
            "auto_renew":          auto_renew,
        }

        if include_user:
            user = await prisma.user.find_unique(where={"id": log.userId})
            row["user"] = _serialize_actor(user)

        return row

    @staticmethod
    async def get_my_packages_history(
        user_id: str,
        page: int = 1,
        page_size: int = 20,
        status_filter: Optional[str] = None,
        search: Optional[str] = None,
    ) -> dict:
        """User's own package purchases."""
        where: Dict[str, Any] = {"userId": user_id, "module": "PACKAGE"}

        enum_status = _safe_enum(status_filter, PaymentStatus)
        if enum_status is not None:
            where["status"] = enum_status

        if search:
            where["referenceId"] = {"contains": search, "mode": "insensitive"}

        total = await prisma.paymentlog.count(where=where)
        skip, take = _paginate(page, page_size)

        logs = await prisma.paymentlog.find_many(
            where=where,
            order={"createdAt": "desc"},
            skip=skip,
            take=take,
        )

        items = [await HistoryService._build_package_history_item(l) for l in logs]
        return _envelope(items, total, page, page_size)

    @staticmethod
    async def get_all_packages_history(
        actor_user_id: str,
        page: int = 1,
        page_size: int = 20,
        status_filter: Optional[str] = None,
        search: Optional[str] = None,
    ) -> dict:
        """Admin / Manager view of every package purchase across the app."""
        await _require_role(
            actor_user_id,
            allowed=[UserRole.ADMIN.value, UserRole.MANAGER.value],
        )

        where: Dict[str, Any] = {"module": "PACKAGE"}

        enum_status = _safe_enum(status_filter, PaymentStatus)
        if enum_status is not None:
            where["status"] = enum_status

        if search:
            # Search by reference id or by purchaser identity
            where["OR"] = [
                {"referenceId": {"contains": search, "mode": "insensitive"}},
                {"user": {"is": {"name":  {"contains": search, "mode": "insensitive"}}}},
                {"user": {"is": {"email": {"contains": search, "mode": "insensitive"}}}},
            ]

        total = await prisma.paymentlog.count(where=where)
        skip, take = _paginate(page, page_size)

        logs = await prisma.paymentlog.find_many(
            where=where,
            order={"createdAt": "desc"},
            skip=skip,
            take=take,
        )

        items = [
            await HistoryService._build_package_history_item(l, include_user=True)
            for l in logs
        ]
        return _envelope(items, total, page, page_size)

    # ════════════════════════════════════════════════════════════════════════
    # 4. STORE ORDER HISTORY
    # ════════════════════════════════════════════════════════════════════════

    @staticmethod
    async def _build_order_history_item(
        order: Any, include_user: bool = False,
    ) -> dict:
        """Hydrate an Order with its items + payment status into a history row."""
        # Items: fetched eagerly via include in the caller
        order_items = getattr(order, "items", None) or []

        item_lines: List[dict] = []
        total_qty = 0
        for oi in order_items:
            product = getattr(oi, "product", None)
            item_lines.append({
                "product_id":    oi.productId,
                "product_name":  product.name if product else "Unknown product",
                "product_image": product.thumbnail if product else None,
                "quantity":      oi.quantity,
                "unit_price":    oi.price,
                "line_total":    oi.total,
            })
            total_qty += int(oi.quantity or 0)

        payment_status, ref_id = await _payment_status_for(
            user_id=order.userId, module="ORDER",
            payment_log_id=order.paymentLogId,
        )

        row = {
            "order_id":         order.id,
            "order_number":     order.orderNumber,
            "items":            item_lines,
            "item_count":       total_qty,
            "subtotal":         order.subtotal,
            "discount":         order.discount,
            "tax":              order.tax,
            "total":            order.total,
            "currency":         "QAR",
            "status":           str(order.status),
            "payment_method":   str(order.paymentMethod),
            "payment_status":   payment_status,
            "transaction_id":   order.transactionId,
            "reference_id":     ref_id,
            "paid_at":          _iso(order.paidAt),
            "pickup_note":      order.pickupNote,
            "notes":            order.notes,
            "created_at":       _iso(order.createdAt) or "",
        }

        if include_user:
            user = getattr(order, "user", None)
            row["user"] = _serialize_actor(user)

        return row

    @staticmethod
    async def get_my_orders_history(
        user_id: str,
        page: int = 1,
        page_size: int = 20,
        status_filter: Optional[str] = None,
        search: Optional[str] = None,
    ) -> dict:
        """User's own store orders."""
        where: Dict[str, Any] = {"userId": user_id}

        enum_status = _safe_enum(status_filter, OrderStatus)
        if enum_status is not None:
            where["status"] = enum_status

        if search:
            where["OR"] = [
                {"orderNumber":   {"contains": search, "mode": "insensitive"}},
                {"transactionId": {"contains": search, "mode": "insensitive"}},
                {"notes":         {"contains": search, "mode": "insensitive"}},
            ]

        total = await prisma.order.count(where=where)
        skip, take = _paginate(page, page_size)

        orders = await prisma.order.find_many(
            where=where,
            include={"items": {"include": {"product": True}}},
            order={"createdAt": "desc"},
            skip=skip,
            take=take,
        )

        items = [await HistoryService._build_order_history_item(o) for o in orders]
        return _envelope(items, total, page, page_size)

    @staticmethod
    async def get_all_orders_history(
        actor_user_id: str,
        page: int = 1,
        page_size: int = 20,
        status_filter: Optional[str] = None,
        search: Optional[str] = None,
    ) -> dict:
        """Admin / Manager view of every store order across the app."""
        await _require_role(
            actor_user_id,
            allowed=[UserRole.ADMIN.value, UserRole.MANAGER.value],
        )

        where: Dict[str, Any] = {}

        enum_status = _safe_enum(status_filter, OrderStatus)
        if enum_status is not None:
            where["status"] = enum_status

        if search:
            where["OR"] = [
                {"orderNumber":   {"contains": search, "mode": "insensitive"}},
                {"transactionId": {"contains": search, "mode": "insensitive"}},
                {"notes":         {"contains": search, "mode": "insensitive"}},
                {"user": {"is": {"name":  {"contains": search, "mode": "insensitive"}}}},
                {"user": {"is": {"email": {"contains": search, "mode": "insensitive"}}}},
            ]

        total = await prisma.order.count(where=where)
        skip, take = _paginate(page, page_size)

        orders = await prisma.order.find_many(
            where=where,
            include={
                "items": {"include": {"product": True}},
                "user":  True,
            },
            order={"createdAt": "desc"},
            skip=skip,
            take=take,
        )

        items = [
            await HistoryService._build_order_history_item(o, include_user=True)
            for o in orders
        ]
        return _envelope(items, total, page, page_size)