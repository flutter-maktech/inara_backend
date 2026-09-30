from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime
from prisma.enums import MembershipStatus

# ============================================
# MEMBERSHIP MODELS (User's active memberships)
# ============================================

class MembershipBase(BaseModel):
    """Base membership model matching UI"""
    name: str  # Membership Title
    description: Optional[str] = None  # Membership Description 
    price: float  # Membership Price (QAR) 
    durationDays: int  # Valid For X months
    allowedClasses: List[str] = []  # Array of class IDs 
    timeRestriction: Optional[str] = None  # e.g., "Before 3:00 PM" 
    autoRenew: bool = False  # Auto Renew toggle

class MembershipCreate(MembershipBase):
    """
    Create a new membership plan template (Admin only).

    userId is NOT required in the request body — the Admin's own ID is
    automatically sourced from the JWT token server-side. This ensures
    catalogue templates are always owned by the authenticated Admin without
    exposing an unnecessary userId field in the API surface.
    """
    pass

class MembershipUpdate(BaseModel):
    """Update existing membership - all fields optional"""
    name: Optional[str] = None
    description: Optional[str] = None
    price: Optional[float] = None
    durationDays: Optional[int] = None
    allowedClasses: Optional[List[str]] = None
    timeRestriction: Optional[str] = None
    status: Optional[MembershipStatus] = None
    progress: Optional[float] = None
    autoRenew: Optional[bool] = None
    endDate: Optional[datetime] = None

class MembershipResponse(MembershipBase):
    """Membership response with full details"""
    id: str
    userId: str
    status: MembershipStatus
    progress: float
    startDate: datetime
    endDate: Optional[datetime]
    enrolledAt: datetime
    completedAt: Optional[datetime]
    cancelledAt: Optional[datetime]
    createdAt: datetime
    updatedAt: datetime
    
    # Computed fields
    daysRemaining: Optional[int] = None  # Days until expiry
    isExpired: bool = False
    totalClasses: Optional[int] = 0  # Count of allowed classes
    classDetails: Optional[List[dict]] = []  # Populated class info
    excludedClasses: Optional[List[str]] = []           # IDs of classes NOT in this membership
    excludedClassDetails: Optional[List[dict]] = []     # Full details of excluded classes
    
    # User info
    user: Optional[dict] = None
    
    class Config:
        from_attributes = True

class MembershipListResponse(BaseModel):
    """Paginated membership list"""
    memberships: List[MembershipResponse]
    total: int
    page: int
    pageSize: int
    totalPages: int

# ============================================
# SEARCH & FILTER MODELS
# ============================================

class MembershipSearchParams(BaseModel):
    """Search and filter parameters"""
    search: Optional[str] = None  # Search by name/description
    userId: Optional[str] = None  # Filter by user
    status: Optional[MembershipStatus] = None  # Filter by status
    minPrice: Optional[float] = None
    maxPrice: Optional[float] = None
    expiringInDays: Optional[int] = None  # Find memberships expiring in X days
    page: int = Field(1, ge=1)
    pageSize: int = Field(10, ge=1, le=100)
    sortBy: str = "createdAt"
    sortOrder: str = "desc"

# ============================================
# MEMBERSHIP ACTIONS
# ============================================

class CancelMembership(BaseModel):
    """Cancel a membership"""
    membershipId: str
    reason: Optional[str] = None
    refundAmount: Optional[float] = None

class PauseMembership(BaseModel):
    """Pause a membership"""
    membershipId: str
    pauseDays: int  # How many days to pause

class RenewMembership(BaseModel):
    """Renew a membership"""
    membershipId: str
    extendDays: Optional[int] = None  # Extend by X days

# ============================================
# MEMBERSHIP VALIDATION
# ============================================

class ValidateMembershipAccess(BaseModel):
    """Check if user can access a class via membership"""
    userId: str
    classId: str
    requestedTime: Optional[datetime] = None  # Time of booking attempt

class MembershipAccessResponse(BaseModel):
    """Response for access validation"""
    hasAccess: bool
    reason: Optional[str] = None  # If no access, explain why
    membership: Optional[MembershipResponse] = None
    
    
# ============================================
# MEMBERSHIP CATALOGUE (Public browsing — all users)
# ============================================

class MembershipCatalogue(BaseModel):
    """
    A membership plan available for purchase.
    Shown to ALL authenticated users so they can browse before buying.
    These are admin-created templates — no userId on the plan itself.
    """
    id: str
    name: str
    description: Optional[str] = None
    price: float
    durationDays: int
    allowedClasses: List[str] = []
    timeRestriction: Optional[str] = None
    autoRenew: bool
    status: MembershipStatus
    totalClasses: Optional[int] = 0
    classDetails: Optional[List[dict]] = []
    excludedClasses: Optional[List[str]] = []           # IDs of classes NOT in this membership
    excludedClassDetails: Optional[List[dict]] = []     # Full details of excluded classes
    createdAt: datetime
    updatedAt: datetime

    class Config:
        from_attributes = True

class MembershipCatalogueListResponse(BaseModel):
    """Paginated catalogue of membership plans"""
    memberships: List[MembershipCatalogue]
    total: int
    page: int
    pageSize: int
    totalPages: int

# ============================================
# PURCHASE MEMBERSHIP
# ============================================

class PurchaseMembership(BaseModel):
    """
    User purchases an existing admin-created membership plan.

    Workflow:
      1. Admin creates a Membership plan (template).
      2. User calls POST /memberships/purchase with only the membershipId.
      3. Service resolves the purchasing user from the JWT token (current_user).
      4. Service clones the plan into a brand-new membership record for that user.

    userId is NOT required in the request body — it is sourced automatically
    from the authenticated user's JWT token. This eliminates the security risk
    of a caller supplying an arbitrary userId to purchase on behalf of someone
    else without authorisation.
    """
    membershipId: str   # ID of the admin-created plan to clone
    autoRenew: bool = False

class PurchaseMembershipResponse(BaseModel):
    """Response after a successful membership purchase"""
    message: str
    membershipId: str      
    membershipName: str
    validFrom: datetime
    validUntil: Optional[datetime]