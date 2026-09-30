from fastapi import APIRouter, Depends, status, Response
from typing import Optional
from app.models.package_model import (
    PackageCreate, PackageUpdate, PackageResponse, PackageListResponse,
    PackageSearchParams,
    PackageCatalogueListResponse,
    PurchasePackage, PurchasePackageResponse,
    CancelPackage, PausePackage, RenewPackage,
    ValidatePackageAccess, PackageAccessResponse,
    UserPackageListResponse,
)
from app.services.package_service import PackageService
from app.api.v1.dependencies import get_current_active_user

router = APIRouter()


# ============================================
# CREATE PACKAGE PLAN
# ============================================

@router.post("/", response_model=PackageResponse, status_code=status.HTTP_201_CREATED)
async def create_package(
    data: PackageCreate,
    current_user=Depends(get_current_active_user)
):
    """
    Create a new package plan (Admin only).

    **Purpose:** Admin defines a package plan that users can later browse
    in the catalogue and purchase. This is a plan DEFINITION — not a user purchase.

    **UI Reference:** "Add New Package" modal
    - Package Title
    - Package Description
    - Package Price (QAR)
    - Valid For (duration in days)
    - Allowed Classes (multi-select)
    - Time Restriction (e.g., "Before 3:00 PM")
    - Auto Renew toggle

    **Permissions:** ADMIN only
    """
    return await PackageService.create_package(data, current_user.id)


# ============================================
# GET ALL PACKAGES (Admin/Manager management view)
# ============================================

@router.get("/", response_model=PackageListResponse)
async def get_all_packages(
    search: Optional[str] = None,
    minPrice: Optional[float] = None,
    maxPrice: Optional[float] = None,
    isActive: Optional[bool] = None,
    page: int = 1,
    pageSize: int = 10,
    sortBy: str = "order",
    sortOrder: str = "asc",
    current_user=Depends(get_current_active_user)
):
    """
    Retrieve ALL package plan definitions — management view (Admin/Manager only).

    **Purpose:** Full administrative overview of every package plan including
    inactive ones. Use this to manage, audit, or update existing plans.

    **Features:**
    - Search by name/description
    - Filter by price range, active status (including inactive plans)
    - Pagination & sorting

    **Scope:** Returns ALL packages regardless of active status.
    For user-facing browsing of purchasable plans, use GET /packages/catalogue.

    **Permissions:** ADMIN, MANAGER only
    """
    params = PackageSearchParams(
        search=search,
        minPrice=minPrice,
        maxPrice=maxPrice,
        isActive=isActive,
        page=page,
        pageSize=pageSize,
        sortBy=sortBy,
        sortOrder=sortOrder
    )
    return await PackageService.get_all_packages(current_user.id, params)


# ============================================
# GET PACKAGE CATALOGUE (All users — browse & purchase)
# ============================================

@router.get("/catalogue", response_model=PackageCatalogueListResponse)
async def get_package_catalogue(
    search: Optional[str] = None,
    minPrice: Optional[float] = None,
    maxPrice: Optional[float] = None,
    page: int = 1,
    pageSize: int = 10,
    sortBy: str = "order",
    sortOrder: str = "asc",
    current_user=Depends(get_current_active_user)
):
    """
    Browse ALL active package plans — visible to every authenticated user.

    **Purpose:** Users need to see what packages are available before purchasing.
    This is the discovery endpoint for the package purchase flow, equivalent to
    GET /memberships/catalogue for the membership flow.

    **Returns:**
    - Active package plans only (isActive=True)
    - No ownership filter — pure catalogue view
    - Class details per plan
    - Price, duration, time restrictions

    **Permissions:** Any authenticated user (ADMIN, MANAGER, INSTRUCTOR, USER)
    """
    params = PackageSearchParams(
        search=search,
        minPrice=minPrice,
        maxPrice=maxPrice,
        page=page,
        pageSize=pageSize,
        sortBy=sortBy,
        sortOrder=sortOrder
    )
    return await PackageService.get_package_catalogue(params)


# ============================================
# PURCHASE PACKAGE
# ============================================

@router.post("/purchase", response_model=PurchasePackageResponse, status_code=status.HTTP_201_CREATED)
async def purchase_package(
    data: PurchasePackage,
    current_user=Depends(get_current_active_user)
):
    """
    Purchase a package plan — fully independent of the Membership module.

    **Workflow:**
    1. User browses GET /packages/catalogue and selects a plan.
    2. User calls this endpoint with the plan's packageId + their userId.
    3. System validates the plan is active and the user has no duplicate active purchase.
    4. Returns purchase confirmation with validity dates.

    **Duplicate Guard:** Cannot purchase the same package while an active purchase
    already exists (tracked via PaymentLog).

    **Independence Note:** This endpoint does NOT create or modify any Membership
    record. Packages and Memberships are entirely separate modules.

    **Request Body:**
    ```json
    {
      "packageId": "plan_id_here",
      "userId":    "user_id_here",
      "autoRenew": false
    }
    ```

    **Permissions:** Any authenticated user
    """
    return await PackageService.purchase_package(data, current_user.id)


# ============================================
# GET MY PACKAGES (Convenience endpoint — current user's purchases)
# ============================================

@router.get("/info-me", response_model=UserPackageListResponse)
async def get_my_packages(
    search: Optional[str] = None,
    minPrice: Optional[float] = None,
    maxPrice: Optional[float] = None,
    page: int = 1,
    pageSize: int = 10,
    sortBy: str = "order",
    sortOrder: str = "asc",
    current_user=Depends(get_current_active_user)
):
    """
    Get the current user's purchased packages WITH session tracking.

    **Purpose:** Lets a logged-in user view the package plans they have
    personally purchased, including how many sessions they have used and
    how many remain.

    **Session Fields (per package):**
    - `numberOfSessions` — total sessions set by Admin (null = unlimited)
    - `sessionsUsed` — how many classes this user has booked via this package
    - `sessionsRemaining` — sessions left (null = unlimited)

    **Excluded Classes:**
    - `excludedClasses` — IDs of classes NOT covered by this package
    - `excludedClassDetails` — full details of those excluded classes

    **Use case:** "My Packages" page in the member dashboard.

    **Permissions:** Any authenticated user (own purchases only)
    """
    params = PackageSearchParams(
        search=search,
        minPrice=minPrice,
        maxPrice=maxPrice,
        page=page,
        pageSize=pageSize,
        sortBy=sortBy,
        sortOrder=sortOrder
    )
    return await PackageService.get_my_packages(current_user.id, params)


# ============================================
# GET EXPIRING PACKAGES (Admin/Manager — renewal alerts)
# ============================================

@router.get("/expiring", response_model=PackageListResponse)
async def get_expiring_packages(
    days: int = 7,
    page: int = 1,
    pageSize: int = 10,
    sortBy: str = "order",
    sortOrder: str = "asc",
    current_user=Depends(get_current_active_user)
):
    """
    Get purchased packages expiring within the next X days (Admin/Manager only).

    **Purpose:** Proactive renewal reminder tool. Identifies users whose package
    purchases are approaching expiry so staff can reach out or trigger auto-reminders.

    **Default:** Packages expiring within the next 7 days.

    **Permissions:** ADMIN, MANAGER only
    """
    params = PackageSearchParams(
        page=page,
        pageSize=pageSize,
        sortBy=sortBy,
        sortOrder=sortOrder
    )
    return await PackageService.get_expiring_packages(current_user.id, days, params)


# ============================================
# GET PACKAGE BY ID
# ============================================

@router.get("/{package_id}", response_model=PackageResponse)
async def get_package_by_id(
    package_id: str,
    current_user=Depends(get_current_active_user)
):
    """
    Get full details of a specific package plan by its ID.

    **Returns:**
    - Full package plan details
    - List of allowed classes with their details
    - Price, duration, time restriction

    **Permissions:** Any authenticated user
    """
    return await PackageService.get_package_by_id(package_id, current_user.id)


# ============================================
# UPDATE PACKAGE
# ============================================

@router.patch("/{package_id}", response_model=PackageResponse)
async def update_package(
    package_id: str,
    data: PackageUpdate,
    current_user=Depends(get_current_active_user)
):
    """
    Update a package plan (Admin only).

    **UI Reference:** "Edit Package" modal
    - All fields are optional — send only what changes
    - Can update classes, restrictions, pricing, active status

    **Permissions:** ADMIN only
    """
    return await PackageService.update_package(package_id, data, current_user.id)


# ============================================
# DELETE PACKAGE
# ============================================

@router.delete("/{package_id}", status_code=status.HTTP_200_OK)
async def delete_package(
    package_id: str,
    current_user=Depends(get_current_active_user)
):
    """
    Permanently delete a package plan (Admin only).

    **Warning:** This removes the plan definition. Consider deactivating
    (isActive=false via PATCH) instead to preserve history.

    **Permissions:** ADMIN only
    """
    return await PackageService.delete_package(package_id, current_user.id)


# ============================================
# CANCEL PACKAGE PURCHASE
# ============================================

@router.post("/cancel", status_code=status.HTTP_200_OK)
async def cancel_package(
    data: CancelPackage,
    current_user=Depends(get_current_active_user)
):
    """
    Cancel a user's purchased package.

    **Purpose:** Marks the user's active package purchase as cancelled.
    The package plan definition remains intact for future purchases.

    **Use case:** User cancels their package before it expires.
    - Can include a refund amount
    - Can include a cancellation reason

    **Ownership:** Users can only cancel their own purchases.
    Admin/Manager can cancel on behalf of any user.

    **Permissions:** Any authenticated user (own purchases); ADMIN/MANAGER (any user)
    """
    return await PackageService.cancel_package(data, current_user.id)


# ============================================
# PAUSE PACKAGE PURCHASE
# ============================================

@router.post("/pause", status_code=status.HTTP_200_OK)
async def pause_package(
    data: PausePackage,
    current_user=Depends(get_current_active_user)
):
    """
    Pause a user's purchased package and extend its validity.

    **Purpose:** User requests a temporary pause (e.g., vacation, injury).
    The package's validity end date is extended by the pause duration.

    **Use case:** "Pause my package for 14 days while I'm on holiday"
    - Validity window extended by pauseDays
    - Pause history recorded in purchase metadata

    **Permissions:** Any authenticated user (own purchases); ADMIN/MANAGER (any user)
    """
    return await PackageService.pause_package(data, current_user.id)


# ============================================
# RENEW PACKAGE PURCHASE
# ============================================

@router.post("/renew", status_code=status.HTTP_200_OK)
async def renew_package(
    data: RenewPackage,
    current_user=Depends(get_current_active_user)
):
    """
    Renew a user's purchased package.

    **Purpose:** Extends the validity of an existing package purchase.
    Use when a user wants to continue an expired or near-expiry package
    without going through the full purchase flow again.

    **Extension:** Defaults to the original package duration; override with extendDays.

    **Permissions:** Any authenticated user (own purchases); ADMIN/MANAGER (any user)
    """
    return await PackageService.renew_package(data, current_user.id)


# ============================================
# VALIDATE PACKAGE ACCESS (Class booking gate)
# ============================================

@router.post("/validate-access", response_model=PackageAccessResponse)
async def validate_package_access(
    data: ValidatePackageAccess,
    current_user=Depends(get_current_active_user)
):
    """
    Check if a user can access a specific class via an active package purchase.

    **Purpose:** Pre-booking access gate. Called before a class booking is
    confirmed to verify the user's package grants access to that class.

    **Validates:**
    - User has an active, non-expired package purchase
    - The package's allowedClasses list includes the requested classId
    - Current time satisfies any time restriction on the package
    - Purchase is not cancelled or paused

    **Returns:**
    - hasAccess: true/false
    - reason: explains denial when hasAccess=false
    - package: the granting package plan details (when hasAccess=true)

    **Permissions:** Any authenticated user
    """
    return await PackageService.validate_package_access(data)


# ============================================
# EXPORT PACKAGES TO EXCEL
# ============================================

@router.get("/export/excel")
async def export_packages_to_excel(
    search: Optional[str] = None,
    isActive: Optional[bool] = None,
    current_user=Depends(get_current_active_user)
):
    """
    Export package plan definitions to an Excel file (Admin/Manager only).

    **Use case:** Admin exports the package catalogue for offline reporting,
    finance review, or bulk editing.

    **Permissions:** ADMIN, MANAGER only
    """
    excel_file = await PackageService.export_packages_to_excel(
        current_user.id,
        search=search,
        is_active=isActive
    )

    return Response(
        content=excel_file.read(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": "attachment; filename=packages_export.xlsx"
        }
    )