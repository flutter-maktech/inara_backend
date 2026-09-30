"""
AboutUsService — Business logic for About Us management.

About Us is a SINGLETON - only one record exists globally.
Admin/Manager can create/edit it. All users can view it.
"""

from datetime import datetime, timezone
from typing import Optional
from urllib.parse import quote

from fastapi import HTTPException, status

from app.db.db_client import prisma
from app.models.aboutus_model import (
    AboutUsCreate,
    AboutUsUpdate,
    AboutUsResponse,
    ContactActionResponse,
)
from prisma.enums import UserRole


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


class AboutUsService:
    """
    Enterprise-grade About Us service.
    
    SINGLETON PATTERN:
    - Only ONE AboutUs record exists
    - First create initializes it
    - Subsequent creates return existing record
    - Update modifies the single record
    - Get returns the single record
    
    IMPORTANT: Stats are MANUAL per client request.
    """
    
    # ─────────────────────────────────────────
    # HELPER: admin/manager guard
    # ─────────────────────────────────────────
    
    @staticmethod
    async def _require_admin_or_manager(user_id: str) -> None:
        """Only Admin/Manager can create/edit About Us"""
        user = await prisma.user.find_unique(where={"id": user_id})
        if not user or user.role not in [UserRole.ADMIN, UserRole.MANAGER]:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Admin or Manager access required"
            )
    
    # ─────────────────────────────────────────
    # CREATE ABOUT US (First time setup)
    # ─────────────────────────────────────────
    
    @staticmethod
    async def create_about_us(
        data: AboutUsCreate,
        created_by_user_id: str
    ) -> AboutUsResponse:
        """
        Create About Us record (singleton pattern).
        
        If record already exists, returns existing record instead of creating.
        Only Admin/Manager can create.
        """
        await AboutUsService._require_admin_or_manager(created_by_user_id)
        
        # Check if About Us already exists (singleton)
        existing = await prisma.aboutus.find_first()
        if existing:
            return AboutUsResponse(**existing.model_dump())
        
        # Create new About Us record
        about_us = await prisma.aboutus.create(
            data=data.model_dump()
        )
        
        return AboutUsResponse(**about_us.model_dump())
    
    # ─────────────────────────────────────────
    # GET ABOUT US (Public - for all users)
    # ─────────────────────────────────────────
    
    @staticmethod
    async def get_about_us() -> AboutUsResponse:
        """
        Get About Us information.
        
        Accessible by all authenticated users (public read).
        Returns the single About Us record.
        """
        about_us = await prisma.aboutus.find_first()
        
        if not about_us:
            # Return default empty About Us if not created yet
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="About Us information not yet configured. Please contact administrator."
            )
        
        return AboutUsResponse(**about_us.model_dump())
    
    # ─────────────────────────────────────────
    # UPDATE ABOUT US
    # ─────────────────────────────────────────
    
    @staticmethod
    async def update_about_us(
        data: AboutUsUpdate,
        updated_by_user_id: str
    ) -> AboutUsResponse:
        """
        Update About Us information.
        Only Admin/Manager can update.
        
        IMPORTANT: All stats are MANUAL per client request.
        """
        await AboutUsService._require_admin_or_manager(updated_by_user_id)
        
        # Get existing About Us record
        about_us = await prisma.aboutus.find_first()
        if not about_us:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="About Us not found. Please create it first."
            )
        
        # Update About Us with only provided fields
        update_data = data.model_dump(exclude_unset=True)
        
        if not update_data:
            # Nothing to update - return current state
            return AboutUsResponse(**about_us.model_dump())
        
        updated_about_us = await prisma.aboutus.update(
            where={"id": about_us.id},
            data=update_data
        )
        
        return AboutUsResponse(**updated_about_us.model_dump())
    
    # ─────────────────────────────────────────
    # WHATSAPP REDIRECT (Utility endpoint)
    # ─────────────────────────────────────────
    
    @staticmethod
    async def get_whatsapp_redirect() -> ContactActionResponse:
        """
        Generate WhatsApp chat redirect URL.
        
        Format: https://wa.me/<phone_number>
        Opens WhatsApp chat with the configured phone number.
        """
        about_us = await prisma.aboutus.find_first()
        
        if not about_us or not about_us.phoneNumber:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Phone number not configured in About Us"
            )
        
        # Clean phone number (remove spaces, dashes, parentheses)
        clean_phone = about_us.phoneNumber.replace(" ", "").replace("-", "").replace("(", "").replace(")", "")
        
        # WhatsApp URL format: https://wa.me/1234567890
        # Remove leading + if present
        if clean_phone.startswith("+"):
            clean_phone = clean_phone[1:]
        
        whatsapp_url = f"https://wa.me/{clean_phone}"
        
        return ContactActionResponse(
            action="whatsapp",
            redirectUrl=whatsapp_url,
            phoneNumber=about_us.phoneNumber,
            message="Redirecting to WhatsApp chat"
        )
    
    # ─────────────────────────────────────────
    # CALL REDIRECT (Utility endpoint)
    # ─────────────────────────────────────────
    
    @staticmethod
    async def get_call_redirect() -> ContactActionResponse:
        """
        Generate direct phone call URL.
        
        Format: tel:<phone_number>
        Opens phone dialer with the configured phone number.
        """
        about_us = await prisma.aboutus.find_first()
        
        if not about_us or not about_us.phoneNumber:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Phone number not configured in About Us"
            )
        
        # Tel URL format: tel:+1234567890
        call_url = f"tel:{about_us.phoneNumber}"
        
        return ContactActionResponse(
            action="call",
            redirectUrl=call_url,
            phoneNumber=about_us.phoneNumber,
            message="Initiating phone call"
        )