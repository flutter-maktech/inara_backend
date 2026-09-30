from fastapi import APIRouter, Depends, status, Response
from typing import Optional
from app.models.membership_model import (
    MembershipCreate, MembershipUpdate, MembershipResponse, MembershipListResponse,
    MembershipSearchParams, CancelMembership, PauseMembership, RenewMembership,
    ValidateMembershipAccess, MembershipAccessResponse,
    MembershipCatalogueListResponse,
    PurchaseMembership, PurchaseMembershipResponse
)
from app.services.membership_service import MembershipService
from app.api.v1.dependencies import get_current_active_user
from prisma.enums import MembershipStatus

router = APIRouter()


# ============================================
# CREATE MEMBERSHIP
# ============================================

@router.post("/", response_model=MembershipResponse, status_code=status.HTTP_201_CREATED)
async def create_membership(
    data: MembershipCreate,
    current_user=Depends(get_current_active_user)
):
    """
    Create a new membership plan template (Admin only).

    **Purpose:** Admin defines a membership plan that acts as a purchasable
    template in the catalogue. This is NOT a user's purchased membership —
    it is a plan DEFINITION. Users purchase copies via POST /memberships/purchase.

    **How ownership works:** The authenticated Admin is automatically set as
    the template owner via the JWT token. No `userId` is required or accepted
    in the request body — this keeps the API surface minimal and secure.

    **UI Reference:** "Add New Membership" modal
    - Membership Title
    - Membership Description
    - Membership Price (QAR)
    - Valid For (duration in days)
    - Allowed Classes (multi-select)
    - Time Restriction (e.g., "Before 3:00 PM")
    - Auto Renew toggle

    **Permissions:** ADMIN only
    """
    return await MembershipService.create_membership(data, current_user.id)


# ============================================
# GET ALL MEMBERSHIPS (Admin/Manager — management view)
# ============================================

@router.get("/", response_model=MembershipListResponse)
async def get_all_memberships(
    search: Optional[str] = None,
    userId: Optional[str] = None,
    status: Optional[MembershipStatus] = None,
    minPrice: Optional[float] = None,
    maxPrice: Optional[float] = None,
    expiringInDays: Optional[int] = None,
    page: int = 1,
    pageSize: int = 10,
    sortBy: str = "createdAt",
    sortOrder: str = "desc",
    current_user=Depends(get_current_active_user)
):
    """
    Retrieve membership records — scope depends strictly on the caller's role.

    **Role-based scope (enforced server-side):**
    - **ADMIN / MANAGER:** See ALL membership records across every user.
      Use the `userId` filter to narrow down to a specific user.
    - **INSTRUCTOR:** See ALL membership records (read-only view for reference).
    - **USER:** Sees ONLY their own membership records, regardless of any
      `userId` parameter supplied. Cross-user queries are silently ignored.

    **Features:**
    - Search by name/description
    - Filter by userId (Admin/Manager only), status, price range
    - Filter memberships expiring within `expiringInDays` days (ACTIVE only)
    - Auto-expires overdue ACTIVE memberships on each call
    - Pagination & sorting

    **Permissions:** All authenticated roles (scope auto-enforced per role)
    """
    params = MembershipSearchParams(
        search=search,
        userId=userId,
        status=status,
        minPrice=minPrice,
        maxPrice=maxPrice,
        expiringInDays=expiringInDays,
        page=page,
        pageSize=pageSize,
        sortBy=sortBy,
        sortOrder=sortOrder
    )
    return await MembershipService.get_all_memberships(current_user.id, params)


# ============================================
# GET MEMBERSHIP CATALOGUE (All users — browse & purchase)
# ============================================

@router.get("/catalogue", response_model=MembershipCatalogueListResponse)
async def get_membership_catalogue(
    search: Optional[str] = None,
    status: Optional[MembershipStatus] = None,
    minPrice: Optional[float] = None,
    maxPrice: Optional[float] = None,
    page: int = 1,
    pageSize: int = 10,
    sortBy: str = "createdAt",
    sortOrder: str = "desc",
    current_user=Depends(get_current_active_user)
):
    """
    Browse ALL available membership plans — visible to every authenticated user.

    **Purpose:** Users need to see what membership plans exist before purchasing.
    This is the discovery endpoint for the membership purchase flow, equivalent to
    GET /packages/catalogue for the package flow.

    **How catalogue works:**
    Membership plan templates are admin-created Membership rows. The catalogue
    returns only admin-owned plans — user-purchased copies never appear here.

    **Returns:**
    - Full list of membership plan templates (no ownership filter)
    - Class details per plan
    - Price, duration, time restrictions

    **Permissions:** Any authenticated user (ADMIN, MANAGER, INSTRUCTOR, USER)
    """
    params = MembershipSearchParams(
        search=search,
        status=status,
        minPrice=minPrice,
        maxPrice=maxPrice,
        page=page,
        pageSize=pageSize,
        sortBy=sortBy,
        sortOrder=sortOrder
    )
    return await MembershipService.get_membership_catalogue(params)


# ============================================
# PURCHASE MEMBERSHIP
# ============================================

@router.post("/purchase", response_model=PurchaseMembershipResponse, status_code=status.HTTP_201_CREATED)
async def purchase_membership(
    data: PurchaseMembership,
    current_user=Depends(get_current_active_user)
):
    """
    Purchase a membership plan — clones the admin plan into a new user-owned record.

    **Workflow:**
    1. User browses GET /memberships/catalogue and selects a plan.
    2. User calls this endpoint with only the plan's `membershipId`.
    3. System validates the plan is an admin-created template and is ACTIVE.
    4. System clones the plan into a brand-new Membership record for the
       authenticated user (resolved automatically from the JWT token).
    5. New membership is immediately ACTIVE with computed start/end dates.

    **Duplicate Guard:** Cannot purchase the same plan while an active, non-expired
    copy already exists under the user's account.

    **Security:** `userId` is never accepted in the request body. The purchasing
    user is always the authenticated caller from the JWT token — this prevents
    any user from purchasing a membership on behalf of another without authorisation.

    **Independence Note:** This endpoint is completely independent of the Package
    module — no Package records are created or referenced.

    **Request Body:**
    ```json
    {
      "membershipId": "plan_id_here",
      "autoRenew":    false
    }
    ```

    **Permissions:** Any authenticated user
    """
    return await MembershipService.purchase_membership(data, current_user.id)


# ============================================
# GET MY MEMBERSHIPS (Convenience endpoint — current user's memberships)
# ============================================

@router.get("/info-me", response_model=MembershipListResponse)
async def get_my_memberships(
    status: Optional[MembershipStatus] = None,
    page: int = 1,
    pageSize: int = 10,
    current_user=Depends(get_current_active_user)
):
    """
    Get the current user's own memberships.

    **Purpose:** Lets a logged-in user view only the memberships they personally
    own (purchased or assigned). Scoped strictly to the requesting user — no
    cross-user data is returned regardless of role.

    **Use case:** "My Memberships" page in the member dashboard.

    **Permissions:** Any authenticated user (own memberships only)
    """
    params = MembershipSearchParams(
        userId=current_user.id,
        status=status,
        page=page,
        pageSize=pageSize,
        sortBy="createdAt",
        sortOrder="desc"
    )
    return await MembershipService.get_all_memberships(current_user.id, params)


# ============================================
# GET EXPIRING MEMBERSHIPS (Admin/Manager — renewal alerts)
# ============================================

@router.get("/expiring", response_model=MembershipListResponse)
async def get_expiring_memberships(
    days: int = 7,
    page: int = 1,
    pageSize: int = 10,
    current_user=Depends(get_current_active_user)
):
    """
    Get ACTIVE memberships expiring within the next X days (Admin/Manager only).

    **Purpose:** Proactive renewal reminder tool. Identifies memberships approaching
    their end date so staff can reach out or trigger automated renewal reminders.

    **Default:** Memberships expiring in the next 7 days.
    **Sorted:** By endDate ascending (soonest to expire first).

    **Permissions:** ADMIN, MANAGER only
    """
    params = MembershipSearchParams(
        expiringInDays=days,
        page=page,
        pageSize=pageSize,
        sortBy="endDate",
        sortOrder="asc"
    )
    return await MembershipService.get_all_memberships(current_user.id, params)


# ============================================
# GET MEMBERSHIP BY ID
# ============================================

@router.get("/{membership_id}", response_model=MembershipResponse)
async def get_membership_by_id(
    membership_id: str,
    current_user=Depends(get_current_active_user)
):
    """
    Get full details of a specific membership record by its ID.

    **Access control:** ADMIN/MANAGER can retrieve any membership by ID.
    A USER can only retrieve their own membership records — attempting to
    retrieve another user's membership returns 403 Forbidden.

    **Returns:**
    - Full membership details
    - Days remaining until expiry
    - List of allowed classes with details
    - Owning user information

    **Permissions:** ADMIN, MANAGER (any); USER (own only)
    """
    return await MembershipService.get_membership_by_id(membership_id, current_user.id)


# ============================================
# UPDATE MEMBERSHIP
# ============================================

@router.patch("/{membership_id}", response_model=MembershipResponse)
async def update_membership(
    membership_id: str,
    data: MembershipUpdate,
    current_user=Depends(get_current_active_user)
):
    """
    Update a membership record (Admin only).

    **UI Reference:** "Edit Membership" modal
    - All fields are optional — send only what changes
    - Can update classes, restrictions, status, end date

    **Permissions:** ADMIN only
    """
    return await MembershipService.update_membership(membership_id, data, current_user.id)


# ============================================
# DELETE MEMBERSHIP
# ============================================

@router.delete("/{membership_id}", status_code=status.HTTP_200_OK)
async def delete_membership(
    membership_id: str,
    current_user=Depends(get_current_active_user)
):
    """
    Permanently delete a membership record (Admin only).

    **Warning:** This is a hard delete. To end a membership non-destructively,
    use POST /memberships/cancel instead which preserves the audit trail.

    **Permissions:** ADMIN only
    """
    return await MembershipService.delete_membership(membership_id, current_user.id)


# ============================================
# CANCEL MEMBERSHIP
# ============================================

@router.post("/cancel", status_code=status.HTTP_200_OK)
async def cancel_membership(
    data: CancelMembership,
    current_user=Depends(get_current_active_user)
):
    """
    Cancel a membership (soft status change — preserves the record).

    **Purpose:** Gracefully ends a membership while keeping the audit trail.
    Status changes to CANCELLED; the record remains in the database.

    **Use case:** User requests early cancellation; Admin processes a refund cancellation.
    - Status → CANCELLED
    - cancelledAt timestamp is set
    - Optional refund amount and cancellation reason captured

    **Ownership:** Users can only cancel their own memberships.
    Admin/Manager can cancel any membership.

    **Permissions:** Any authenticated user (own); ADMIN/MANAGER (any)
    """
    return await MembershipService.cancel_membership(data, current_user.id)


# ============================================
# PAUSE MEMBERSHIP
# ============================================

@router.post("/pause", status_code=status.HTTP_200_OK)
async def pause_membership(
    data: PauseMembership,
    current_user=Depends(get_current_active_user)
):
    """
    Pause a membership and extend its end date.

    **Purpose:** Temporarily suspends access while preserving the remaining validity.
    The membership's end date is extended by the pause duration so the user
    does not lose paid time.

    **Use case:** User requests a pause for vacation, injury, or personal reasons.
    - Status → PAUSED
    - endDate extended by pauseDays
    - Resume happens via PATCH /{id} setting status back to ACTIVE

    **Permissions:** Any authenticated user (own); ADMIN/MANAGER (any)
    """
    return await MembershipService.pause_membership(data, current_user.id)


# ============================================
# RENEW MEMBERSHIP
# ============================================

@router.post("/renew", status_code=status.HTTP_200_OK)
async def renew_membership(
    data: RenewMembership,
    current_user=Depends(get_current_active_user)
):
    """
    Renew a membership by extending its validity.

    **Purpose:** Extends an expired, expiring, or active membership. Use when
    a user wants to continue without going through the full purchase flow.

    **Extension logic:**
    - If extendDays is provided → extend by that many days
    - If omitted → extend by the membership's original durationDays

    **Effect:**
    - Status → ACTIVE (re-activates EXPIRED or CANCELLED memberships)
    - endDate extended from current endDate
    - cancelledAt cleared

    **Permissions:** Any authenticated user (own); ADMIN/MANAGER (any)
    """
    return await MembershipService.renew_membership(data, current_user.id)


# ============================================
# VALIDATE MEMBERSHIP ACCESS (Class booking gate)
# ============================================

@router.post("/validate-access", response_model=MembershipAccessResponse)
async def validate_membership_access(
    data: ValidateMembershipAccess,
    current_user=Depends(get_current_active_user)
):
    """
    Check if a user can access a specific class via an active membership.

    **Purpose:** Pre-booking access gate. Called before a class booking is
    confirmed to verify the user's membership grants access to that class.

    **Validates:**
    - User has at least one ACTIVE, non-expired membership
    - The membership's allowedClasses list includes the requested classId
    - Current time satisfies any time restriction on the membership

    **Returns:**
    - hasAccess: true/false
    - reason: explains denial when hasAccess is false
    - membership: the granting membership details (when hasAccess is true)

    **Permissions:** Any authenticated user
    """
    return await MembershipService.validate_membership_access(data)


# ============================================
# EXPORT MEMBERSHIPS TO EXCEL
# ============================================

@router.get("/export/excel")
async def export_memberships_to_excel(
    search: Optional[str] = None,
    userId: Optional[str] = None,
    status: Optional[MembershipStatus] = None,
    current_user=Depends(get_current_active_user)
):
    """
    Export membership records to an Excel file (Admin/Manager only).

    **Scope:** ADMIN/MANAGER see all records; USER sees only their own.

    **Use case:** Admin exports membership list for financial reporting,
    renewal campaigns, or management review.

    **Permissions:** ADMIN, MANAGER (all records); USER (own records only)
    """
    excel_file = await MembershipService.export_memberships_to_excel(
        current_user.id,
        search=search,
        user_filter_id=userId,
        status=status
    )

    return Response(
        content=excel_file.read(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": "attachment; filename=memberships_export.xlsx"
        }
    )