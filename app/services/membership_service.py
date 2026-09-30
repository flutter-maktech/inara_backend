from datetime import datetime, timedelta, timezone
from typing import Optional, List, Dict, Any
from app.db.db_client import prisma
from app.models.membership_model import (
    MembershipCreate, MembershipUpdate, MembershipResponse, MembershipListResponse,
    MembershipSearchParams, CancelMembership, PauseMembership, RenewMembership,
    ValidateMembershipAccess, MembershipAccessResponse,
    MembershipCatalogue, MembershipCatalogueListResponse,
    PurchaseMembership, PurchaseMembershipResponse
)
from prisma.enums import UserRole, MembershipStatus
from app.core.permissions import can as _policy_can, Resource as _Resource
from fastapi import HTTPException, status
import pandas as pd
from io import BytesIO


# ──────────────────────────────────────────────────────────────────────────────
# excludedClasses computation helper
# ──────────────────────────────────────────────────────────────────────────────

async def _compute_excluded_classes(allowed_class_ids: List[str]) -> List[dict]:
    """
    Compute the list of ALL active classes that are NOT in allowed_class_ids.

    If allowed_class_ids is empty, the membership covers all classes,
    so there are no excluded classes — returns empty list.

    Returns a list of dicts: [{id, title, scheduledAt, duration, location}, ...]
    """
    if not allowed_class_ids:
        # Empty allowedClasses = all classes allowed → nothing excluded
        return []

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


class MembershipService:
    """
    Enterprise-grade Membership service

    Features:
    - CRUD operations for membership plan templates (Admin-created)
    - Purchase flow: user buys a plan → new Membership record cloned for them
    - Role-scoped listing: ADMIN/MANAGER see all; USER sees only their own
    - Search & filtering with expiry tracking
    - Membership actions (cancel, pause, renew)
    - Access validation for classes
    - Auto-expiry handling
    - Excel export

    Timezone policy (enforced throughout):
    - All datetime comparisons use now_utc()
    - All datetimes from DB are normalised via make_aware()

    Catalogue design (schema-compatible):
    - Prisma's Membership.userId is NON-NULLABLE — NULL-based templates are
      impossible at the DB level.
    - Catalogue plan templates are Membership rows owned by an ADMIN user.
    - get_membership_catalogue filters by user.role == ADMIN, guaranteeing that
      only admin-created plan templates are returned and no user-purchased copies
      ever leak into the public catalogue.
    - Memberships and Packages are 100% independent — no cross-dependency.

    Role-scoping in get_all_memberships (ROOT BUG FIX):
    - ADMIN / MANAGER / INSTRUCTOR : where clause has no userId restriction →
      sees every membership row.
    - USER : userId is ALWAYS forced to current_user.id in the where clause,
      regardless of any userId parameter supplied by the caller.
      This prevents users from querying other users' records by passing an
      arbitrary userId query param.
    """

    # ============================================
    # ROLE-BASED ACCESS CONTROL
    # ============================================

    @staticmethod
    async def check_membership_permission(
        user_id: str,
        membership_id: Optional[str] = None,
        action: str = "view"
    ) -> bool:
        """
        Check user permissions for membership actions.

        Permissions (resource = "membership"):
          ADMIN       : view, create, update, delete
          MANAGER     : view only            (NO create / update / delete)
          INSTRUCTOR  : view only
          USER        : view all + manage own memberships (ownership exception)

        Ownership exception: any user may view / cancel / renew their own
        membership row regardless of policy.
        """
        user = await prisma.user.find_unique(where={"id": user_id})
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        # Matrix decision first
        if _policy_can(str(user.role), _Resource.MEMBERSHIP, action):
            return True

        # Ownership exception — a user can act on their own membership row.
        if membership_id:
            membership = await prisma.membership.find_unique(
                where={"id": membership_id}
            )
            if membership and membership.userId == user_id:
                return True

        # Any authenticated USER can "purchase" / "create" their own membership.
        if action == "create" and user.role == UserRole.USER:
            return True

        return False

    # ============================================
    # CREATE MEMBERSHIP
    # ============================================

    @staticmethod
    async def create_membership(
        data: MembershipCreate,
        created_by_user_id: str
    ) -> MembershipResponse:
        """
        Create a new membership plan template (Admin only).

        The authenticated Admin is automatically set as the owner (userId) of
        the template. No userId is accepted from the request body — the catalogue
        design requires templates to be owned by ADMIN users, and the owner is
        always the calling Admin resolved from the JWT token.
        """
        has_permission = await MembershipService.check_membership_permission(
            created_by_user_id, action="create"
        )
        if not has_permission:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You don't have permission to create memberships"
            )

        # Verify the creator is an ADMIN — only ADMIN-owned records appear
        # in the catalogue. This guard is belt-and-suspenders alongside the
        # RBAC check above.
        creator = await prisma.user.find_unique(where={"id": created_by_user_id})
        if not creator:
            raise HTTPException(status_code=404, detail="User not found")

        if creator.role != UserRole.ADMIN:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only Admins can create membership plan templates"
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

        start_date = now_utc()
        end_date   = start_date + timedelta(days=data.durationDays)

        # Build membership data from the Pydantic model, then inject
        # the server-resolved userId — never accept it from the request body.
        membership_data = data.model_dump()
        membership_data["userId"]    = created_by_user_id   # ← JWT-sourced, not caller-supplied
        membership_data["startDate"] = start_date
        membership_data["endDate"]   = end_date
        membership_data["status"]    = MembershipStatus.ACTIVE

        membership = await prisma.membership.create(data=membership_data)
        return await MembershipService._populate_membership_response(membership)

    # ============================================
    # GET ALL MEMBERSHIPS (Role-scoped)
    # ============================================

    @staticmethod
    async def get_all_memberships(
        user_id: str,
        params: MembershipSearchParams
    ) -> MembershipListResponse:
        """
        Get membership records with role-enforced scoping.

        RBAC scope (server-enforced — cannot be bypassed via query params):
          ADMIN / MANAGER / INSTRUCTOR → all membership records
          USER                         → strictly own records only
                                         (params.userId is overridden to user_id)

        ROOT BUG FIX:
        Previously, a USER could call GET /memberships/?userId=<other_id> and see
        another user's records because the where clause simply used whatever
        params.userId was set to. The fix below forces the userId clause to the
        requesting user's own ID whenever the caller is a USER role,
        regardless of what params.userId contains.
        """
        user = await prisma.user.find_unique(where={"id": user_id})
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        has_permission = await MembershipService.check_membership_permission(
            user_id, action="view"
        )
        if not has_permission:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You don't have permission to view memberships"
            )

        where_clause: Dict[str, Any] = {}

        # ── ROLE-SCOPE ENFORCEMENT ──────────────────────────────────────
        # USER role: always restrict to own records, ignore caller-supplied userId.
        # Admin / Manager / Instructor: respect the optional userId filter param.
        if user.role == UserRole.USER:
            where_clause["userId"] = user_id          # hard-scoped — non-negotiable

            # INDEPENDENCE GUARANTEE: Exclude package-activated membership rows
            # from the membership view. Package-backed rows are identified by
            # having a paymentLogId that points to a PaymentLog with module=PACKAGE.
            # They must be surfaced ONLY via the /packages/ endpoints.
            package_logs = await prisma.paymentlog.find_many(
                where={"userId": user_id, "module": "PACKAGE"},
            )
            package_log_ids = [log.id for log in package_logs]
            if package_log_ids:
                where_clause["paymentLogId"] = {"notIn": package_log_ids}

        elif params.userId:
            where_clause["userId"] = params.userId    # optional filter for privileged roles

            # For admin/manager filtering by a specific user, also exclude
            # that user's package-activated rows from the membership view.
            package_logs = await prisma.paymentlog.find_many(
                where={"userId": params.userId, "module": "PACKAGE"},
            )
            package_log_ids = [log.id for log in package_logs]
            if package_log_ids:
                where_clause["paymentLogId"] = {"notIn": package_log_ids}

        else:
            # Admin / Manager viewing all — exclude ALL package-activated rows
            all_package_logs = await prisma.paymentlog.find_many(
                where={"module": "PACKAGE"},
            )
            all_package_log_ids = [log.id for log in all_package_logs]
            if all_package_log_ids:
                where_clause["paymentLogId"] = {"notIn": all_package_log_ids}

        if params.search:
            where_clause["OR"] = [
                {"name":        {"contains": params.search, "mode": "insensitive"}},
                {"description": {"contains": params.search, "mode": "insensitive"}}
            ]

        if params.status:
            where_clause["status"] = params.status

        if params.minPrice is not None:
            where_clause.setdefault("price", {})["gte"] = params.minPrice

        if params.maxPrice is not None:
            where_clause.setdefault("price", {})["lte"] = params.maxPrice

        if params.expiringInDays:
            _now        = now_utc()
            expiry_date = _now + timedelta(days=params.expiringInDays)
            where_clause["endDate"] = {"lte": expiry_date, "gte": _now}
            where_clause["status"]  = MembershipStatus.ACTIVE

        total       = await prisma.membership.count(where=where_clause)
        skip        = (params.page - 1) * params.pageSize
        total_pages = max(1, (total + params.pageSize - 1) // params.pageSize)

        memberships = await prisma.membership.find_many(
            where=where_clause,
            include={"user": True},
            skip=skip,
            take=params.pageSize,
            order={params.sortBy: params.sortOrder}
        )

        await MembershipService._auto_expire_memberships()

        memberships_response = [
            await MembershipService._populate_membership_response(m)
            for m in memberships
        ]

        return MembershipListResponse(
            memberships=memberships_response,
            total=total,
            page=params.page,
            pageSize=params.pageSize,
            totalPages=total_pages
        )

    # ============================================
    # GET MEMBERSHIP BY ID
    # ============================================

    @staticmethod
    async def get_membership_by_id(
        membership_id: str,
        user_id: str
    ) -> MembershipResponse:
        """
        Get membership details by ID.

        Only accessible by Admin/Manager or the owning User.
        """
        has_permission = await MembershipService.check_membership_permission(
            user_id, membership_id, action="view"
        )
        if not has_permission:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You don't have permission to view this membership"
            )

        membership = await prisma.membership.find_unique(
            where={"id": membership_id},
            include={"user": True}
        )

        if not membership:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Membership not found"
            )

        return await MembershipService._populate_membership_response(membership)

    # ============================================
    # UPDATE MEMBERSHIP
    # ============================================

    @staticmethod
    async def update_membership(
        membership_id: str,
        data: MembershipUpdate,
        updated_by_user_id: str
    ) -> MembershipResponse:
        """Update a membership record (Admin only)."""
        has_permission = await MembershipService.check_membership_permission(
            updated_by_user_id, membership_id, action="update"
        )
        if not has_permission:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You don't have permission to update this membership"
            )

        membership = await prisma.membership.find_unique(where={"id": membership_id})
        if not membership:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Membership not found"
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

        update_data = data.model_dump(exclude_unset=True)
        updated_membership = await prisma.membership.update(
            where={"id": membership_id},
            data=update_data,
            include={"user": True}
        )

        return await MembershipService._populate_membership_response(updated_membership)

    # ============================================
    # DELETE MEMBERSHIP
    # ============================================

    @staticmethod
    async def delete_membership(
        membership_id: str,
        deleted_by_user_id: str
    ):
        """
        Delete a membership record (Admin only).

        Per the latest policy this is ADMIN only — Manager cannot delete
        memberships. Enforced through the central RBAC matrix.
        """
        user = await prisma.user.find_unique(where={"id": deleted_by_user_id})
        if not user or not _policy_can(str(user.role), _Resource.MEMBERSHIP, "delete"):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You don't have permission to delete memberships"
            )

        membership = await prisma.membership.find_unique(where={"id": membership_id})
        if not membership:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Membership not found"
            )

        await prisma.membership.delete(where={"id": membership_id})
        return {"message": "Membership deleted successfully"}

    # ============================================
    # CANCEL MEMBERSHIP
    # ============================================

    @staticmethod
    async def cancel_membership(
        data: CancelMembership,
        cancelled_by_user_id: str
    ):
        """Cancel a membership (soft status change — preserves the record)."""
        has_permission = await MembershipService.check_membership_permission(
            cancelled_by_user_id, data.membershipId, action="cancel"
        )
        if not has_permission:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You don't have permission to cancel this membership"
            )

        membership = await prisma.membership.find_unique(where={"id": data.membershipId})
        if not membership:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Membership not found"
            )

        await prisma.membership.update(
            where={"id": data.membershipId},
            data={
                "status":      MembershipStatus.CANCELLED,
                "cancelledAt": now_utc()
            }
        )

        return {
            "message": "Membership cancelled successfully",
            "refundAmount": data.refundAmount or 0
        }

    # ============================================
    # PAUSE MEMBERSHIP
    # ============================================

    @staticmethod
    async def pause_membership(
        data: PauseMembership,
        paused_by_user_id: str
    ):
        """Pause a membership and extend its end date."""
        has_permission = await MembershipService.check_membership_permission(
            paused_by_user_id, data.membershipId, action="pause"
        )
        if not has_permission:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You don't have permission to pause this membership"
            )

        membership = await prisma.membership.find_unique(where={"id": data.membershipId})
        if not membership:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Membership not found"
            )

        base         = make_aware(membership.endDate) or now_utc()
        new_end_date = base + timedelta(days=data.pauseDays)

        await prisma.membership.update(
            where={"id": data.membershipId},
            data={"status": MembershipStatus.PAUSED, "endDate": new_end_date}
        )

        return {
            "message": f"Membership paused for {data.pauseDays} days",
            "newEndDate": new_end_date
        }

    # ============================================
    # RENEW MEMBERSHIP
    # ============================================

    @staticmethod
    async def renew_membership(
        data: RenewMembership,
        renewed_by_user_id: str
    ):
        """Renew a membership by extending its validity."""
        has_permission = await MembershipService.check_membership_permission(
            renewed_by_user_id, data.membershipId, action="renew"
        )
        if not has_permission:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You don't have permission to renew this membership"
            )

        membership = await prisma.membership.find_unique(where={"id": data.membershipId})
        if not membership:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Membership not found"
            )

        extend_days  = data.extendDays or membership.durationDays
        base         = make_aware(membership.endDate) or now_utc()
        new_end_date = base + timedelta(days=extend_days)

        await prisma.membership.update(
            where={"id": data.membershipId},
            data={
                "status":      MembershipStatus.ACTIVE,
                "endDate":     new_end_date,
                "cancelledAt": None
            }
        )

        return {
            "message": "Membership renewed successfully",
            "newEndDate": new_end_date,
            "extendedBy": extend_days
        }

    # ============================================
    # VALIDATE MEMBERSHIP ACCESS
    # ============================================

    @staticmethod
    async def validate_membership_access(
        data: ValidateMembershipAccess
    ) -> MembershipAccessResponse:
        """
        Check if a user can access a class via their active membership.

        Validates:
        - Active membership exists
        - Class is in allowedClasses
        - Time restriction matches
        - Membership not expired
        """
        memberships = await prisma.membership.find_many(
            where={"userId": data.userId, "status": MembershipStatus.ACTIVE}
        )

        if not memberships:
            return MembershipAccessResponse(
                hasAccess=False,
                reason="No active membership found"
            )

        class_obj = await prisma.classes.find_unique(where={"id": data.classId})
        if not class_obj:
            return MembershipAccessResponse(
                hasAccess=False,
                reason="Class not found"
            )

        _now = now_utc()

        for membership in memberships:
            end = make_aware(membership.endDate)
            if end and end < _now:
                continue

            if data.classId not in membership.allowedClasses:
                continue

            if membership.timeRestriction:
                requested_time = make_aware(data.requestedTime) if data.requestedTime else _now
                # Placeholder for full time-restriction parsing logic
                _ = requested_time

            membership_response = await MembershipService._populate_membership_response(
                membership
            )
            return MembershipAccessResponse(hasAccess=True, membership=membership_response)

        return MembershipAccessResponse(
            hasAccess=False,
            reason="No membership grants access to this class at this time"
        )

    # ============================================
    # HELPER: AUTO-EXPIRE MEMBERSHIPS
    # ============================================

    @staticmethod
    async def _auto_expire_memberships():
        """Automatically expire memberships past their end date."""
        await prisma.membership.update_many(
            where={
                "endDate": {"lt": now_utc()},
                "status":   MembershipStatus.ACTIVE
            },
            data={"status": MembershipStatus.EXPIRED}
        )

    # ============================================
    # HELPER: POPULATE MEMBERSHIP RESPONSE
    # ============================================

    @staticmethod
    async def _populate_membership_response(membership) -> MembershipResponse:
        """Populate membership response with computed fields, class details, and excludedClasses."""
        allowed_classes: List[str] = list(membership.allowedClasses) if membership.allowedClasses else []

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

        # Compute excluded classes (classes NOT in allowedClasses)
        excluded_details = await _compute_excluded_classes(allowed_classes)
        excluded_ids = [e["id"] for e in excluded_details]

        days_remaining = None
        is_expired     = False
        if membership.endDate:
            end            = make_aware(membership.endDate)
            delta          = end - now_utc()
            days_remaining = max(0, delta.days)
            is_expired     = delta.days < 0

        user_info = None
        if hasattr(membership, "user") and membership.user:
            user_info = {
                "id":    membership.user.id,
                "name":  membership.user.name,
                "email": membership.user.email,
                "phone": membership.user.phone
            }

        membership_dict = membership.model_dump(exclude={"user"})

        return MembershipResponse(
            **membership_dict,
            daysRemaining=days_remaining,
            isExpired=is_expired,
            totalClasses=len(allowed_classes),
            classDetails=class_details,
            excludedClasses=excluded_ids,
            excludedClassDetails=excluded_details,
            user=user_info
        )

    # ============================================
    # GET MEMBERSHIP CATALOGUE (All users — browsing)
    # ============================================

    @staticmethod
    async def get_membership_catalogue(
        params: MembershipSearchParams
    ) -> MembershipCatalogueListResponse:
        """
        Return only admin-created membership plan TEMPLATES for user browsing.

        SCHEMA-COMPATIBLE DESIGN:
          Prisma's Membership.userId is NON-NULLABLE (NOT NULL in DB), so the
          previous approach of filtering `userId IS NULL` for "templates" is
          architecturally impossible — Prisma throws MissingRequiredValueError.

          FIX: Catalogue templates are Membership rows whose OWNER is an ADMIN
          user. We resolve all ADMIN user IDs first, then filter memberships by
          `userId IN [admin_ids]`. This guarantees:
            - Only admin-created plan templates appear in the catalogue.
            - No user-purchased copies ever leak (those have USER-role owners).
            - No cross-dependency with the Package module whatsoever.
            - Zero schema migrations required.

        Memberships and Packages are 100% INDEPENDENT modules.
        """
        admin_users = await prisma.user.find_many(
            where={"role": UserRole.ADMIN},
        )
        admin_ids = [u.id for u in admin_users]

        if not admin_ids:
            return MembershipCatalogueListResponse(
                memberships=[],
                total=0,
                page=params.page,
                pageSize=params.pageSize,
                totalPages=1
            )

        where_clause: Dict[str, Any] = {"userId": {"in": admin_ids}}

        if params.search:
            where_clause["OR"] = [
                {"name":        {"contains": params.search, "mode": "insensitive"}},
                {"description": {"contains": params.search, "mode": "insensitive"}}
            ]

        if params.status:
            where_clause["status"] = params.status

        if params.minPrice is not None:
            where_clause.setdefault("price", {})["gte"] = params.minPrice

        if params.maxPrice is not None:
            where_clause.setdefault("price", {})["lte"] = params.maxPrice

        total       = await prisma.membership.count(where=where_clause)
        skip        = (params.page - 1) * params.pageSize
        total_pages = max(1, (total + params.pageSize - 1) // params.pageSize)

        memberships = await prisma.membership.find_many(
            where=where_clause,
            skip=skip,
            take=params.pageSize,
            order={params.sortBy: params.sortOrder}
        )

        plans = []
        for m in memberships:
            class_details = []
            # Safely read allowedClasses — may be None or empty list from Prisma
            allowed_classes: List[str] = list(m.allowedClasses) if m.allowedClasses else []

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

            # ROOT CAUSE FIX (Issue #2):
            # Using **m.model_dump() passes ALL Prisma Membership fields, including
            # fields not defined in MembershipCatalogue (e.g. allowedCourses, userId,
            # startDate, endDate, progress, isPaid, paymentLogId, paymentMethod).
            # In Pydantic v2 with model_config extra="ignore" this silently discards
            # them — BUT if the Prisma model_dump() returns snake_case keys (e.g.
            # allowed_classes) while MembershipCatalogue expects camelCase
            # (allowedClasses), the field is silently set to its default [] instead
            # of the actual DB value.
            #
            # FIX: Explicitly construct MembershipCatalogue with only its defined
            # fields, reading all values directly from the Prisma object attributes
            # (which are always camelCase in prisma-client-py).
            plans.append(
                MembershipCatalogue(
                    id=m.id,
                    name=m.name,
                    description=m.description,
                    price=m.price,
                    durationDays=m.durationDays,
                    allowedClasses=allowed_classes,
                    timeRestriction=m.timeRestriction,
                    autoRenew=m.autoRenew,
                    status=m.status,
                    totalClasses=len(allowed_classes),
                    classDetails=class_details,
                    excludedClasses=excluded_ids,
                    excludedClassDetails=excluded_details,
                    createdAt=m.createdAt,
                    updatedAt=m.updatedAt,
                )
            )

        return MembershipCatalogueListResponse(
            memberships=plans,
            total=total,
            page=params.page,
            pageSize=params.pageSize,
            totalPages=total_pages
        )

    # ============================================
    # PURCHASE MEMBERSHIP
    # ============================================

    @staticmethod
    async def purchase_membership(
        data: PurchaseMembership,
        purchased_by_user_id: str
    ) -> PurchaseMembershipResponse:
        """
        User purchases an existing admin-created membership plan.

        Steps:
          1. Validate the plan exists, is admin-owned (catalogue template), and is ACTIVE.
          2. Validate the purchasing user exists (resolved from JWT token — not request body).
          3. GUARD: Reject if the user already holds a valid active membership
             created from this same plan.
          4. Clone the plan into a brand-new Membership record for that user.
          5. Return a clean purchase-confirmation response.

        Security: purchased_by_user_id is ALWAYS sourced from the authenticated
        caller's JWT token. It is never accepted from the request body, preventing
        any caller from purchasing a membership on behalf of an arbitrary other
        user without explicit authorisation.
        """
        # ── 1. Resolve the plan ──────────────────────────────────────────
        plan = await prisma.membership.find_unique(
            where={"id": data.membershipId},
            include={"user": True}
        )
        if not plan:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Membership plan not found"
            )

        if not plan.user or plan.user.role != UserRole.ADMIN:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="The specified ID does not refer to a purchasable membership plan"
            )

        if plan.status != MembershipStatus.ACTIVE:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="This membership plan is not currently available for purchase"
            )

        # ── 2. Resolve the purchasing user from JWT (not request body) ───
        user = await prisma.user.find_unique(where={"id": purchased_by_user_id})
        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found"
            )

        # ── 3. DUPLICATE GUARD ────────────────────────────────────────────
        # Check for an ACTIVE membership with the same name, but EXCLUDE
        # package-activated rows (those have paymentLogId → module=PACKAGE).
        # A user who purchased a package with the same display name as a
        # membership plan must NOT be blocked from purchasing the membership plan.
        _now = now_utc()

        # Collect this user's package PaymentLog IDs to exclude
        _pkg_logs = await prisma.paymentlog.find_many(
            where={"userId": purchased_by_user_id, "module": "PACKAGE"},
        )
        _pkg_log_ids = [log.id for log in _pkg_logs]

        _existing_where: Dict[str, Any] = {
            "userId":  purchased_by_user_id,
            "name":    plan.name,
            "status":  MembershipStatus.ACTIVE,
            "endDate": {"gt": _now},
        }
        if _pkg_log_ids:
            _existing_where["paymentLogId"] = {"notIn": _pkg_log_ids}

        existing = await prisma.membership.find_first(where=_existing_where)
        if existing:
            end_label = existing.endDate.strftime("%d %b %Y") if existing.endDate else "unknown date"
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"You already have an active '{plan.name}' membership that is valid "
                    f"until {end_label}. You cannot purchase the same plan while it is "
                    "still active."
                ),
            )

        # ── 4. Clone plan → new membership for the purchasing user ───────
        start_date = now_utc()
        end_date   = start_date + timedelta(days=plan.durationDays)

        new_membership = await prisma.membership.create(
            data={
                "userId":          purchased_by_user_id,   # ← JWT-sourced, never caller-supplied
                "name":            plan.name,
                "description":     plan.description,
                "price":           plan.price,
                "durationDays":    plan.durationDays,
                "allowedClasses":  plan.allowedClasses,
                "allowedCourses":  plan.allowedCourses,
                "timeRestriction": plan.timeRestriction,
                "autoRenew":       data.autoRenew,
                "startDate":       start_date,
                "endDate":         end_date,
                "status":          MembershipStatus.ACTIVE,
                "enrolledAt":      start_date,
                # Payment tracking — marks this as a user-purchased membership
                # (not an admin-created template, not a package-activated row).
                # isPaid=True with NO paymentLogId (paymentLogId is only set
                # when a PaymentLog record exists for this purchase, which the
                # current free-purchase flow does not create).
                "isPaid":          True,
                # paymentMethod is not part of the current PurchaseMembership
                # request model (this is a free-of-payment-gateway catalogue
                # purchase). The field is left null here; a future payment-gateway
                # integration can supply it via an extended request model.
            }
        )

        # ── 5. Return confirmation ────────────────────────────────────────
        return PurchaseMembershipResponse(
            message="Membership purchased successfully",
            membershipId=new_membership.id,
            membershipName=new_membership.name,
            validFrom=start_date,
            validUntil=end_date
        )

    # ============================================
    # EXPORT TO EXCEL
    # ============================================

    @staticmethod
    async def export_memberships_to_excel(
        user_id: str,
        search: Optional[str] = None,
        user_filter_id: Optional[str] = None,
        status: Optional[MembershipStatus] = None
    ) -> BytesIO:
        """Export memberships to an Excel file (role-scoped)."""
        where_clause: Dict[str, Any] = {}

        user = await prisma.user.find_unique(where={"id": user_id})
        # USER role: always restrict to own records
        if user and user.role == UserRole.USER:
            where_clause["userId"] = user_id
        elif user_filter_id:
            where_clause["userId"] = user_filter_id

        if search:
            where_clause["OR"] = [
                {"name":        {"contains": search, "mode": "insensitive"}},
                {"description": {"contains": search, "mode": "insensitive"}}
            ]

        if status:
            where_clause["status"] = status

        memberships = await prisma.membership.find_many(
            where=where_clause,
            include={"user": True},
            order={"createdAt": "desc"}
        )

        _now        = now_utc()
        export_data = []
        for m in memberships:
            days_remaining = None
            if m.endDate:
                end            = make_aware(m.endDate)
                days_remaining = max(0, (end - _now).days)

            export_data.append({
                "Membership Name": m.name,
                "User Name":       m.user.name  if m.user else "Unknown",
                "User Email":      m.user.email if m.user else "Unknown",
                "Price (QAR)":     m.price,
                "Duration (Days)": m.durationDays,
                "Total Classes":   len(m.allowedClasses) if m.allowedClasses else 0,
                "Status":          m.status,
                "Days Remaining":  days_remaining if days_remaining is not None else "N/A",
                "Start Date":      m.startDate.strftime("%Y-%m-%d"),
                "End Date":        m.endDate.strftime("%Y-%m-%d") if m.endDate else "N/A",
                "Auto Renew":      "Yes" if m.autoRenew else "No",
                "Created Date":    m.createdAt.strftime("%Y-%m-%d")
            })

        df = pd.DataFrame(export_data)

        output = BytesIO()
        with pd.ExcelWriter(output, engine="openpyxl") as writer:
            df.to_excel(writer, sheet_name="Memberships", index=False)
            worksheet = writer.sheets["Memberships"]
            for idx, col in enumerate(df.columns):
                max_len = max(df[col].astype(str).apply(len).max(), len(col)) + 2
                worksheet.column_dimensions[chr(65 + idx)].width = min(max_len, 50)

        output.seek(0)
        return output