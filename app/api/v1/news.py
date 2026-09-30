"""
News API Router
===============
News = Admin/Manager creates news announcements for users to read.

Endpoints:
  GET    /news                    → Paginated news list + search (PUBLIC - all users)
  POST   /news                    → Add new news (Admin/Manager only)
  GET    /news/{id}               → Get news by ID (PUBLIC - all users)
  PATCH  /news/{id}               → Edit news (Admin/Manager only)
  DELETE /news/{id}               → Delete news (Admin/Manager only)

Expiry behaviour:
  - Admin/Manager GET /news  → ALL news returned (live + expired).
    Each item carries isExpired=True/False for the "Expired" admin label.
  - User/Instructor GET /news → only live (non-expired) news returned.
"""

from fastapi import APIRouter, Depends, File, Form, UploadFile, status
from typing import Optional

from app.models.news_model import (
    NewsCreate,
    NewsUpdate,
    NewsResponse,
    NewsListResponse,
    NewsSearchParams,
)
from app.services.news_service import NewsService
from app.core.cloudinary_service import upload_image
from app.api.v1.dependencies import get_current_active_user

router = APIRouter()


# ─────────────────────────────────────────────────────────────────
# GET ALL NEWS (Public - all authenticated users)
# ─────────────────────────────────────────────────────────────────

@router.get("", response_model=NewsListResponse)
async def get_all_news(
    search: Optional[str] = None,
    page: int = 1,
    pageSize: int = 10,
    sortBy: str = "publishedAt",
    sortOrder: str = "desc",
    current_user=Depends(get_current_active_user)
):
    """
    Get all news with search and pagination.

    **Role-aware expiry filtering:**
    - **Admin / Manager** → receives ALL news (live + expired). Expired items
      carry `isExpired: true` so the admin panel can display the "Expired" label.
    - **All other users** → only live, non-expired news is returned, keeping
      the in-app news feed clean.

    **Returns:** Brief news info (title, short description, thumbnail,
    published date, durationDays, expiresAt, isExpired)

    **Features:** Search by news title

    **Access:** All authenticated users (PUBLIC READ)
    """
    params = NewsSearchParams(
        search=search,
        page=page,
        pageSize=pageSize,
        sortBy=sortBy,
        sortOrder=sortOrder
    )
    return await NewsService.get_all_news(params, caller_role=current_user.role)


# ─────────────────────────────────────────────────────────────────
# CREATE NEWS
# ─────────────────────────────────────────────────────────────────

@router.post("", response_model=NewsResponse, status_code=status.HTTP_201_CREATED)
async def create_news(
    title: str = Form(..., description="News headline"),
    shortDescription: Optional[str] = Form(
        default=None,
        description="Brief summary shown on the card"
    ),
    durationDays: Optional[int] = Form(
        default=None,
        description=(
            "How many days from today the news remains visible in the user feed "
            "(e.g. 7 = one week, 30 = one month). "
            "Omit or leave blank for a permanent post that never expires."
        )
    ),

    # ── Image ──────────────────────────────────────────────────────
    thumbnail: UploadFile = File(default=None),

    current_user=Depends(get_current_active_user)
):
    """
    Add a new news announcement.

    **Content-Type:** `multipart/form-data`

    **Form fields:**
    | Field              | Type    | Required | Description                                         |
    |--------------------|---------|----------|-----------------------------------------------------|
    | `title`            | string  | ✅ Yes   | News headline                                       |
    | `shortDescription` | string  | No       | Brief summary shown on the card                     |
    | `durationDays`     | integer | No       | Visibility duration in days (≥ 1). Omit = permanent |
    | `thumbnail`        | file    | No       | Card thumbnail — JPEG/PNG/WEBP/GIF, max 5 MB        |

    **Expiry:**
    When `durationDays` is set, `expiresAt` is computed automatically as
    `publishedAt + durationDays`. Once that timestamp passes, the news is
    hidden from the user feed and marked "Expired" in the admin panel.

    **UI Reference:** "Add a new News" modal

    **Access:** Admin / Manager only
    """
    # Upload thumbnail to Cloudinary if a file was provided
    thumbnail_url: Optional[str] = None
    if thumbnail and thumbnail.filename:
        thumbnail_url = await upload_image(thumbnail, folder="news/thumbnails")

    data = NewsCreate(
        title=title,
        shortDescription=shortDescription,
        thumbnail=thumbnail_url,
        durationDays=durationDays,
    )

    return await NewsService.create_news(data, current_user.id)


# ─────────────────────────────────────────────────────────────────
# GET NEWS BY ID (Public - all authenticated users)
# ─────────────────────────────────────────────────────────────────

@router.get("/{news_id}", response_model=NewsResponse)
async def get_news_by_id(
    news_id: str,
    current_user=Depends(get_current_active_user)
):
    """
    Get full news details by ID.

    The response includes `isExpired` (computed) and `expiresAt` so the
    client always has the full expiry picture without a separate request.

    **Access:** All authenticated users (PUBLIC READ)
    """
    return await NewsService.get_news_by_id(news_id)


# ─────────────────────────────────────────────────────────────────
# UPDATE NEWS
# ─────────────────────────────────────────────────────────────────

@router.patch("/{news_id}", response_model=NewsResponse)
async def update_news(
    news_id: str,
    title: Optional[str] = Form(default=None, description="News headline"),
    shortDescription: Optional[str] = Form(
        default=None,
        description="Brief summary shown on the card"
    ),
    durationDays: Optional[int] = Form(
        default=None,
        description=(
            "Visibility duration in days from the original publish date. "
            "Omit entirely to leave expiry unchanged."
        )
    ),
    clearExpiry: bool = Form(
        default=False,
        description=(
            "Set to true to remove the expiry and make the news permanent. "
            "This is equivalent to setting durationDays=null in the JSON API."
        )
    ),

    # ── Image ──────────────────────────────────────────────────────
    thumbnail: UploadFile = File(default=None),

    current_user=Depends(get_current_active_user)
):
    """
    Edit news details.

    **Content-Type:** `multipart/form-data`

    **All fields optional — send only what needs to change.**

    | Field              | Type    | Description                                                            |
    |--------------------|---------|------------------------------------------------------------------------|
    | `title`            | string  | News headline                                                          |
    | `shortDescription` | string  | Brief summary shown on the card                                        |
    | `durationDays`     | integer | New duration in days (≥ 1). Recomputed from original `publishedAt`.    |
    | `clearExpiry`      | boolean | `true` → removes expiry, making news permanent. Default: `false`.      |
    | `thumbnail`        | file    | New thumbnail — JPEG/PNG/WEBP/GIF, max 5 MB. Replaces existing image. |

    **durationDays behaviour on update:**
    - New positive integer → `expiresAt` recomputed from the **original** `publishedAt`.
    - `clearExpiry=true` → removes expiry, making the news permanent.
    - Field omitted & `clearExpiry=false` → expiry left exactly as-is.

    **UI Reference:** "Edit" modal

    **Access:** Admin / Manager only
    """
    # Upload new thumbnail to Cloudinary if a file was provided
    thumbnail_url: Optional[str] = None
    if thumbnail and thumbnail.filename:
        thumbnail_url = await upload_image(thumbnail, folder="news/thumbnails")

    # Build the update payload respecting PATCH semantics —
    # only include keys the caller explicitly wants to change.
    update_kwargs: dict = {}

    if title is not None:
        update_kwargs["title"] = title
    if shortDescription is not None:
        update_kwargs["shortDescription"] = shortDescription
    if thumbnail_url is not None:
        update_kwargs["thumbnail"] = thumbnail_url

    # Expiry handling:
    #   clearExpiry=True   → durationDays=None clears expiry (permanent)
    #   durationDays set   → service recomputes expiresAt from original publishedAt
    #   neither provided   → expiry left unchanged (key omitted from payload)
    if clearExpiry:
        update_kwargs["durationDays"] = None
    elif durationDays is not None:
        update_kwargs["durationDays"] = durationDays

    data = NewsUpdate(**update_kwargs)

    return await NewsService.update_news(news_id, data, current_user.id)


# ─────────────────────────────────────────────────────────────────
# DELETE NEWS
# ─────────────────────────────────────────────────────────────────

@router.delete("/{news_id}", status_code=status.HTTP_200_OK)
async def delete_news(
    news_id: str,
    current_user=Depends(get_current_active_user)
):
    """
    Delete a news announcement.

    **UI Reference:** Delete button on news card

    **Access:** Admin / Manager only
    """
    return await NewsService.delete_news(news_id, current_user.id)