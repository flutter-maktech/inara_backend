"""
About Us API Router
===================
About Us = SINGLETON configuration for company information.
Admin/Manager creates and edits. All users can view.

Endpoints:
  POST   /about-us              → Create About Us (first time setup)
  GET    /about-us              → Get About Us info (PUBLIC - all users)
  PATCH  /about-us              → Edit About Us (Admin/Manager only)
  GET    /about-us/whatsapp     → Get WhatsApp redirect URL
  GET    /about-us/call         → Get phone call redirect URL
"""

from fastapi import APIRouter, Depends, status

from app.models.aboutus_model import (
    AboutUsCreate,
    AboutUsUpdate,
    AboutUsResponse,
    ContactActionResponse,
)
from app.services.aboutus_service import AboutUsService
from app.api.v1.dependencies import get_current_active_user

router = APIRouter()


# ─────────────────────────────────────────────────────────────────
# UTILITY ENDPOINTS (Must be defined BEFORE main endpoints)
# ─────────────────────────────────────────────────────────────────

@router.get("/whatsapp", response_model=ContactActionResponse)
async def get_whatsapp_redirect(
    current_user=Depends(get_current_active_user)
):
    """
    Get WhatsApp chat redirect URL.
    
    **Returns:**
    - WhatsApp URL (wa.me format)
    - Phone number
    
    **Usage:**
    ```javascript
    // Frontend can redirect user to WhatsApp
    const response = await fetch('/api/v1/about-us/whatsapp');
    const { redirectUrl } = await response.json();
    window.open(redirectUrl, '_blank');
    ```
    
    **UI Reference:** WhatsApp button on About Us page
    **Access:** All authenticated users
    """
    return await AboutUsService.get_whatsapp_redirect()


@router.get("/call", response_model=ContactActionResponse)
async def get_call_redirect(
    current_user=Depends(get_current_active_user)
):
    """
    Get phone call redirect URL.
    
    **Returns:**
    - Tel URL (tel: format)
    - Phone number
    
    **Usage:**
    ```javascript
    // Frontend can initiate phone call
    const response = await fetch('/api/v1/about-us/call');
    const { redirectUrl } = await response.json();
    window.location.href = redirectUrl; // Opens phone dialer
    ```
    
    **UI Reference:** Call Studio button on About Us page
    **Access:** All authenticated users
    """
    return await AboutUsService.get_call_redirect()


# ─────────────────────────────────────────────────────────────────
# CREATE ABOUT US (First time setup)
# ─────────────────────────────────────────────────────────────────

@router.post("", response_model=AboutUsResponse, status_code=status.HTTP_201_CREATED)
async def create_about_us(
    data: AboutUsCreate,
    current_user=Depends(get_current_active_user)
):
    """
    Create About Us information (first time setup).
    
    **SINGLETON PATTERN:**
    - Only one About Us record exists
    - If already exists, returns existing record
    
    **Request Body:**
    - Active Members (manual count)
    - Total Classes (manual count)
    - Total Instructors (manual count)
    - Our Story (text content)
    - Our Mission (text content)
    - Location (address)
    - Location Map Link (Google Maps URL)
    - Latitude (GPS coordinate, optional)
    - Longitude (GPS coordinate, optional)
    - Email
    - Phone Number (used for both WhatsApp and Call)
    - Instagram Account
    - Facebook Page
    - Website URL
    
    **IMPORTANT:** Stats are MANUAL inputs per client request.
    
    **UI Reference:** About Us page with "Edit About Us" modal
    **Access:** Admin / Manager only
    """
    return await AboutUsService.create_about_us(data, current_user.id)


# ─────────────────────────────────────────────────────────────────
# GET ABOUT US (Public - all authenticated users)
# ─────────────────────────────────────────────────────────────────

@router.get("", response_model=AboutUsResponse)
async def get_about_us(
    current_user=Depends(get_current_active_user)
):
    """
    Get About Us information.
    
    **Returns:**
    - Stats: Active Members, Classes, Instructors (manual counts)
    - Content: Our Story, Our Mission
    - Contact: Location, Map Link, Latitude, Longitude, Email, Phone, Instagram
    
    **UI Reference:** About Us page (left side of image)
    **Access:** All authenticated users (PUBLIC READ)
    """
    return await AboutUsService.get_about_us()


# ─────────────────────────────────────────────────────────────────
# UPDATE ABOUT US
# ─────────────────────────────────────────────────────────────────

@router.patch("", response_model=AboutUsResponse)
async def update_about_us(
    data: AboutUsUpdate,
    current_user=Depends(get_current_active_user)
):
    """
    Edit About Us information.
    
    **All fields are optional** - only provided fields are updated.
    
    **Editable Fields:**
    - Active Members (manual input)
    - Total Classes (manual input)
    - Total Instructors (manual input)
    - Our Story description
    - Our Mission description
    - Location address
    - Location Map Link
    - Latitude (GPS coordinate)
    - Longitude (GPS coordinate)
    - Email address
    - Phone Number
    - Instagram Account
    - Facebook Page
    - Website URL
    
    **IMPORTANT:** Stats are MANUAL inputs per client request.
    They are NOT auto-computed from database.
    
    **UI Reference:** "Edit About Us" modal (right side of image)
    **Access:** Admin / Manager only
    """
    return await AboutUsService.update_about_us(data, current_user.id)