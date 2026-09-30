from pydantic import BaseModel, EmailStr, Field
from typing import Optional, List
from datetime import datetime
from enum import Enum


# ============================================
# ENUMS
# ============================================

class InvitationStatusEnum(str, Enum):
    PENDING = "PENDING"
    ACCEPTED = "ACCEPTED"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"


class VisibilityStatusEnum(str, Enum):
    VISIBLE = "VISIBLE"
    HIDDEN = "HIDDEN"


class UserRoleEnum(str, Enum):
    ADMIN = "ADMIN"
    MANAGER = "MANAGER"
    INSTRUCTOR = "INSTRUCTOR"
    USER = "USER"


# ============================================
# INVITATION MODELS
# ============================================

class InviteTeamMember(BaseModel):
    """Invite a new team member by email"""
    email: EmailStr
    role: UserRoleEnum = Field(..., description="Role to assign: ADMIN, MANAGER, or INSTRUCTOR")


class InvitationResponse(BaseModel):
    """Invitation record response"""
    id: str
    email: str
    role: str
    status: str
    inviteToken: str
    invitedBy: str
    invitedByName: Optional[str] = None
    expiresAt: datetime
    acceptedAt: Optional[datetime] = None
    createdAt: datetime
    
    class Config:
        from_attributes = True


class InvitationListResponse(BaseModel):
    """Paginated invitation list"""
    invitations: List[InvitationResponse]
    total: int
    page: int
    pageSize: int
    totalPages: int


class AcceptInvitationRequest(BaseModel):
    """Accept invitation and set password"""
    inviteToken: str
    password: str = Field(..., min_length=8, description="Password (min 8 characters)")
    name: str
    phone: Optional[str] = None


# ============================================
# PERMISSION RULE MODELS
# ============================================

class PermissionRuleBase(BaseModel):
    """Base permission rule"""
    moduleName: str = Field(..., description="Module/Page name (e.g., 'Dashboard/Overview')")
    adminAccess: VisibilityStatusEnum = VisibilityStatusEnum.VISIBLE
    managerAccess: VisibilityStatusEnum = VisibilityStatusEnum.VISIBLE
    instructorAccess: VisibilityStatusEnum = VisibilityStatusEnum.HIDDEN
    displayOrder: int = 0


class PermissionRuleCreate(PermissionRuleBase):
    """Create new permission rule"""
    pass


class PermissionRuleUpdate(BaseModel):
    """Update permission rule - all fields optional"""
    moduleName: Optional[str] = None
    adminAccess: Optional[VisibilityStatusEnum] = None
    managerAccess: Optional[VisibilityStatusEnum] = None
    instructorAccess: Optional[VisibilityStatusEnum] = None
    displayOrder: Optional[int] = None


class PermissionRuleResponse(PermissionRuleBase):
    """Permission rule response"""
    id: str
    createdAt: datetime
    updatedAt: datetime
    
    class Config:
        from_attributes = True


class VisibilityMatrixResponse(BaseModel):
    """Complete visibility matrix"""
    rules: List[PermissionRuleResponse]
    total: int


# ============================================
# TERMS & CONDITIONS MODELS
# ============================================

class TermsConditionBase(BaseModel):
    """Base Terms & Conditions model"""
    introduction: Optional[str] = None
    eligibility: Optional[str] = None
    healthDisclaimer: Optional[str] = None
    userResponsibility: Optional[str] = None
    accountUsage: Optional[str] = None
    subscriptionsPayments: Optional[str] = None
    version: str = "1.0"


class TermsConditionCreate(TermsConditionBase):
    """Create Terms & Conditions"""
    pass


class TermsConditionUpdate(BaseModel):
    """Update Terms & Conditions - all fields optional"""
    introduction: Optional[str] = None
    eligibility: Optional[str] = None
    healthDisclaimer: Optional[str] = None
    userResponsibility: Optional[str] = None
    accountUsage: Optional[str] = None
    subscriptionsPayments: Optional[str] = None
    version: Optional[str] = None
    isActive: Optional[bool] = None


class TermsConditionResponse(TermsConditionBase):
    """Terms & Conditions response"""
    id: str
    isActive: bool
    createdAt: datetime
    updatedAt: datetime
    
    class Config:
        from_attributes = True


# ============================================
# ACTION PERMISSIONS (Per role)
# ============================================

class RoleActionPermissions(BaseModel):
    """Action permissions per role (from UI Image 2)"""
    role: str
    description: str
    permissions: List[str]


class ActionPermissionsResponse(BaseModel):
    """All role action permissions"""
    admin: RoleActionPermissions
    manager: RoleActionPermissions
    instructor: RoleActionPermissions