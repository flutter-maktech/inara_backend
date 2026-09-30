from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime

# ============================================
# PACKAGE MODELS (Admin creates package plans)
# ============================================

class PackageBase(BaseModel):
    """Base package model matching UI"""
    name: str                                   # Package Title
    description: Optional[str] = None          # Package Description
    price: float                                # Package Price (QAR)
    discountPrice: Optional[float] = None      # Optional discount
    durationDays: int                           # Validity in days (30, 90, 365, etc.)
    allowedClasses: List[str] = []             # Array of class IDs (included classes)
    numberOfSessions: Optional[int] = None     # Total sessions Admin sets (None = unlimited)
    timeRestriction: Optional[str] = None      # e.g., "Before 3:00 PM"
    autoRenew: bool = False                    # Auto Renew toggle


class PackageCreate(PackageBase):
    """Create new package plan (Admin/Manager only)"""
    order: int = 0  # For sorting in UI


class PackageUpdate(BaseModel):
    """Update existing package — all fields optional"""
    name: Optional[str] = None
    description: Optional[str] = None
    price: Optional[float] = None
    discountPrice: Optional[float] = None
    durationDays: Optional[int] = None
    allowedClasses: Optional[List[str]] = None
    numberOfSessions: Optional[int] = None
    timeRestriction: Optional[str] = None
    autoRenew: Optional[bool] = None
    isActive: Optional[bool] = None
    order: Optional[int] = None


class PackageResponse(PackageBase):
    """Package plan response with full details"""
    id: str
    isActive: bool
    order: int
    createdAt: datetime
    updatedAt: datetime

    # Computed fields
    totalClasses: Optional[int] = 0
    classDetails: Optional[List[dict]] = []
    excludedClasses: Optional[List[str]] = []          # IDs of classes NOT in this package
    excludedClassDetails: Optional[List[dict]] = []    # Full details of excluded classes

    class Config:
        from_attributes = True


class PackageListResponse(BaseModel):
    """Paginated package plan list"""
    packages: List[PackageResponse]
    total: int
    page: int
    pageSize: int
    totalPages: int


# ============================================
# SEARCH & FILTER MODELS
# ============================================

class PackageSearchParams(BaseModel):
    """Search and filter parameters for package plans"""
    search: Optional[str] = None
    minPrice: Optional[float] = None
    maxPrice: Optional[float] = None
    isActive: Optional[bool] = None
    page: int = Field(1, ge=1)
    pageSize: int = Field(10, ge=1, le=100)
    sortBy: str = "order"
    sortOrder: str = "asc"


# ============================================
# PACKAGE CATALOGUE (Public browsing — all users)
# ============================================

class PackageCatalogue(BaseModel):
    """
    A package plan available for purchase.
    Shown to ALL authenticated users so they can browse before buying.
    These are admin-created plan definitions — no ownership filter.
    """
    id: str
    name: str
    description: Optional[str] = None
    price: float
    discountPrice: Optional[float] = None
    durationDays: int
    allowedClasses: List[str] = []
    numberOfSessions: Optional[int] = None             # Total sessions (None = unlimited)
    timeRestriction: Optional[str] = None
    autoRenew: bool
    isActive: bool
    totalClasses: Optional[int] = 0
    classDetails: Optional[List[dict]] = []
    excludedClasses: Optional[List[str]] = []          # IDs of classes NOT in this package
    excludedClassDetails: Optional[List[dict]] = []    # Full details of excluded classes
    createdAt: datetime
    updatedAt: datetime

    class Config:
        from_attributes = True


class PackageCatalogueListResponse(BaseModel):
    """Paginated catalogue of available package plans"""
    packages: List[PackageCatalogue]
    total: int
    page: int
    pageSize: int
    totalPages: int


# ============================================
# PURCHASE PACKAGE
# ============================================

class PurchasePackage(BaseModel):
    """
    User purchases a package plan.
    Records the purchase independently via PaymentLog — no Membership dependency.
    """
    packageId: str      # ID of the active package plan to purchase
    userId: str         # The purchasing user
    autoRenew: bool = False


class PurchasePackageResponse(BaseModel):
    """Response after a successful package purchase"""
    message: str
    packageId: str
    packageName: str
    validFrom: datetime
    validUntil: datetime


# ============================================
# PACKAGE ACTIONS
# ============================================

class CancelPackage(BaseModel):
    """Cancel a purchased package (tracked via PaymentLog referenceId)"""
    packageId: str          # The package plan ID
    userId: str             # The user whose purchase to cancel
    reason: Optional[str] = None
    refundAmount: Optional[float] = None


class PausePackage(BaseModel):
    """
    Pause a purchased package.
    Pause state is advisory — tracked via PaymentLog metadata.
    """
    packageId: str          # The package plan ID
    userId: str             # The user whose purchase to pause
    pauseDays: int          # How many days to pause (extends validity)


class RenewPackage(BaseModel):
    """Renew a purchased package"""
    packageId: str          # The package plan ID
    userId: str             # The user whose package to renew
    extendDays: Optional[int] = None  # Override duration; defaults to package.durationDays


class ValidatePackageAccess(BaseModel):
    """Check if a user can access a class via their active package purchase"""
    userId: str
    classId: str
    requestedTime: Optional[datetime] = None


class PackageAccessResponse(BaseModel):
    """Response for package access validation"""
    hasAccess: bool
    reason: Optional[str] = None       # Explains denial if hasAccess=False
    package: Optional[PackageResponse] = None  # The granting package plan (if access granted)


# ============================================
# USER PACKAGE INSTANCE (with session tracking)
# ============================================

class UserPackageInstance(BaseModel):
    """
    A user's active package purchase instance.
    Extends PackageResponse with session tracking specific to this user's purchase.
    """
    id: str                             # Membership record ID (the user-owned instance)
    packageId: Optional[str] = None     # Linked Package plan ID (if resolvable)
    name: str
    description: Optional[str] = None
    price: float
    durationDays: int
    allowedClasses: List[str] = []
    numberOfSessions: Optional[int] = None         # Total sessions from package template
    sessionsUsed: Optional[int] = None             # How many sessions this user has used
    sessionsRemaining: Optional[int] = None        # Remaining sessions (None = unlimited)
    timeRestriction: Optional[str] = None
    autoRenew: bool = False
    isActive: bool = True
    status: str
    startDate: datetime
    endDate: Optional[datetime] = None
    daysRemaining: Optional[int] = None
    totalClasses: Optional[int] = 0
    classDetails: Optional[List[dict]] = []
    excludedClasses: Optional[List[str]] = []
    excludedClassDetails: Optional[List[dict]] = []
    createdAt: datetime
    updatedAt: datetime

    class Config:
        from_attributes = True


class UserPackageListResponse(BaseModel):
    """Paginated list of user's active package instances"""
    packages: List[UserPackageInstance]
    total: int
    page: int
    pageSize: int
    totalPages: int