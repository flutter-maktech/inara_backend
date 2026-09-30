from pydantic import BaseModel, Field, field_validator
from typing import Optional, List
from datetime import datetime


# ============================================
# NEWS MODELS (Admin/Manager creates news)
# ============================================

class NewsBase(BaseModel):
    """Base news model matching UI"""
    title: str  # News Title
    shortDescription: Optional[str] = None  # Short description
    thumbnail: Optional[str] = None  # Thumbnail image URL
    # How many days the news remains visible in the feed after publishing.
    # NULL / omitted → news never expires (permanent post).
    # Must be a positive integer when provided (e.g. 7 = visible for one week).
    durationDays: Optional[int] = Field(
        default=None,
        ge=1,
        description=(
            "Visibility duration in days from the publish date. "
            "Leave empty for a permanent post that never expires."
        ),
        examples=[7, 14, 30],
    )

    @field_validator("durationDays", mode="before")
    @classmethod
    def validate_duration_days(cls, v: Optional[int]) -> Optional[int]:
        if v is None:
            return v
        if not isinstance(v, int) or v < 1:
            raise ValueError("durationDays must be a positive integer (≥ 1)")
        return v


class NewsCreate(NewsBase):
    """
    Create a new news announcement.

    **durationDays** controls how long the news is visible in the user feed:
    - Provide a positive integer (e.g. `7`) → news disappears from the feed
      after that many days and is labelled "Expired" in the admin panel.
    - Omit / set to `null` → news stays visible permanently.
    """
    pass


class NewsUpdate(BaseModel):
    """
    Update existing news — all fields optional.

    **durationDays** notes:
    - Providing a new value recalculates `expiresAt` from the original
      `publishedAt` date, so the duration is always measured from first publish.
    - Set to `null` explicitly to remove expiry and make the news permanent.
    """
    title: Optional[str] = None
    shortDescription: Optional[str] = None
    thumbnail: Optional[str] = None
    durationDays: Optional[int] = Field(
        default=None,
        ge=1,
        description=(
            "Visibility duration in days from the publish date. "
            "Set to null to make the news permanent (remove expiry)."
        ),
    )


class NewsBrief(BaseModel):
    """
    Brief news info for the listing card grid.

    **isExpired** — computed server-side:
    - `True`  → the news has passed its expiry date (admin sees "Expired" label).
    - `False` → the news is still live.
    - Only non-expired news is returned to regular users; admins receive all
      records (both live and expired) so they can manage the feed.
    """
    id: str
    title: str
    shortDescription: Optional[str] = None
    thumbnail: Optional[str] = None
    publishedAt: datetime
    # Duration & Expiry
    durationDays: Optional[int] = None    # days until expiry; None = permanent
    expiresAt: Optional[datetime] = None  # absolute expiry timestamp; None = never
    isExpired: bool = False               # True when expiresAt < now (admin label)

    class Config:
        from_attributes = True


class NewsResponse(NewsBase):
    """
    Full news response — returned on create, get-by-id, update.

    Includes computed expiry fields so the client always has the full picture
    without a separate request.
    """
    id: str
    publishedAt: datetime
    # Duration & Expiry
    expiresAt: Optional[datetime] = None  # absolute expiry timestamp; None = never
    isExpired: bool = False               # True when expiresAt < now
    createdAt: datetime
    updatedAt: datetime

    class Config:
        from_attributes = True


class NewsListResponse(BaseModel):
    """Paginated news list"""
    news: List[NewsBrief]
    total: int
    page: int
    pageSize: int
    totalPages: int


# ============================================
# SEARCH & FILTER MODELS
# ============================================

class NewsSearchParams(BaseModel):
    """Search and filter parameters"""
    search: Optional[str] = None  # Search by title
    page: int = Field(1, ge=1)
    pageSize: int = Field(10, ge=1, le=100)
    sortBy: str = "publishedAt"
    sortOrder: str = "desc"