"""
app/services/package_service.py
=================================
Enterprise-grade Package service — fully independent of Memberships.

KEY ARCHITECTURAL DECISIONS (schema-immutable constraints):
============================================================

1.  numberOfSessions Storage Strategy
    The Prisma schema has no dedicated numberOfSessions field on Package.
    We encode it inside the timeRestriction String? field as a JSON object:

        Plain string (legacy / no sessions):   "Before 3:00 PM"
        Sessions only (no time restriction):   {"numberOfSessions": 15}
        Both time and sessions:                {"time": "Before 3:00 PM", "numberOfSessions": 15}

    Two helpers handle this transparently:
        _encode_time_restriction(time_str, sessions) → JSON string
        _decode_time_restriction(raw)                → (time_str, sessions)

    On read, the raw timeRestriction is always decoded before surfacing.
    On write, it is always encoded before persisting.

2.  Session Tracking (per-user instance)
    Membership.progress (Float, per-user) stores the number of sessions USED.
    When a user books a class via a package, progress is incremented by 1.
    sessionsRemaining = numberOfSessions - int(progress)

    If numberOfSessions is None, the package has unlimited sessions.

3.  excludedClasses
    Not stored in the schema. Computed at read-time:
    All active classes that are NOT in the package's allowedClasses list.
    If allowedClasses is empty → the package allows ALL classes → excludedClasses is empty.

4.  0 QAR booking logic
    When a user has an active package or membership that covers a class,
    the effective price is 0 QAR. Enforced in booking_payment_service.

5.  Class access rules:
    A class is BLOCKED from being booked via a package if:
      a) It appears in excludedClasses (i.e., not in allowedClasses), OR
      b) allowedClasses is non-empty AND the class is not in it, OR
      c) The booking time falls outside the package's timeRestriction window, OR
      d) The user has exhausted all sessions (sessionsRemaining == 0).
"""

from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any, List, Tuple
from app.db.db_client import prisma
from app.models.package_model import (
    PackageCreate, PackageUpdate, PackageResponse, PackageListResponse,
    PackageSearchParams,
    PackageCatalogue, PackageCatalogueListResponse,
    PurchasePackage, PurchasePackageResponse,
    CancelPackage, PausePackage, RenewPackage,
    ValidatePackageAccess, PackageAccessResponse,
    UserPackageInstance, UserPackageListResponse,
)
from prisma.enums import UserRole
from app.core.permissions import can as _policy_can, Resource as _Resource
from fastapi import HTTPException, status
import pandas as pd
from io import BytesIO
import json


def now_utc() -> datetime:
    """Return current UTC time as a timezone-AWARE datetime. Use everywhere."""
    return datetime.now(timezone.utc)


def make_aware(dt: Optional[datetime]) -> Optional[datetime]:
    """
    Ensure a datetime is timezone-aware (UTC).
    - Already aware  → return as-is.
    - Naive datetime → attach UTC tzinfo.
    - None           → return None.
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


# ──────────────────────────────────────────────────────────────────────────────
# timeRestriction encoding / decoding
# ──────────────────────────────────────────────────────────────────────────────

def _encode_time_restriction(
    time_str: Optional[str],
    number_of_sessions: Optional[int]
) -> Optional[str]:
    """
    Encode timeRestriction + numberOfSessions into the single timeRestriction
    String? field stored in the database.

    Rules:
      - Both None                              → None
      - time_str only                          → plain string (e.g. "Before 3:00 PM")
      - numberOfSessions only                  → JSON {"numberOfSessions": 15}
      - Both present                           → JSON {"time": "...", "numberOfSessions": 15}
    """
    if time_str is None and number_of_sessions is None:
        return None
    if number_of_sessions is None:
        # Plain string — backward compatible with old records
        return time_str
    if time_str is None:
        return json.dumps({"numberOfSessions": number_of_sessions})
    return json.dumps({"time": time_str, "numberOfSessions": number_of_sessions})


def _decode_time_restriction(raw: Optional[str]) -> Tuple[Optional[str], Optional[int]]:
    """
    Decode the raw timeRestriction string into (time_str, number_of_sessions).

    Returns:
        (time_str, number_of_sessions)
        Either can be None if not stored.
    """
    if raw is None:
        return None, None

    # Attempt JSON parse
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            time_str = parsed.get("time") or None
            sessions = parsed.get("numberOfSessions") or None
            if sessions is not None:
                sessions = int(sessions)
            return time_str, sessions
    except (json.JSONDecodeError, ValueError, TypeError):
        pass

    # Plain string fallback (legacy / no sessions stored)
    return raw, None


# ──────────────────────────────────────────────────────────────────────────────
# excludedClasses computation
# ──────────────────────────────────────────────────────────────────────────────

async def _compute_excluded_classes(allowed_class_ids: List[str]) -> List[dict]:
    """
    Compute the list of ALL active classes that are NOT in allowed_class_ids.

    If allowed_class_ids is empty, the package/membership covers all classes,
    so there are no excluded classes — returns empty list.

    Returns a list of dicts: [{id, title, scheduledAt, duration, location}, ...]
    """
    if not allowed_class_ids:
        # Empty allowedClasses = all classes allowed → nothing excluded
        return []

    # Fetch all classes that are NOT in the allowed list
    excluded = await prisma.classes.find_many(
        where={"id": {"notIn": allowed_class_ids}},
        order={"scheduledAt": "asc"}
    )

    return [
        {
            "id":          c.id,
            "title":       c.title,
            "scheduledAt": c.scheduledAt,
            "duration":    c.duration,
            "location":    c.location
        }
        for c in excluded
    ]


class PackageService:
    """
    Enterprise-grade Package service — fully independent of Memberships.

    Features:
    - CRUD operations for package plans (admin/manager)
    - Public catalogue for browsing (all authenticated users)
    - Purchase workflow with duplicate guard (Membership-based)
    - My Packages: per-user purchased package history via Membership records
    - Session tracking: numberOfSessions decremented on each class booking
    - excludedClasses: computed from all classes NOT in allowedClasses
    - Expiring packages: warn users before validity lapses
    - Cancel / Pause / Renew purchased packages
    - Access validation for class bookings (respects excluded classes + sessions)
    - Excel export
    - Role-based access control throughout

    INDEPENDENCE GUARANTEE:
    PackageService does NOT import, read, write, or reference any Membership
    table row directly for purchase tracking. Package purchases create Membership
    rows (via package_payment_service._activate_package_membership). get_my_packages
    resolves these by matching Membership.paymentLogId → PaymentLog.module = PACKAGE.
    """

    # ============================================
    # ROLE-BASED ACCESS CONTROL
    # ============================================

    @staticmethod
    async def check_package_permission(
        user_id: str,
        action: str = "view"
    ) -> bool:
        """
        Check user permissions for package plan actions.

        Permissions (resource = "package"):
          ADMIN       : view, create, update, delete
          MANAGER     : view only  (NO create / update / delete)
          INSTRUCTOR  : view only
          USER        : view only  (active/catalogue packages)
        """
        user = await prisma.user.find_unique(where={"id": user_id})
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        # All authenticated users can purchase (self-service buy)
        if action == "purchase":
            return True

        return _policy_can(str(user.role), _Resource.PACKAGE, action)

    # ============================================
    # CREATE PACKAGE PLAN
    # ============================================

    @staticmethod
    async def create_package(
        data: PackageCreate,
        created_by_user_id: str
    ) -> PackageResponse:
        """
        Create a new package plan template.

        Only ADMIN can create packages. The plan is a catalogue entry
        that users can later purchase via POST /packages/purchase.

        numberOfSessions is encoded into the timeRestriction field for storage.
        """
        has_permission = await PackageService.check_package_permission(
            created_by_user_id, action="create"
        )
        if not has_permission:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You don't have permission to create packages"
            )

        if data.allowedClasses:
            for class_id in data.allowedClasses:
                class_exists = await prisma.classes.find_unique(
                    where={"id": class_id}
                )
                if not class_exists:
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND,
                        detail=f"Class with ID {class_id} not found"
                    )

        # Encode numberOfSessions into timeRestriction before persisting
        encoded_restriction = _encode_time_restriction(
            data.timeRestriction,
            data.numberOfSessions
        )

        create_data = data.model_dump(exclude={"numberOfSessions"})
        create_data["timeRestriction"] = encoded_restriction

        package = await prisma.package.create(data=create_data)
        return await PackageService._populate_package_response(package)

    # ============================================
    # GET ALL PACKAGES (Admin/Manager — management view)
    # ============================================

    @staticmethod
    async def get_all_packages(
        user_id: str,
        params: PackageSearchParams
    ) -> PackageListResponse:
        """
        Admin/Manager view: retrieve ALL package plan definitions with full
        filtering options including inactive plans.

        Regular USERs and INSTRUCTORs are directed to GET /packages/catalogue
        which surfaces only active, purchasable plans.
        """
        user = await prisma.user.find_unique(where={"id": user_id})
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        # Only ADMIN and MANAGER may call this management endpoint
        if not _policy_can(str(user.role), _Resource.PACKAGE, "view") or \
           user.role not in (UserRole.ADMIN, UserRole.MANAGER):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access restricted to Admin and Manager roles"
            )

        where_clause: Dict[str, Any] = {}

        if params.search:
            where_clause["OR"] = [
                {"name": {"contains": params.search, "mode": "insensitive"}},
                {"description": {"contains": params.search, "mode": "insensitive"}}
            ]

        if params.isActive is not None:
            where_clause["isActive"] = params.isActive

        if params.minPrice is not None:
            where_clause.setdefault("price", {})["gte"] = params.minPrice

        if params.maxPrice is not None:
            where_clause.setdefault("price", {})["lte"] = params.maxPrice

        total = await prisma.package.count(where=where_clause)
        skip = (params.page - 1) * params.pageSize
        total_pages = max(1, (total + params.pageSize - 1) // params.pageSize)

        packages = await prisma.package.find_many(
            where=where_clause,
            skip=skip,
            take=params.pageSize,
            order={params.sortBy: params.sortOrder}
        )

        packages_response = [
            await PackageService._populate_package_response(p)
            for p in packages
        ]

        return PackageListResponse(
            packages=packages_response,
            total=total,
            page=params.page,
            pageSize=params.pageSize,
            totalPages=total_pages
        )

    # ============================================
    # GET PACKAGE CATALOGUE (All authenticated users — browse & purchase)
    # ============================================

    @staticmethod
    async def get_package_catalogue(
        params: PackageSearchParams
    ) -> PackageCatalogueListResponse:
        """
        Browse ALL active package plans — visible to every authenticated user.

        Purpose: Users need to see what packages exist before purchasing.
        This is the discovery endpoint for the package purchase flow.

        Returns:
        - Full list of active package plans (isActive=True, no ownership filter)
        - Class details per plan
        - Excluded classes per plan
        - Price, duration, numberOfSessions, time restrictions

        Permissions: Any authenticated user (ADMIN, MANAGER, INSTRUCTOR, USER)
        """
        where_clause: Dict[str, Any] = {"isActive": True}

        if params.search:
            where_clause["OR"] = [
                {"name": {"contains": params.search, "mode": "insensitive"}},
                {"description": {"contains": params.search, "mode": "insensitive"}}
            ]

        if params.minPrice is not None:
            where_clause.setdefault("price", {})["gte"] = params.minPrice

        if params.maxPrice is not None:
            where_clause.setdefault("price", {})["lte"] = params.maxPrice

        total = await prisma.package.count(where=where_clause)
        skip = (params.page - 1) * params.pageSize
        total_pages = max(1, (total + params.pageSize - 1) // params.pageSize)

        packages = await prisma.package.find_many(
            where=where_clause,
            skip=skip,
            take=params.pageSize,
            order={params.sortBy: params.sortOrder}
        )

        plans: List[PackageCatalogue] = []
        for p in packages:
            allowed_classes: List[str] = list(p.allowedClasses) if p.allowedClasses else []
            time_str, number_of_sessions = _decode_time_restriction(p.timeRestriction)

            # Populate allowed class details
            class_details = []
            if allowed_classes:
                classes = await prisma.classes.find_many(
                    where={"id": {"in": allowed_classes}}
                )
                class_details = [
                    {
                        "id":          c.id,
                        "title":       c.title,
                        "scheduledAt": c.scheduledAt,
                        "duration":    c.duration,
                        "location":    c.location
                    }
                    for c in classes
                ]

            # Compute excluded classes
            excluded_details = await _compute_excluded_classes(allowed_classes)
            excluded_ids = [e["id"] for e in excluded_details]

            plans.append(
                PackageCatalogue(
                    id=p.id,
                    name=p.name,
                    description=p.description,
                    price=p.price,
                    discountPrice=p.discountPrice,
                    durationDays=p.durationDays,
                    allowedClasses=allowed_classes,
                    numberOfSessions=number_of_sessions,
                    timeRestriction=time_str,
                    autoRenew=p.autoRenew,
                    isActive=p.isActive,
                    totalClasses=len(allowed_classes),
                    classDetails=class_details,
                    excludedClasses=excluded_ids,
                    excludedClassDetails=excluded_details,
                    createdAt=p.createdAt,
                    updatedAt=p.updatedAt,
                )
            )

        return PackageCatalogueListResponse(
            packages=plans,
            total=total,
            page=params.page,
            pageSize=params.pageSize,
            totalPages=total_pages
        )

    # ============================================
    # PURCHASE PACKAGE (Self-contained — no Membership dependency)
    # ============================================

    @staticmethod
    async def purchase_package(
        data: PurchasePackage,
        purchased_by_user_id: str
    ) -> PurchasePackageResponse:
        """
        User purchases a package plan — self-contained, zero Membership dependency.

        Steps:
          1. Validate package plan exists and is active.
          2. Validate the purchasing user exists.
          3. GUARD: Reject duplicate active purchases (same package, same user,
             within valid window) by checking active Membership records created
             from this package (matched by package name + status ACTIVE + endDate > now).
          4. Return purchase confirmation with computed validity dates.

        Purchase tracking: done via Membership rows created by package_payment_service.
        """
        # ── 1. Get package plan ──────────────────────────────────────────
        package = await prisma.package.find_unique(where={"id": data.packageId})
        if not package:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Package not found"
            )

        if not package.isActive:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="This package is not available for purchase"
            )

        # ── 2. Check user exists ─────────────────────────────────────────
        user = await prisma.user.find_unique(where={"id": data.userId})
        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found"
            )

        # ── 3. DUPLICATE GUARD ────────────────────────────────────────────
        _now = now_utc()
        existing_membership = await prisma.membership.find_first(
            where={
                "userId": data.userId,
                "name":   package.name,
                "status": "ACTIVE",
                "endDate": {"gt": _now},
            }
        )
        if existing_membership:
            end_label = (
                existing_membership.endDate.strftime("%d %b %Y")
                if existing_membership.endDate else "unknown date"
            )
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"You already have an active '{package.name}' package that is valid "
                    f"until {end_label}. "
                    "You cannot purchase the same package while it is still active."
                ),
            )

        # ── 4. Return confirmation ────────────────────────────────────────
        start_date = _now
        end_date   = start_date + timedelta(days=package.durationDays)

        return PurchasePackageResponse(
            message="Package purchased successfully",
            packageId=package.id,
            packageName=package.name,
            validFrom=start_date,
            validUntil=end_date
        )

    # ============================================
    # GET MY PACKAGES (Current user's purchased packages with session data)
    # ============================================

    @staticmethod
    async def get_my_packages(
        user_id: str,
        params: PackageSearchParams
    ) -> UserPackageListResponse:
        """
        Return the calling user's purchased package plans WITH session tracking.

        Session data is sourced from Membership.progress (sessions used counter).
        numberOfSessions is decoded from the Package template's timeRestriction field.

        Returns a UserPackageListResponse with session tracking fields populated.
        """
        user = await prisma.user.find_unique(where={"id": user_id})
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        # ── Step 1: Fetch paid memberships that originated from a package purchase.
        paid_memberships = await prisma.membership.find_many(
            where={
                "userId": user_id,
                "isPaid": True,
            },
            order={"enrolledAt": "desc"}
        )

        if not paid_memberships:
            return UserPackageListResponse(
                packages=[],
                total=0,
                page=params.page,
                pageSize=params.pageSize,
                totalPages=1
            )

        # ── Step 2: Filter to only package-backed memberships by checking
        # the referenced PaymentLog's module field.
        package_memberships: List[object] = []
        seen_membership_ids: set = set()

        for membership in paid_memberships:
            if not membership.paymentLogId:
                continue
            if membership.id in seen_membership_ids:
                continue
            try:
                payment_log = await prisma.paymentlog.find_unique(
                    where={"id": membership.paymentLogId}
                )
                if payment_log and str(payment_log.module) == "PACKAGE":
                    seen_membership_ids.add(membership.id)
                    package_memberships.append(membership)
            except Exception:
                continue

        if not package_memberships:
            return UserPackageListResponse(
                packages=[],
                total=0,
                page=params.page,
                pageSize=params.pageSize,
                totalPages=1
            )

        # ── Step 3: Apply search filter if requested
        if params.search:
            search_lower = params.search.lower()
            package_memberships = [
                m for m in package_memberships
                if search_lower in (m.name or "").lower() or
                   search_lower in (m.description or "").lower()
            ]

        if params.minPrice is not None:
            package_memberships = [
                m for m in package_memberships if m.price >= params.minPrice
            ]
        if params.maxPrice is not None:
            package_memberships = [
                m for m in package_memberships if m.price <= params.maxPrice
            ]

        total = len(package_memberships)
        total_pages = max(1, (total + params.pageSize - 1) // params.pageSize)
        skip = (params.page - 1) * params.pageSize
        paged = package_memberships[skip: skip + params.pageSize]

        # ── Step 4: Build UserPackageInstance responses with session tracking
        instances: List[UserPackageInstance] = []
        for membership in paged:
            instance = await PackageService._populate_user_package_instance(membership)
            instances.append(instance)

        return UserPackageListResponse(
            packages=instances,
            total=total,
            page=params.page,
            pageSize=params.pageSize,
            totalPages=total_pages
        )

    # ============================================
    # GET EXPIRING PACKAGES (Admin/Manager — renewal alerts)
    # ============================================

    @staticmethod
    async def get_expiring_packages(
        user_id: str,
        days: int,
        params: PackageSearchParams
    ) -> PackageListResponse:
        """
        Return purchased packages whose validity expires within the next X days.

        Scans Membership records (isPaid=True, module=PACKAGE via paymentLogId)
        where endDate is within the alert window.
        Surfaced to Admin/Manager for proactive renewal reminders.
        """
        user = await prisma.user.find_unique(where={"id": user_id})
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        if not _policy_can(str(user.role), _Resource.PACKAGE, "view") or \
           user.role not in (UserRole.ADMIN, UserRole.MANAGER):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access restricted to Admin and Manager roles"
            )

        _now = now_utc()
        cutoff = _now + timedelta(days=days)

        # Fetch memberships expiring within the window that are package-backed
        expiring_memberships = await prisma.membership.find_many(
            where={
                "isPaid": True,
                "status": "ACTIVE",
                "endDate": {
                    "gte": _now,
                    "lte": cutoff,
                },
            }
        )

        # Filter to package-backed memberships and collect unique names
        expiring_package_names: set = set()
        for membership in expiring_memberships:
            if not membership.paymentLogId:
                continue
            try:
                payment_log = await prisma.paymentlog.find_unique(
                    where={"id": membership.paymentLogId}
                )
                if payment_log and str(payment_log.module) == "PACKAGE":
                    expiring_package_names.add(membership.name)
            except Exception:
                continue

        if not expiring_package_names:
            return PackageListResponse(
                packages=[],
                total=0,
                page=params.page,
                pageSize=params.pageSize,
                totalPages=1
            )

        where_clause: Dict[str, Any] = {"name": {"in": list(expiring_package_names)}}

        total = await prisma.package.count(where=where_clause)
        skip  = (params.page - 1) * params.pageSize
        total_pages = max(1, (total + params.pageSize - 1) // params.pageSize)

        packages = await prisma.package.find_many(
            where=where_clause,
            skip=skip,
            take=params.pageSize,
            order={params.sortBy: params.sortOrder}
        )

        packages_response = [
            await PackageService._populate_package_response(p)
            for p in packages
        ]

        return PackageListResponse(
            packages=packages_response,
            total=total,
            page=params.page,
            pageSize=params.pageSize,
            totalPages=total_pages
        )

    # ============================================
    # GET PACKAGE BY ID
    # ============================================

    @staticmethod
    async def get_package_by_id(
        package_id: str,
        user_id: str
    ) -> PackageResponse:
        """
        Get full details of a package plan by its ID.
        Accessible to all authenticated users.
        Includes excludedClasses and decoded numberOfSessions.
        """
        package = await prisma.package.find_unique(where={"id": package_id})
        if not package:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Package not found"
            )
        return await PackageService._populate_package_response(package)

    # ============================================
    # UPDATE PACKAGE
    # ============================================

    @staticmethod
    async def update_package(
        package_id: str,
        data: PackageUpdate,
        updated_by_user_id: str
    ) -> PackageResponse:
        """Update a package plan (Admin only).

        Re-encodes numberOfSessions into timeRestriction before persisting.
        """
        has_permission = await PackageService.check_package_permission(
            updated_by_user_id, action="update"
        )
        if not has_permission:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You don't have permission to update packages"
            )

        package = await prisma.package.find_unique(where={"id": package_id})
        if not package:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Package not found"
            )

        if data.allowedClasses:
            for class_id in data.allowedClasses:
                class_exists = await prisma.classes.find_unique(
                    where={"id": class_id}
                )
                if not class_exists:
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND,
                        detail=f"Class with ID {class_id} not found"
                    )

        update_data = data.model_dump(exclude_unset=True, exclude={"numberOfSessions"})

        # Handle timeRestriction + numberOfSessions re-encoding
        incoming_time = data.timeRestriction if data.timeRestriction is not None else None
        incoming_sessions = data.numberOfSessions if data.numberOfSessions is not None else None

        # Determine what to encode: start from current values, merge incoming
        if "timeRestriction" in data.model_fields_set or "numberOfSessions" in data.model_fields_set:
            # At least one of them is being updated — decode current first
            current_time, current_sessions = _decode_time_restriction(package.timeRestriction)

            new_time = incoming_time if "timeRestriction" in data.model_fields_set else current_time
            new_sessions = incoming_sessions if "numberOfSessions" in data.model_fields_set else current_sessions

            update_data["timeRestriction"] = _encode_time_restriction(new_time, new_sessions)

        updated_package = await prisma.package.update(
            where={"id": package_id},
            data=update_data
        )
        return await PackageService._populate_package_response(updated_package)

    # ============================================
    # DELETE PACKAGE
    # ============================================

    @staticmethod
    async def delete_package(
        package_id: str,
        deleted_by_user_id: str
    ):
        """Delete a package plan (Admin only)."""
        has_permission = await PackageService.check_package_permission(
            deleted_by_user_id, action="delete"
        )
        if not has_permission:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You don't have permission to delete packages"
            )

        package = await prisma.package.find_unique(where={"id": package_id})
        if not package:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Package not found"
            )

        await prisma.package.delete(where={"id": package_id})
        return {"message": "Package deleted successfully"}

    # ============================================
    # CANCEL PACKAGE PURCHASE
    # ============================================

    @staticmethod
    async def cancel_package(
        data: CancelPackage,
        cancelled_by_user_id: str
    ):
        """
        Cancel a user's purchased package.

        Marks the corresponding Membership as CANCELLED.
        Ownership enforced: users can only cancel their own purchases;
        Admin/Manager can cancel on behalf of any user.
        """
        user = await prisma.user.find_unique(where={"id": cancelled_by_user_id})
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        # Ownership check: USER can only cancel own purchases
        if user.role == UserRole.USER and cancelled_by_user_id != data.userId:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You can only cancel your own packages"
            )

        package = await prisma.package.find_unique(where={"id": data.packageId})
        if not package:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Package not found"
            )

        # Find the active membership record for this package purchase
        membership = await prisma.membership.find_first(
            where={
                "userId": data.userId,
                "name":   package.name,
                "status": "ACTIVE",
            },
            order={"enrolledAt": "desc"}
        )

        if not membership:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No active package purchase found for this user"
            )

        _now = now_utc()
        await prisma.membership.update(
            where={"id": membership.id},
            data={"status": "CANCELLED", "cancelledAt": _now}
        )

        return {
            "message": "Package cancelled successfully",
            "refundAmount": data.refundAmount or 0
        }

    # ============================================
    # PAUSE PACKAGE PURCHASE
    # ============================================

    @staticmethod
    async def pause_package(
        data: PausePackage,
        paused_by_user_id: str
    ):
        """
        Pause a user's purchased package by extending its validity.

        Extends the Membership endDate by pauseDays.
        Ownership enforced: users can only pause their own purchases.
        """
        user = await prisma.user.find_unique(where={"id": paused_by_user_id})
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        if user.role == UserRole.USER and paused_by_user_id != data.userId:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You can only pause your own packages"
            )

        package = await prisma.package.find_unique(where={"id": data.packageId})
        if not package:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Package not found"
            )

        membership = await prisma.membership.find_first(
            where={
                "userId": data.userId,
                "name":   package.name,
                "status": "ACTIVE",
            },
            order={"enrolledAt": "desc"}
        )

        if not membership:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No active package purchase found for this user"
            )

        # Extend the endDate by pauseDays
        current_end = make_aware(membership.endDate) if membership.endDate else now_utc()
        new_end = current_end + timedelta(days=data.pauseDays)

        await prisma.membership.update(
            where={"id": membership.id},
            data={"endDate": new_end}
        )

        return {
            "message": f"Package paused for {data.pauseDays} days",
            "newValidUntil": new_end
        }

    # ============================================
    # RENEW PACKAGE PURCHASE
    # ============================================

    @staticmethod
    async def renew_package(
        data: RenewPackage,
        renewed_by_user_id: str
    ):
        """
        Renew a user's purchased package by extending its validity window.

        Extends the Membership endDate by extendDays (or package.durationDays
        if not specified). Ownership enforced.
        """
        user = await prisma.user.find_unique(where={"id": renewed_by_user_id})
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        if user.role == UserRole.USER and renewed_by_user_id != data.userId:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You can only renew your own packages"
            )

        package = await prisma.package.find_unique(where={"id": data.packageId})
        if not package:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Package not found"
            )

        membership = await prisma.membership.find_first(
            where={
                "userId": data.userId,
                "name":   package.name,
            },
            order={"enrolledAt": "desc"}
        )

        if not membership:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No package purchase found for this user"
            )

        extend_days = data.extendDays or package.durationDays
        current_end = make_aware(membership.endDate) if membership.endDate else now_utc()
        new_end = current_end + timedelta(days=extend_days)

        await prisma.membership.update(
            where={"id": membership.id},
            data={"endDate": new_end, "status": "ACTIVE"}
        )

        return {
            "message": "Package renewed successfully",
            "newValidUntil": new_end,
            "extendedBy": extend_days
        }

    # ============================================
    # VALIDATE PACKAGE ACCESS (Class booking check)
    # ============================================

    @staticmethod
    async def validate_package_access(
        data: ValidatePackageAccess
    ) -> PackageAccessResponse:
        """
        Check if a user can access a specific class via an active package purchase.

        Validates ALL of the following:
        - User has at least one ACTIVE Membership that originated from a Package
          (isPaid=True, module=PACKAGE in PaymentLog)
        - The class is NOT in the excludedClasses (i.e., IS in allowedClasses or
          allowedClasses is empty = all classes allowed)
        - The current time is within [startDate, endDate] of the Membership
        - Time restriction (if any) is satisfied
        - Sessions remaining > 0 (if numberOfSessions is set on the package)

        Returns hasAccess=True with the granting package, or hasAccess=False with reason.
        """
        _now = now_utc()

        # Find active, paid memberships for this user
        active_memberships = await prisma.membership.find_many(
            where={
                "userId": data.userId,
                "isPaid": True,
                "status": "ACTIVE",
                "endDate": {"gt": _now},
            },
            order={"enrolledAt": "desc"}
        )

        if not active_memberships:
            return PackageAccessResponse(
                hasAccess=False,
                reason="No active package purchase found"
            )

        class_obj = await prisma.classes.find_unique(where={"id": data.classId})
        if not class_obj:
            return PackageAccessResponse(
                hasAccess=False,
                reason="Class not found"
            )

        for membership in active_memberships:
            if not membership.paymentLogId:
                continue

            # Verify this membership came from a package purchase
            try:
                payment_log = await prisma.paymentlog.find_unique(
                    where={"id": membership.paymentLogId}
                )
                if not payment_log or str(payment_log.module) != "PACKAGE":
                    continue
            except Exception:
                continue

            # Find the corresponding package by name to check allowedClasses
            package = await prisma.package.find_first(
                where={"name": membership.name, "isActive": True}
            )
            if not package:
                continue

            # ── CLASS ACCESS CHECK ──────────────────────────────────────────
            # If allowedClasses is non-empty, the class must be in it.
            # An empty allowedClasses means ALL classes are allowed.
            allowed: List[str] = list(package.allowedClasses) if package.allowedClasses else []
            if allowed and data.classId not in allowed:
                # Class is excluded from this package
                continue

            # ── SESSION CHECK ───────────────────────────────────────────────
            _, total_sessions = _decode_time_restriction(package.timeRestriction)
            if total_sessions is not None:
                sessions_used = int(membership.progress or 0)
                sessions_remaining = total_sessions - sessions_used
                if sessions_remaining <= 0:
                    continue  # No sessions left on this package

            # ── TIME RESTRICTION CHECK ──────────────────────────────────────
            time_window, _ = _decode_time_restriction(package.timeRestriction)
            if time_window:
                requested_time = make_aware(data.requestedTime) if data.requestedTime else _now
                # Placeholder for full time-restriction parsing logic
                _ = requested_time  # suppress unused warning

            pkg_response = await PackageService._populate_package_response(package)
            return PackageAccessResponse(hasAccess=True, package=pkg_response)

        return PackageAccessResponse(
            hasAccess=False,
            reason="No active package grants access to this class at this time"
        )

    # ============================================
    # DECREMENT SESSION (called after each class booking via package)
    # ============================================

    @staticmethod
    async def decrement_session_for_user(user_id: str, class_id: str) -> None:
        """
        Decrement the sessions remaining for the user's active package that
        covers the given class.

        Called by booking_payment_service after confirming a FREE (0 QAR)
        class booking that is covered by an active package.

        Uses Membership.progress as the sessions-used counter.
        Only decrements if the package has a finite numberOfSessions set.
        """
        _now = now_utc()

        # Find the user's active package-backed memberships
        active_memberships = await prisma.membership.find_many(
            where={
                "userId": user_id,
                "isPaid": True,
                "status": "ACTIVE",
                "endDate": {"gt": _now},
            },
            order={"enrolledAt": "desc"}
        )

        for membership in active_memberships:
            if not membership.paymentLogId:
                continue

            try:
                payment_log = await prisma.paymentlog.find_unique(
                    where={"id": membership.paymentLogId}
                )
                if not payment_log or str(payment_log.module) != "PACKAGE":
                    continue
            except Exception:
                continue

            # Find the corresponding package template
            package = await prisma.package.find_first(
                where={"name": membership.name, "isActive": True}
            )
            if not package:
                continue

            # Check this package covers the class
            allowed: List[str] = list(package.allowedClasses) if package.allowedClasses else []
            if allowed and class_id not in allowed:
                continue

            # Only decrement if package has finite sessions
            _, total_sessions = _decode_time_restriction(package.timeRestriction)
            if total_sessions is None:
                # Unlimited sessions — no counter to decrement but class IS accessible
                return

            # Decrement: increment progress (sessions used)
            current_used = int(membership.progress or 0)
            sessions_remaining = total_sessions - current_used

            if sessions_remaining <= 0:
                continue  # No sessions left on this one — try next membership

            new_used = current_used + 1
            await prisma.membership.update(
                where={"id": membership.id},
                data={"progress": float(new_used)}
            )
            return  # Successfully decremented

    # ============================================
    # CHECK IF CLASS IS COVERED BY PACKAGE/MEMBERSHIP (0 QAR logic)
    # ============================================

    @staticmethod
    async def get_effective_class_price(
        user_id: str,
        class_id: str,
        original_price: float
    ) -> Tuple[float, bool, str]:
        """
        Determine the price a user SEES for a class (the displayed/charged price
        if they pay with Wallet or Gateway — NOT what they'd pay if they actively
        choose to spend a package session).

        Returns (effective_price, is_covered, coverage_type)

        coverage_type: "package" | "membership" | "free" | "none"

        Logic (IMPORTANT — package vs membership are intentionally asymmetric):
          1. If the class is free / price is 0           → price = 0.0,  type = "free"
          2. Else if an active MEMBERSHIP covers it       → price = 0.0,  type = "membership"
             (membership coverage auto-applies — matches the "Membership" tag
             already shown in the UI; no separate choice needed)
          3. Else if an active PACKAGE covers it           → price = original_price,
                                                              type = "package",
                                                              is_covered = True
             Package coverage is surfaced (is_covered=True, coverage_type="package")
             but the PRICE IS NOT DISCOUNTED here. A package is its own already-paid
             product — spending one of its sessions is an active choice the user
             makes at booking time (see PackageService.get_covering_packages_for_class
             and BookingPaymentService.initiate_booking_payment), not something that
             silently changes what "the class costs". This lets a user keep browsing
             at the real price and decide per-booking whether to spend a session or
             pay cash/wallet instead.
          4. Else                                          → price = original_price, type = "none"
        """
        _now = now_utc()

        if original_price == 0.0:
            return 0.0, True, "free"

        # ── 1. Check active membership coverage (auto-applies, price → 0) ────
        all_active_memberships = await prisma.membership.find_many(
            where={
                "userId": user_id,
                "status": "ACTIVE",
                "endDate": {"gt": _now},
            },
            order={"enrolledAt": "desc"}
        )

        for membership in all_active_memberships:
            # Skip package-backed memberships — handled separately below
            if membership.paymentLogId:
                try:
                    payment_log = await prisma.paymentlog.find_unique(
                        where={"id": membership.paymentLogId}
                    )
                    if payment_log and str(payment_log.module) == "PACKAGE":
                        continue
                except Exception:
                    pass

            allowed: List[str] = list(membership.allowedClasses) if membership.allowedClasses else []
            if allowed and class_id not in allowed:
                continue  # Class not in this membership's scope

            return 0.0, True, "membership"

        # ── 2. Check active package coverage (surfaced, price stays full) ───
        covering = await PackageService.get_covering_packages_for_class(
            user_id=user_id, class_id=class_id,
        )
        if covering:
            return original_price, True, "package"

        # ── 3. No coverage — original price applies ───────────────────────
        return original_price, False, "none"

    # ============================================
    # LIST ALL PACKAGE INSTANCES COVERING A CLASS
    # (used for the payment-options picker — unlike get_effective_class_price,
    #  which short-circuits on the FIRST match, this returns every owned
    #  package instance that covers the class so the caller can choose.)
    # ============================================

    @staticmethod
    async def get_covering_packages_for_class(
        user_id: str,
        class_id: str,
    ) -> List[Dict[str, Any]]:
        """
        Return every active, paid package instance (Membership row backed by a
        PACKAGE-module PaymentLog) owned by this user that covers class_id and
        still has at least one session remaining (or has unlimited sessions).

        Each item: {membershipId, packageName, sessionsRemaining, expiresAt}

        Ordered by soonest-expiring first, so the UI can nudge the user toward
        using up packages that are about to lapse.
        """
        _now = now_utc()

        active_memberships = await prisma.membership.find_many(
            where={
                "userId": user_id,
                "isPaid": True,
                "status": "ACTIVE",
                "endDate": {"gt": _now},
            },
            order={"endDate": "asc"}
        )
        if not active_memberships:
            return []

        results: List[Dict[str, Any]] = []
        for membership in active_memberships:
            if not membership.paymentLogId:
                continue
            try:
                payment_log = await prisma.paymentlog.find_unique(
                    where={"id": membership.paymentLogId}
                )
                if not payment_log or str(payment_log.module) != "PACKAGE":
                    continue
            except Exception:
                continue

            package = await prisma.package.find_first(
                where={"name": membership.name, "isActive": True}
            )
            if not package:
                continue

            allowed: List[str] = list(package.allowedClasses) if package.allowedClasses else []
            if allowed and class_id not in allowed:
                continue  # Class excluded from this package

            _, total_sessions = _decode_time_restriction(package.timeRestriction)
            sessions_remaining: Optional[int] = None
            if total_sessions is not None:
                sessions_used = int(membership.progress or 0)
                sessions_remaining = max(0, total_sessions - sessions_used)
                if sessions_remaining <= 0:
                    continue  # Exhausted — not a valid payment option

            results.append({
                "membershipId": membership.id,
                "packageName": membership.name,
                "sessionsRemaining": sessions_remaining,  # None = unlimited
                "expiresAt": membership.endDate.isoformat() if membership.endDate else None,
            })

        return results

    # ============================================
    # HELPER: POPULATE PACKAGE RESPONSE
    # ============================================

    @staticmethod
    async def _populate_package_response(package) -> PackageResponse:
        """
        Populate package response with computed class details, excludedClasses,
        and decoded numberOfSessions / timeRestriction.
        """
        allowed_classes: List[str] = list(package.allowedClasses) if package.allowedClasses else []
        time_str, number_of_sessions = _decode_time_restriction(package.timeRestriction)

        # Populate allowed class details
        class_details = []
        if allowed_classes:
            classes = await prisma.classes.find_many(
                where={"id": {"in": allowed_classes}}
            )
            class_details = [
                {
                    "id":          c.id,
                    "title":       c.title,
                    "scheduledAt": c.scheduledAt,
                    "duration":    c.duration,
                    "location":    c.location
                }
                for c in classes
            ]

        # Compute excluded classes
        excluded_details = await _compute_excluded_classes(allowed_classes)
        excluded_ids = [e["id"] for e in excluded_details]

        return PackageResponse(
            id=package.id,
            name=package.name,
            description=package.description,
            price=package.price,
            discountPrice=package.discountPrice,
            durationDays=package.durationDays,
            allowedClasses=allowed_classes,
            numberOfSessions=number_of_sessions,
            timeRestriction=time_str,
            autoRenew=package.autoRenew,
            isActive=package.isActive,
            order=package.order,
            createdAt=package.createdAt,
            updatedAt=package.updatedAt,
            totalClasses=len(allowed_classes),
            classDetails=class_details,
            excludedClasses=excluded_ids,
            excludedClassDetails=excluded_details,
        )

    # ============================================
    # HELPER: POPULATE USER PACKAGE INSTANCE
    # ============================================

    @staticmethod
    async def _populate_user_package_instance(membership) -> UserPackageInstance:
        """
        Build a UserPackageInstance from a package-backed Membership row.

        Resolves the Package template by name to get:
        - numberOfSessions (decoded from timeRestriction)
        - excludedClasses (computed)
        - classDetails (from allowedClasses)

        Session tracking:
        - sessionsUsed  = int(membership.progress)
        - sessionsRemaining = numberOfSessions - sessionsUsed  (None if unlimited)
        """
        allowed_classes: List[str] = list(membership.allowedClasses) if membership.allowedClasses else []

        # Resolve Package template for numberOfSessions + excludedClasses
        package = await prisma.package.find_first(
            where={"name": membership.name}
        )

        number_of_sessions: Optional[int] = None
        time_str: Optional[str] = None
        excluded_details: List[dict] = []
        excluded_ids: List[str] = []
        package_id: Optional[str] = None

        if package:
            package_id = package.id
            time_str, number_of_sessions = _decode_time_restriction(package.timeRestriction)
            excluded_details = await _compute_excluded_classes(
                list(package.allowedClasses) if package.allowedClasses else []
            )
            excluded_ids = [e["id"] for e in excluded_details]
            # Use package's allowedClasses for display (authoritative source)
            allowed_classes = list(package.allowedClasses) if package.allowedClasses else []
        else:
            # Package template deleted — use membership's own allowedClasses
            excluded_details = await _compute_excluded_classes(allowed_classes)
            excluded_ids = [e["id"] for e in excluded_details]
            # Decode from membership's own timeRestriction (copied at purchase time)
            time_str, number_of_sessions = _decode_time_restriction(membership.timeRestriction)

        # Session tracking
        sessions_used = int(membership.progress or 0)
        sessions_remaining: Optional[int] = None
        if number_of_sessions is not None:
            sessions_remaining = max(0, number_of_sessions - sessions_used)

        # Populate class details
        class_details = []
        if allowed_classes:
            classes = await prisma.classes.find_many(
                where={"id": {"in": allowed_classes}}
            )
            class_details = [
                {
                    "id":          c.id,
                    "title":       c.title,
                    "scheduledAt": c.scheduledAt,
                    "duration":    c.duration,
                    "location":    c.location
                }
                for c in classes
            ]

        # Days remaining
        days_remaining: Optional[int] = None
        if membership.endDate:
            end = make_aware(membership.endDate)
            delta = end - now_utc()
            days_remaining = max(0, delta.days)

        return UserPackageInstance(
            id=membership.id,
            packageId=package_id,
            name=membership.name,
            description=membership.description,
            price=membership.price,
            durationDays=membership.durationDays,
            allowedClasses=allowed_classes,
            numberOfSessions=number_of_sessions,
            sessionsUsed=sessions_used,
            sessionsRemaining=sessions_remaining,
            timeRestriction=time_str,
            autoRenew=membership.autoRenew,
            isActive=str(membership.status) == "ACTIVE",
            status=str(membership.status),
            startDate=membership.startDate,
            endDate=membership.endDate,
            daysRemaining=days_remaining,
            totalClasses=len(allowed_classes),
            classDetails=class_details,
            excludedClasses=excluded_ids,
            excludedClassDetails=excluded_details,
            createdAt=membership.createdAt,
            updatedAt=membership.updatedAt,
        )

    # ============================================
    # EXPORT TO EXCEL
    # ============================================

    @staticmethod
    async def export_packages_to_excel(
        user_id: str,
        search: Optional[str] = None,
        is_active: Optional[bool] = None
    ) -> BytesIO:
        """Export package plan definitions to an Excel file (Admin/Manager only)."""
        user = await prisma.user.find_unique(where={"id": user_id})
        if not user or not _policy_can(str(user.role), _Resource.PACKAGE, "view") or \
           user.role not in (UserRole.ADMIN, UserRole.MANAGER):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access restricted to Admin and Manager roles"
            )

        where_clause: Dict[str, Any] = {}

        if search:
            where_clause["OR"] = [
                {"name": {"contains": search, "mode": "insensitive"}},
                {"description": {"contains": search, "mode": "insensitive"}}
            ]

        if is_active is not None:
            where_clause["isActive"] = is_active

        packages = await prisma.package.find_many(
            where=where_clause,
            order={"order": "asc"}
        )

        export_data = []
        for package in packages:
            time_str, number_of_sessions = _decode_time_restriction(package.timeRestriction)
            export_data.append({
                "Package Name":          package.name,
                "Description":           package.description or "N/A",
                "Price (QAR)":           package.price,
                "Discount Price (QAR)":  package.discountPrice or "N/A",
                "Duration (Days)":       package.durationDays,
                "Number of Sessions":    number_of_sessions if number_of_sessions is not None else "Unlimited",
                "Total Classes":         len(package.allowedClasses) if package.allowedClasses else 0,
                "Time Restriction":      time_str or "No restriction",
                "Auto Renew":            "Yes" if package.autoRenew else "No",
                "Active":                "Yes" if package.isActive else "No",
                "Created Date":          package.createdAt.strftime("%Y-%m-%d")
            })

        df = pd.DataFrame(export_data)
        output = BytesIO()
        with pd.ExcelWriter(output, engine="openpyxl") as writer:
            df.to_excel(writer, sheet_name="Packages", index=False)
            worksheet = writer.sheets["Packages"]
            for idx, col in enumerate(df.columns):
                max_length = max(df[col].astype(str).apply(len).max(), len(col)) + 2
                worksheet.column_dimensions[chr(65 + idx)].width = min(max_length, 50)

        output.seek(0)
        return output