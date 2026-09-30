"""
app/services/classes_service.py
================================
Service layer for the standalone Classes entity.
Classes have NO relation to Courses — they are fully independent.
"""

from datetime import datetime, timezone
from typing import Optional, List, Dict, Any, Tuple
from app.db.db_client import prisma
from urllib.parse import quote
from app.utils.location_utils import resolve_map_link
from app.models.classes_model import (
    ClassCreate, ClassUpdate, ClassResponse, ClassWithBookings,
    ClassListResponse, ClassSearchParams, SendNotification, CancelClass,
)
from app.core.notification_service import dispatch_push_notification, resolve_channels
from app.core.permissions import can as _policy_can, Resource as _Resource, Action as _Action
from prisma.enums import ClassStatus, BookingStatus, UserRole
from fastapi import HTTPException, status
import pandas as pd
from io import BytesIO


class ClassesService:
    """
    Enterprise-grade service layer for the Classes entity.

    Classes are a standalone entity — they carry their own instructor,
    location, pricing, and schedule data and are NOT children of any Course.
    """

    # ============================================
    # DATE PARSING UTILITY
    # ============================================

    @staticmethod
    def _parse_simple_date(date_str: str) -> tuple[datetime, datetime]:
        """
        Parse a human-friendly date string into a (start_of_day, end_of_day) tuple.

        Supported formats:
            "2-24-2026"   →  M-D-YYYY
            "02-24-2026"  →  MM-DD-YYYY
            "2/24/2026"   →  M/D/YYYY
            "2026-02-24"  →  ISO YYYY-MM-DD
        """
        FORMATS = [
            "%m-%d-%Y",
            "%m/%d/%Y",
            "%Y-%m-%d",
            "%d-%m-%Y",
            "%d/%m/%Y",
        ]
        for fmt in FORMATS:
            try:
                parsed = datetime.strptime(date_str.strip(), fmt)
                start = parsed.replace(hour=0,  minute=0,  second=0,  microsecond=0)
                end   = parsed.replace(hour=23, minute=59, second=59, microsecond=999999)
                return start, end
            except ValueError:
                continue
        raise ValueError(f'Cannot parse date "{date_str}". Use "2-24-2026" or "2026-02-24".')

    # ============================================
    # ROLE-BASED ACCESS CONTROL
    # ============================================

    @staticmethod
    async def check_class_permission(
        user_id: str,
        class_id: Optional[str] = None,
        action: str = "view"
    ) -> bool:
        """
        Delegates to the central RBAC matrix in app.core.permissions.

        Policy summary (resource = "class"):
          ADMIN       : view, create, update, delete
          MANAGER     : view, create, update    (NO delete)
          INSTRUCTOR  : view only                (NO create / update / delete)
          USER        : view only

        `class_id` is preserved for backwards compatibility with existing
        call-sites — ownership checks are not required here because the
        matrix already encodes the full policy.
        """
        user = await prisma.user.find_unique(where={"id": user_id})
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        return _policy_can(str(user.role), _Resource.CLASS, action)

    # ============================================
    # EFFECTIVE PRICE RESOLVER  (user-personalised pricing)
    # ============================================

    @staticmethod
    async def _resolve_effective_price(
        user_id: str,
        class_id: str,
        original_price: float,
        is_free: bool,
    ) -> Tuple[float, str, List[Dict[str, Any]]]:
        """
        Compute the price a specific user sees for a class, plus any package
        instances they could choose to pay with instead.

        Delegates to PackageService.get_effective_class_price — the single
        source of truth for package/membership coverage logic. Imported
        inline to avoid circular imports (PackageService imports nothing
        from ClassesService).

        Returns:
            (effective_price, coverage_type, available_packages)

            effective_price   : 0.0 if free or membership-covered (auto-applies),
                                 else original_price (package coverage does NOT
                                 discount the price — it's an active choice made
                                 at booking time, see booking_payment_service)
            coverage_type     : "package" | "membership" | "free" | "none"
            available_packages: list of {membershipId, packageName,
                                 sessionsRemaining, expiresAt} — only non-empty
                                 when coverage_type == "package"

        Called only for USER-role callers — Admin/Manager/Instructor always
        see the raw price so operational data is never distorted.
        """
        # Universal free rule — no coverage lookup needed
        if is_free or original_price == 0.0:
            return 0.0, "free", []

        # Lazy import — breaks circular dependency chain
        from app.services.package_service import PackageService

        effective_price, is_covered, coverage_type = \
            await PackageService.get_effective_class_price(
                user_id=user_id,
                class_id=class_id,
                original_price=original_price,
            )

        available_packages: List[Dict[str, Any]] = []
        if coverage_type == "package":
            available_packages = await PackageService.get_covering_packages_for_class(
                user_id=user_id, class_id=class_id,
            )

        return effective_price, coverage_type, available_packages

    @staticmethod
    async def _batch_resolve_coverage(
        user_id: str,
        class_ids: List[str],
    ) -> Dict[str, Tuple[float, str, List[Dict[str, Any]]]]:
        """
        Batch-resolve effective pricing for a list of class IDs for one user.

        Performance strategy — ONE DB round-trip, not N:
        ─────────────────────────────────────────────────
        Instead of calling get_effective_class_price() once per class
        (which would issue 3–5 DB queries per class = N×5 queries for a
        page of N classes), we:

          1. Fetch the user's active memberships ONCE.
          2. Fetch the backing Package templates ONCE (one query for all names).
          3. For each class_id, determine coverage using only in-memory data.

        This reduces the cost of pricing a full page (e.g. 10 classes) from
        ~50 DB queries down to ~3, regardless of page size.

        IMPORTANT — package vs membership are intentionally ASYMMETRIC here,
        mirroring PackageService.get_effective_class_price:
          • MEMBERSHIP coverage auto-applies → effective_price = 0.0
          • PACKAGE coverage is surfaced but does NOT change the price —
            the user must actively choose to spend a session at booking time.
            coverage_type is still "package" so the frontend knows to render
            the picker, and the matching package instances are returned in
            the third tuple element so no extra round-trip is needed.

        Returns:
            Dict mapping class_id → (effective_price, coverage_type, available_packages)
            where coverage_type is "package" | "membership" | "free" | "none"
            and available_packages is a list of
            {membershipId, packageName, sessionsRemaining, expiresAt} dicts
            (always [] unless coverage_type == "package").
        """
        from app.services.package_service import _decode_time_restriction

        _now = datetime.now(timezone.utc)
        result: Dict[str, Tuple[float, str, List[Dict[str, Any]]]] = {}

        if not class_ids:
            return result

        # ── Step 1: fetch all classes to get their prices ────────────────────
        classes_raw = await prisma.classes.find_many(
            where={"id": {"in": class_ids}},
        )
        class_price_map: Dict[str, Tuple[float, bool]] = {
            c.id: (c.price, bool(c.isFree)) for c in classes_raw
        }

        # ── Step 2: short-circuit free classes immediately ───────────────────
        remaining_ids: List[str] = []
        for cid in class_ids:
            orig_price, is_free = class_price_map.get(cid, (0.0, False))
            if is_free or orig_price == 0.0:
                result[cid] = (0.0, "free", [])
            else:
                remaining_ids.append(cid)

        if not remaining_ids:
            return result

        # ── Step 3: fetch user's active memberships ONCE ─────────────────────
        active_memberships = await prisma.membership.find_many(
            where={
                "userId": user_id,
                "status": "ACTIVE",
                "endDate": {"gt": _now},
            },
            order={"endDate": "asc"}
        )

        if not active_memberships:
            # No active memberships — all remaining classes at full price
            for cid in remaining_ids:
                orig_price, _ = class_price_map.get(cid, (0.0, False))
                result[cid] = (orig_price, "none", [])
            return result

        # ── Step 4: resolve package-backed memberships + their Package records ─
        # Fetch PaymentLogs for all memberships that have a paymentLogId in one query
        payment_log_ids = [
            m.paymentLogId for m in active_memberships if m.paymentLogId
        ]
        payment_log_map: Dict[str, object] = {}
        if payment_log_ids:
            logs = await prisma.paymentlog.find_many(
                where={"id": {"in": payment_log_ids}}
            )
            payment_log_map = {log.id: log for log in logs}

        # Identify package-backed memberships and fetch their Package templates
        pkg_membership_names: List[str] = []
        for m in active_memberships:
            if m.paymentLogId:
                log = payment_log_map.get(m.paymentLogId)
                if log and str(log.module) == "PACKAGE":
                    pkg_membership_names.append(m.name)

        package_template_map: Dict[str, object] = {}
        if pkg_membership_names:
            packages = await prisma.package.find_many(
                where={"name": {"in": pkg_membership_names}, "isActive": True}
            )
            package_template_map = {p.name: p for p in packages}

        # ── Step 5: per-class coverage resolution (pure in-memory) ──────────
        for cid in remaining_ids:
            orig_price, _ = class_price_map.get(cid, (0.0, False))

            # ── 5a. Check MEMBERSHIP coverage FIRST — it auto-applies (price → 0) ──
            membership_covered = False
            for m in active_memberships:
                # Skip package-backed memberships — handled separately below
                if m.paymentLogId:
                    log = payment_log_map.get(m.paymentLogId)
                    if log and str(log.module) == "PACKAGE":
                        continue

                allowed: List[str] = list(m.allowedClasses) if m.allowedClasses else []
                if allowed and cid not in allowed:
                    continue  # class not in this membership's scope

                membership_covered = True
                break

            if membership_covered:
                result[cid] = (0.0, "membership", [])
                continue

            # ── 5b. Check PACKAGE coverage — surfaced, price unchanged ───────────
            covering_packages: List[Dict[str, Any]] = []
            for m in active_memberships:
                if not m.paymentLogId:
                    continue
                log = payment_log_map.get(m.paymentLogId)
                if not log or str(log.module) != "PACKAGE":
                    continue

                pkg = package_template_map.get(m.name)
                if not pkg:
                    continue

                allowed: List[str] = list(pkg.allowedClasses) if pkg.allowedClasses else []
                if allowed and cid not in allowed:
                    continue  # class excluded from this package

                # Check sessions remaining
                _, total_sessions = _decode_time_restriction(pkg.timeRestriction)
                sessions_remaining: Optional[int] = None
                if total_sessions is not None:
                    sessions_used = int(m.progress or 0)
                    sessions_remaining = max(0, total_sessions - sessions_used)
                    if sessions_remaining <= 0:
                        continue  # exhausted — not a valid option

                covering_packages.append({
                    "membershipId":      m.id,
                    "packageName":       m.name,
                    "sessionsRemaining": sessions_remaining,
                    "expiresAt":         m.endDate.isoformat() if m.endDate else None,
                })

            if covering_packages:
                result[cid] = (orig_price, "package", covering_packages)
            else:
                result[cid] = (orig_price, "none", [])

        return result

    # ============================================
    # CREATE CLASS
    # ============================================

    @staticmethod
    async def create_class(data: ClassCreate, created_by_user_id: str) -> ClassResponse:
        has_permission = await ClassesService.check_class_permission(
            created_by_user_id, action="create"
        )
        if not has_permission:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You don't have permission to create classes",
            )

        # Validate instructor
        instructor = await prisma.user.find_unique(where={"id": data.instructorId})
        if not instructor:
            raise HTTPException(status_code=404, detail="Instructor not found")
        if instructor.role not in [UserRole.INSTRUCTOR, UserRole.ADMIN, UserRole.MANAGER]:
            raise HTTPException(status_code=400, detail="Selected user is not an instructor")

        # Build create payload — connect instructor via relation
        class_data = data.model_dump(exclude_none=True)
        # Auto-generate map link from location if none was provided
        class_data["locationMapLink"] = resolve_map_link(
            class_data["location"], class_data.get("locationMapLink")
        )
        class_data["instructor"] = {"connect": {"id": class_data.pop("instructorId")}}

        new_class = await prisma.classes.create(data=class_data)
        return await ClassesService.get_class_by_id(new_class.id, created_by_user_id)

    # ============================================
    # GET BY ID
    # ============================================

    @staticmethod
    async def get_class_by_id(
        class_id: str,
        user_id: str,
        include_bookings: bool = False,
    ) -> ClassResponse:
        has_permission = await ClassesService.check_class_permission(
            user_id, class_id, action="view"
        )
        if not has_permission:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You don't have permission to view this class",
            )

        class_obj = await prisma.classes.find_unique(
            where={"id": class_id},
            include={"instructor": True, "bookings": include_bookings},
        )
        if not class_obj:
            raise HTTPException(status_code=404, detail="Class not found")

        confirmed_bookings = [
            b for b in (class_obj.bookings or [])
            if b.status in [BookingStatus.CONFIRMED, BookingStatus.ATTENDED]
        ]
        booked_seats = len(confirmed_bookings)

        instructor_info = {
            "id":     class_obj.instructor.id,
            "name":   class_obj.instructor.name,
            "email":  class_obj.instructor.email,
            "avatar": class_obj.instructor.avatar,
        }

        # ── Personalised pricing for USER role ───────────────────────────────
        # Admin/Manager/Instructor always see the raw price — no coverage check.
        # USER callers get effectivePrice showing what they actually pay, plus
        # availablePackages listing any owned package instances they could
        # choose to spend a session from instead.
        user = await prisma.user.find_unique(where={"id": user_id})
        effective_price: Optional[float] = None
        coverage_type: Optional[str] = None
        available_packages: List[Dict[str, Any]] = []

        if user and user.role == UserRole.USER:
            effective_price, coverage_type, available_packages = \
                await ClassesService._resolve_effective_price(
                    user_id=user_id,
                    class_id=class_id,
                    original_price=class_obj.price,
                    is_free=bool(class_obj.isFree),
                )

        response_data = {
            **class_obj.model_dump(),
            "bookedSeats":        booked_seats,
            "instructor":         instructor_info,
            "effectivePrice":     effective_price,
            "coverageType":       coverage_type,
            "availablePackages":  available_packages,
        }

        if include_bookings:
            return ClassWithBookings(**response_data)
        return ClassResponse(**response_data)

    # ============================================
    # GET ALL (with search + filters)
    # ============================================

    @staticmethod
    async def get_all_classes(
        user_id: str,
        params: ClassSearchParams,
    ) -> ClassListResponse:
        where_clause: Dict[str, Any] = {}

        # Role-based scope
        user = await prisma.user.find_unique(where={"id": user_id})
        if user and user.role == UserRole.INSTRUCTOR:
            where_clause["instructorId"] = user_id

        # Full-name-aware search
        if params.search:
            search_parts = params.search.strip().split()
            if len(search_parts) >= 2:
                full = " ".join(search_parts)
                where_clause["OR"] = [
                    {"title": {"contains": params.search, "mode": "insensitive"}},
                    {"instructor": {"is": {"name": {"contains": full, "mode": "insensitive"}}}},
                ]
            else:
                where_clause["OR"] = [
                    {"title": {"contains": params.search, "mode": "insensitive"}},
                    {"instructor": {"is": {"name": {"contains": params.search, "mode": "insensitive"}}}},
                ]

        if params.instructorId:
            where_clause["instructorId"] = params.instructorId
        if params.difficulty:
            where_clause["difficulty"] = params.difficulty
        if params.gender:
            where_clause["gender"] = params.gender
        if params.status:
            where_clause["status"] = params.status
        if params.dateFrom:
            where_clause.setdefault("scheduledAt", {})["gte"] = params.dateFrom
        if params.dateTo:
            where_clause.setdefault("scheduledAt", {})["lte"] = params.dateTo

        # Exact-day filter overrides dateFrom/dateTo
        if params.scheduledAt:
            try:
                day_start, day_end = ClassesService._parse_simple_date(params.scheduledAt)
                where_clause["scheduledAt"] = {"gte": day_start, "lte": day_end}
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc))

        total = await prisma.classes.count(where=where_clause)
        skip = (params.page - 1) * params.pageSize
        total_pages = max(1, (total + params.pageSize - 1) // params.pageSize)

        classes = await prisma.classes.find_many(
            where=where_clause,
            include={"instructor": True, "bookings": True},
            skip=skip,
            take=params.pageSize,
            order={params.sortBy: params.sortOrder},
        )

        # ── Personalised pricing: batch pre-fetch coverage for USER role ──────
        # For USER callers, we fetch their active memberships ONCE here and
        # pass them into a lightweight per-class coverage check — avoiding an
        # N+1 query pattern (one DB hit per class).
        # Admin/Manager/Instructor callers skip this entirely.
        is_user_caller = user and user.role == UserRole.USER
        # class_id → (effective_price, coverage_type, available_packages)
        user_coverage_cache: Dict[str, Tuple[float, str, List[Dict[str, Any]]]] = {}

        if is_user_caller:
            user_coverage_cache = await ClassesService._batch_resolve_coverage(
                user_id=user_id,
                class_ids=[cls.id for cls in classes],
            )

        classes_response = []
        for cls in classes:
            confirmed = [
                b for b in cls.bookings
                if b.status in ["CONFIRMED", "ATTENDED"]
            ]
            booked_seats = len(confirmed)
            instructor_info = {
                "id":     cls.instructor.id,
                "name":   cls.instructor.name,
                "email":  cls.instructor.email,
                "avatar": cls.instructor.avatar,
            }
            cls_dict = cls.model_dump(exclude={"instructor", "bookings"})

            # Attach personalised pricing for USER callers
            effective_price: Optional[float] = None
            coverage_type: Optional[str] = None
            available_packages: List[Dict[str, Any]] = []

            if is_user_caller:
                effective_price, coverage_type, available_packages = \
                    user_coverage_cache.get(cls.id, (cls.price, "none", []))

            classes_response.append(
                ClassResponse(
                    **cls_dict,
                    bookedSeats=booked_seats,
                    instructor=instructor_info,
                    effectivePrice=effective_price,
                    coverageType=coverage_type,
                    availablePackages=available_packages,
                )
            )

        return ClassListResponse(
            classes=classes_response,
            total=total,
            page=params.page,
            pageSize=params.pageSize,
            totalPages=total_pages,
        )

    # ============================================
    # UPDATE CLASS
    # ============================================

    @staticmethod
    async def update_class(
        class_id: str,
        data: ClassUpdate,
        updated_by_user_id: str,
    ) -> ClassResponse:
        has_permission = await ClassesService.check_class_permission(
            updated_by_user_id, class_id, action="edit"
        )
        if not has_permission:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You don't have permission to edit this class",
            )

        cls = await prisma.classes.find_unique(where={"id": class_id})
        if not cls:
            raise HTTPException(status_code=404, detail="Class not found")

        if data.instructorId:
            instructor = await prisma.user.find_unique(where={"id": data.instructorId})
            if not instructor:
                raise HTTPException(status_code=404, detail="Instructor not found")
            if instructor.role not in [UserRole.INSTRUCTOR, UserRole.ADMIN, UserRole.MANAGER]:
                raise HTTPException(status_code=400, detail="Selected user is not an instructor")

        update_data = data.model_dump(exclude_unset=True, exclude_none=True)

        # If location or map link is being updated, re-resolve the map link
        if "location" in update_data or "locationMapLink" in update_data:
            new_location = update_data.get("location", cls.location)
            new_map_link = update_data.get("locationMapLink", None)
            update_data["locationMapLink"] = resolve_map_link(new_location, new_map_link)

        if "instructorId" in update_data:
            update_data["instructor"] = {"connect": {"id": update_data.pop("instructorId")}}

        await prisma.classes.update(where={"id": class_id}, data=update_data)
        return await ClassesService.get_class_by_id(class_id, updated_by_user_id)

    # ============================================
    # DELETE CLASS
    # ============================================

    @staticmethod
    async def delete_class(class_id: str, deleted_by_user_id: str) -> Dict[str, str]:
        has_permission = await ClassesService.check_class_permission(
            deleted_by_user_id, class_id, action="delete"
        )
        if not has_permission:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You don't have permission to delete this class",
            )

        cls = await prisma.classes.find_unique(
            where={"id": class_id}, include={"bookings": True}
        )
        if not cls:
            raise HTTPException(status_code=404, detail="Class not found")

        active_bookings = [
            b for b in cls.bookings
            if b.status in [BookingStatus.CONFIRMED, BookingStatus.PENDING]
        ]
        if active_bookings:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Cannot delete class with {len(active_bookings)} active bookings. "
                    "Please cancel the class instead."
                ),
            )

        await prisma.classes.delete(where={"id": class_id})
        return {"message": "Class deleted successfully"}

    # ============================================
    # CANCEL CLASS
    # ============================================

    @staticmethod
    async def cancel_class(data: CancelClass, cancelled_by_user_id: str) -> Dict[str, Any]:
        has_permission = await ClassesService.check_class_permission(
            cancelled_by_user_id, data.classId, action="cancel"
        )
        if not has_permission:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You don't have permission to cancel this class",
            )

        cls = await prisma.classes.find_unique(
            where={"id": data.classId}, include={"bookings": True}
        )
        if not cls:
            raise HTTPException(status_code=404, detail="Class not found")

        await prisma.classes.update(
            where={"id": data.classId},
            data={
                "status":             ClassStatus.CANCELLED,
                "cancelledAt":        datetime.utcnow(),
                "cancellationReason": data.reason,
            },
        )

        active_bookings = [
            b for b in cls.bookings
            if b.status in [BookingStatus.CONFIRMED, BookingStatus.PENDING]
        ]
        for booking in active_bookings:
            await prisma.booking.update(
                where={"id": booking.id},
                data={"status": BookingStatus.CANCELLED},
            )

        if data.notifyParticipants and active_bookings:
            msg = f"Class '{cls.title}' has been cancelled. Reason: {data.reason}"
            # Collect the user records once, then fan-out concurrently across
            # both channels (in-app + WhatsApp) via the central dispatcher.
            recipients = []
            for booking in active_bookings:
                user = await prisma.user.find_unique(where={"id": booking.userId})
                if user:
                    recipients.append(user)

            if recipients:
                await dispatch_push_notification(
                    users=recipients,
                    title="Class Cancelled",
                    message=msg,
                    notification_type="WARNING",
                    send_via_app=True,
                    send_via_whatsapp=True,
                )

        return {
            "message":           f"Class cancelled. {len(active_bookings)} bookings cancelled.",
            "refundPolicy":      data.refundPolicy,
            "notificationsSent": len(active_bookings) if data.notifyParticipants else 0,
        }

    # ============================================
    # SEND NOTIFICATION
    # ============================================

    @staticmethod
    async def send_notification_to_participants(
        data: SendNotification,
        sent_by_user_id: str,
    ) -> Dict[str, Any]:
        """
        Send a push notification to all confirmed participants of a class.

        Channels are resolved from the request flags:
          • sendViaApp / sendViaWhatsApp / sendViaBoth

        User-level preferences (appNotificationsEnabled,
        whatsappNotificationsEnabled) are respected — participants who have
        opted out of a channel are counted in the skipped totals rather than
        skipped silently.
        """
        has_permission = await ClassesService.check_class_permission(
            sent_by_user_id, data.classId, action="notify"
        )
        if not has_permission:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You don't have permission to notify participants of this class",
            )

        cls = await prisma.classes.find_unique(
            where={"id": data.classId},
            include={
                "bookings": {
                    "where": {"status": {"in": [BookingStatus.CONFIRMED, BookingStatus.ATTENDED]}},
                    "include": {"user": True},
                }
            },
        )
        if not cls:
            raise HTTPException(status_code=404, detail="Class not found")

        # Deduplicate — one user may have multiple confirmed bookings
        confirmed_users = {b.user.id: b.user for b in cls.bookings if b.user}
        if not confirmed_users:
            return {
                "message":           "No confirmed participants to notify",
                "totalParticipants": 0,
                "sentViaApp":        0,
                "sentViaWhatsApp":   0,
                "skippedApp":        0,
                "skippedWhatsApp":   0,
                "failed":            0,
            }

        # Resolve which channels to use
        use_app, use_whatsapp = resolve_channels(
            send_via_app=data.sendViaApp,
            send_via_whatsapp=data.sendViaWhatsApp,
            send_via_both=data.sendViaBoth,
        )

        # Fall back to a descriptive title if the caller did not supply one
        notification_title = data.title or f"Update: {cls.title}"

        result = await dispatch_push_notification(
            users=list(confirmed_users.values()),
            title=notification_title,
            message=data.message,
            notification_type=data.notificationType,
            send_via_app=use_app,
            send_via_whatsapp=use_whatsapp,
        )

        return {
            "message":           "Notifications dispatched successfully",
            "totalParticipants": len(confirmed_users),
            **result,
        }

    # ============================================
    # EXPORT TO EXCEL
    # ============================================

    @staticmethod
    async def export_classes_to_excel(
        user_id: str,
        search: Optional[str] = None,
        instructor_id: Optional[str] = None,
        difficulty: Optional[str] = None,
        gender: Optional[str] = None,
        status_filter: Optional[str] = None,
        date_from: Optional[datetime] = None,
        date_to: Optional[datetime] = None,
    ) -> BytesIO:
        where_clause: Dict[str, Any] = {}

        user = await prisma.user.find_unique(where={"id": user_id})
        if user and user.role == "INSTRUCTOR":
            where_clause["instructorId"] = user_id
        if search:
            where_clause["OR"] = [
                {"title": {"contains": search, "mode": "insensitive"}},
                {"instructor": {"is": {"name": {"contains": search, "mode": "insensitive"}}}},
            ]
        if instructor_id:
            where_clause["instructorId"] = instructor_id
        if difficulty:
            where_clause["difficulty"] = difficulty
        if gender:
            where_clause["gender"] = gender
        if status_filter:
            where_clause["status"] = status_filter
        if date_from:
            where_clause.setdefault("scheduledAt", {})["gte"] = date_from
        if date_to:
            where_clause.setdefault("scheduledAt", {})["lte"] = date_to

        classes = await prisma.classes.find_many(
            where=where_clause,
            include={"instructor": True, "bookings": True},
            order={"scheduledAt": "asc"},
        )

        records = []
        for cls in classes:
            confirmed = [b for b in cls.bookings if b.status in ["CONFIRMED", "ATTENDED"]]
            booked_seats  = len(confirmed)
            avail_seats   = (cls.maxParticipants or 0) - booked_seats
            records.append({
                "Class Title":     cls.title,
                "Instructor":      cls.instructor.name if cls.instructor else "Unknown",
                "Date & Time":     cls.scheduledAt.strftime("%Y-%m-%d %H:%M") if cls.scheduledAt else "",
                "Duration":        cls.duration or "",
                "Difficulty":      cls.difficulty or "",
                "Gender":          cls.gender or "",
                "Location":        cls.location or "",
                "Phone":           cls.phone or "",
                "Price (QAR)":      cls.price,
                "Is Free":          cls.isFree,
                "Max Participants": cls.maxParticipants or "",
                "Booked Seats":    booked_seats,
                "Available Seats": avail_seats,
                "Status":          cls.status,
            })

        df = pd.DataFrame(records)
        output = BytesIO()
        with pd.ExcelWriter(output, engine="openpyxl") as writer:
            df.to_excel(writer, index=False, sheet_name="Classes")
        output.seek(0)
        return output

    # ============================================
    # GET AVAILABLE INSTRUCTORS
    # ============================================

    @staticmethod
    async def get_available_instructors() -> List[Dict[str, Any]]:
        instructors = await prisma.user.find_many(
            where={"role": UserRole.INSTRUCTOR, "isActive": True},
            order={"name": "asc"},
        )
        return [
            {
                "id":     i.id,
                "name":   i.name or "Unknown",
                "email":  i.email,
                "avatar": i.avatar,
                "role":   i.role,
            }
            for i in instructors
        ]

    # ============================================
    # GET CLASS BOOKINGS
    # ============================================

    @staticmethod
    async def get_class_bookings(class_id: str, user_id: str) -> List[Dict[str, Any]]:
        has_permission = await ClassesService.check_class_permission(
            user_id, class_id, action="view"
        )
        if not has_permission:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You don't have permission to view bookings for this class",
            )

        bookings = await prisma.booking.find_many(
            where={"classId": class_id},
            include={"user": True},
        )
        return [
            {
                "id":       b.id,
                "name":     b.user.name or "Unknown",
                "email":    b.user.email,
                "phone":    b.user.phone or "",
                "status":   b.status if isinstance(b.status, str) else b.status.value,
                "bookedAt": b.bookedAt.isoformat(),
            }
            for b in bookings
        ]

    # ============================================
    # GET CONTACT INFO
    # ============================================

    @staticmethod
    async def get_class_contact_info(class_id: str, user_id: str) -> Dict[str, Any]:
        has_permission = await ClassesService.check_class_permission(
            user_id, class_id, action="view"
        )
        if not has_permission:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You don't have permission to view this class",
            )

        cls = await prisma.classes.find_unique(where={"id": class_id})
        if not cls:
            raise HTTPException(status_code=404, detail="Class not found")
        if not cls.phone:
            raise HTTPException(status_code=404, detail="No phone number set for this class")

        clean_phone = cls.phone.replace(" ", "").replace("-", "").replace("(", "").replace(")", "")
        return {
            "phone":          cls.phone,
            "phoneClean":     clean_phone,
            "whatsappLink":   f"https://wa.me/{clean_phone}",
            "callLink":       f"tel:{clean_phone}",
            "location":       cls.location,
            "locationMapLink": cls.locationMapLink,
        }

    # ============================================
    # GET MAP INFO
    # ============================================

    @staticmethod
    async def get_map_info(class_id: str, user_id: str) -> Dict[str, Any]:
        has_permission = await ClassesService.check_class_permission(
            user_id, class_id, action="view"
        )
        if not has_permission:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You don't have permission to view this class",
            )

        cls = await prisma.classes.find_unique(where={"id": class_id})
        if not cls:
            raise HTTPException(status_code=404, detail="Class not found")
        if not cls.location:
            raise HTTPException(status_code=404, detail="Class does not have a location set")

        map_link = cls.locationMapLink or (
            f"https://www.google.com/maps/search/?api=1&query={quote(cls.location)}"
        )
        return {
            "location":       cls.location,
            "locationMapLink": map_link,
            "phone":          cls.phone,
            "hasMapLink":     bool(cls.locationMapLink),
        }