from pydantic import BaseModel, EmailStr, Field
from typing import Optional
from datetime import datetime


# ============================================
# ABOUT US MODELS
# ============================================

class AboutUsBase(BaseModel):
    """
    Base About Us model matching UI.
    
    IMPORTANT: Stats (activeMembers, totalClasses, totalInstructors)
    are MANUAL inputs per client request, NOT auto-computed.
    """
    # Stats (Manual - displayed in top cards)
    activeMembers: int = Field(default=0, ge=0, description="Manual count of active members")
    totalClasses: int = Field(default=0, ge=0, description="Manual count of classes")
    totalInstructors: int = Field(default=0, ge=0, description="Manual count of instructors")

    # Content Sections
    ourStory: Optional[str] = Field(None, description="Our Story text content")
    ourMission: Optional[str] = Field(None, description="Our Mission text content")

    # Contact Information
    location: Optional[str] = Field(None, description="Physical address")
    locationMapLink: Optional[str] = Field(None, description="Google Maps link")
    latitude: Optional[float] = Field(None, description="GPS latitude coordinate")
    longitude: Optional[float] = Field(None, description="GPS longitude coordinate")
    email: Optional[EmailStr] = None
    phoneNumber: Optional[str] = Field(None, description="Phone for both WhatsApp and Call")
    instagramAccount: Optional[str] = Field(None, description="Instagram handle or URL")
    facebookPage: Optional[str] = Field(None, description="Facebook page URL")
    websiteUrl: Optional[str] = Field(None, description="Official website URL")


class AboutUsCreate(AboutUsBase):
    """Create About Us (first time setup)"""
    pass


class AboutUsUpdate(BaseModel):
    """
    Update About Us - all fields optional.
    PATCH semantics - only provided fields are updated.
    """
    activeMembers: Optional[int] = Field(None, ge=0)
    totalClasses: Optional[int] = Field(None, ge=0)
    totalInstructors: Optional[int] = Field(None, ge=0)
    ourStory: Optional[str] = None
    ourMission: Optional[str] = None
    location: Optional[str] = None
    locationMapLink: Optional[str] = None
    latitude: Optional[float] = Field(None, description="GPS latitude coordinate")
    longitude: Optional[float] = Field(None, description="GPS longitude coordinate")
    email: Optional[EmailStr] = None
    phoneNumber: Optional[str] = None
    instagramAccount: Optional[str] = None
    facebookPage: Optional[str] = Field(None, description="Facebook page URL")
    websiteUrl: Optional[str] = Field(None, description="Official website URL")


class AboutUsResponse(AboutUsBase):
    """Full About Us response"""
    id: str
    createdAt: datetime
    updatedAt: datetime

    class Config:
        from_attributes = True


# ============================================
# HELPER MODELS FOR WHATSAPP & CALL FEATURES
# ============================================

class ContactActionResponse(BaseModel):
    """Response for WhatsApp/Call redirect actions"""
    action: str          # "whatsapp" or "call"
    redirectUrl: str
    phoneNumber: str
    message: str