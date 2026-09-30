"""
NewsService — Business logic for News Management.

News = Admin/Manager creates news announcements.
Users can view/read these news.

Expiry logic:
  - durationDays (optional Int): how many days from publishedAt the news is
    visible in the user feed. NULL means the news never expires.
  - expiresAt (optional DateTime): computed as publishedAt + durationDays.
    NULL when durationDays is not set (permanent).
  - isExpired (computed, not stored): True when expiresAt is set and < now.

Behaviour per role:
  - Regular users (USER / INSTRUCTOR):
      GET /news  →  only non-expired news returned (feed is clean).
  - Admins / Managers:
      GET /news  →  ALL news returned (live + expired), each record carries
                    isExpired=True/False so the admin panel can show the
                    "Expired" label on stale items.
"""

from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any

from fastapi import HTTPException, status

from app.db.db_client import prisma
from app.models.news_model import (
    NewsCreate,
    NewsUpdate,
    NewsBrief,
    NewsResponse,
    NewsListResponse,
    NewsSearchParams,
)
from prisma.enums import UserRole
from app.core.permissions import can as _policy_can, Resource as _Resource


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _compute_expires_at(
    published_at: datetime,
    duration_days: Optional[int],
) -> Optional[datetime]:
    """
    Return the absolute expiry timestamp, or None if the news is permanent.
    Always measured from published_at so editing duration is predictable.
    """
    if duration_days is None:
        return None
    return published_at + timedelta(days=duration_days)


def _is_expired(expires_at: Optional[datetime]) -> bool:
    """True when an expiry timestamp exists and has already passed."""
    if expires_at is None:
        return False
    # Make both sides timezone-aware for a safe comparison
    now = _now_utc()
    if expires_at.tzinfo is None:
        # DB returned a naive datetime — treat as UTC
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    return expires_at < now


def _build_news_response(news) -> NewsResponse:
    """Build a full NewsResponse from a Prisma News record."""
    expires_at = getattr(news, "expiresAt", None)
    return NewsResponse(
        id=news.id,
        title=news.title,
        shortDescription=news.shortDescription,
        thumbnail=news.thumbnail,
        durationDays=getattr(news, "durationDays", None),
        expiresAt=expires_at,
        isExpired=_is_expired(expires_at),
        publishedAt=news.publishedAt,
        createdAt=news.createdAt,
        updatedAt=news.updatedAt,
    )


def _build_news_brief(news) -> NewsBrief:
    """Build a NewsBrief from a Prisma News record."""
    expires_at = getattr(news, "expiresAt", None)
    return NewsBrief(
        id=news.id,
        title=news.title,
        shortDescription=news.shortDescription,
        thumbnail=news.thumbnail,
        durationDays=getattr(news, "durationDays", None),
        expiresAt=expires_at,
        isExpired=_is_expired(expires_at),
        publishedAt=news.publishedAt,
    )


class NewsService:
    """
    Enterprise-grade News service.

    Features:
    - CRUD operations for news
    - Search by title
    - Duration-based expiry: news auto-hides from the user feed after
      durationDays; admins always see everything with an isExpired label.
    - Public read access for all authenticated users
    - Write access for Admin/Manager only
    """

    # ─────────────────────────────────────────
    # HELPER: admin/manager guard
    # ─────────────────────────────────────────

    @staticmethod
    async def _require_admin_or_manager(user_id: str) -> None:
        user = await prisma.user.find_unique(where={"id": user_id})
        if not user or user.role not in [UserRole.ADMIN, UserRole.MANAGER]:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Admin or Manager access required"
            )

    # ─────────────────────────────────────────
    # HELPER: admin-only guard (deletes — Manager cannot delete)
    # ─────────────────────────────────────────

    @staticmethod
    async def _require_admin(user_id: str) -> None:
        """
        Admin-only gate, enforced via the central RBAC matrix.
        Used for DELETE operations — Manager is restricted from deletion
        on every resource per the latest policy.
        """
        user = await prisma.user.find_unique(where={"id": user_id})
        if not user or not _policy_can(str(user.role), _Resource.NEWS, "delete"):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Admin access required to delete news"
            )

    # ─────────────────────────────────────────
    # CREATE NEWS
    # ─────────────────────────────────────────

    @staticmethod
    async def create_news(
        data: NewsCreate,
        created_by_user_id: str
    ) -> NewsResponse:
        """
        Create a new news announcement.
        Only Admin/Manager can create news.

        If durationDays is provided, expiresAt is computed as
        publishedAt + durationDays. Omitting durationDays creates a
        permanent post (never expires).
        """
        await NewsService._require_admin_or_manager(created_by_user_id)

        published_at = _now_utc()
        expires_at = _compute_expires_at(published_at, data.durationDays)

        news = await prisma.news.create(
            data={
                "title":            data.title,
                "shortDescription": data.shortDescription,
                "thumbnail":        data.thumbnail,
                "durationDays":     data.durationDays,
                "expiresAt":        expires_at,
                "publishedAt":      published_at,
            }
        )

        return _build_news_response(news)

    # ─────────────────────────────────────────
    # GET ALL NEWS (role-aware)
    # ─────────────────────────────────────────

    @staticmethod
    async def get_all_news(
        params: NewsSearchParams,
        caller_role: str = UserRole.USER,
    ) -> NewsListResponse:
        """
        Get all news with search and pagination.

        Role behaviour:
          • Admin / Manager  → returns ALL news (live + expired).
            Each item carries isExpired=True/False so the admin panel
            can render the "Expired" label on stale records.
          • All other roles  → only live (non-expired) news is returned,
            keeping the user feed clean.

        Accessible by all authenticated users (public read).
        """
        where_clause: Dict[str, Any] = {}

        # Search by title
        if params.search:
            where_clause["title"] = {
                "contains": params.search,
                "mode": "insensitive",
            }

        # For non-admin roles, exclude expired news from the feed.
        # A news item is expired when expiresAt is set AND expiresAt < now.
        # We filter with an OR to include:
        #   (a) news that has no expiry at all (permanent), OR
        #   (b) news whose expiresAt is in the future (still live).
        is_admin_or_manager = caller_role in [UserRole.ADMIN, UserRole.MANAGER]
        if not is_admin_or_manager:
            now = _now_utc()
            where_clause["OR"] = [
                {"expiresAt": None},
                {"expiresAt": {"gt": now}},
            ]

        # Count total (with current filter)
        total = await prisma.news.count(where=where_clause)

        # Pagination
        skip = (params.page - 1) * params.pageSize
        total_pages = max(1, (total + params.pageSize - 1) // params.pageSize)

        # Fetch news
        news_items = await prisma.news.find_many(
            where=where_clause,
            skip=skip,
            take=params.pageSize,
            order={params.sortBy: params.sortOrder},
        )

        # Build brief responses (isExpired computed per item)
        news_brief = [_build_news_brief(n) for n in news_items]

        return NewsListResponse(
            news=news_brief,
            total=total,
            page=params.page,
            pageSize=params.pageSize,
            totalPages=total_pages,
        )

    # ─────────────────────────────────────────
    # GET NEWS BY ID (Public - for users)
    # ─────────────────────────────────────────

    @staticmethod
    async def get_news_by_id(news_id: str) -> NewsResponse:
        """
        Get full news details by ID.
        Accessible by all authenticated users.
        isExpired is computed and included in the response.
        """
        news = await prisma.news.find_unique(where={"id": news_id})

        if not news:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="News not found"
            )

        return _build_news_response(news)

    # ─────────────────────────────────────────
    # UPDATE NEWS
    # ─────────────────────────────────────────

    @staticmethod
    async def update_news(
        news_id: str,
        data: NewsUpdate,
        updated_by_user_id: str
    ) -> NewsResponse:
        """
        Update news details.

        durationDays behaviour on update:
          - New value provided  → expiresAt is recomputed from the original
            publishedAt so the duration is always relative to first publish.
          - Explicitly set to null in the payload  → expiresAt is cleared,
            making the news permanent (no longer expires).
          - Field not sent at all (exclude_unset) → expiresAt is unchanged.
        """
        await NewsService._require_admin_or_manager(updated_by_user_id)

        # Check news exists
        news = await prisma.news.find_unique(where={"id": news_id})
        if not news:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="News not found"
            )

        # Build the DB update payload from explicitly sent fields only
        update_data: Dict[str, Any] = data.model_dump(exclude_unset=True)

        # Recompute expiresAt whenever durationDays was included in the patch
        if "durationDays" in update_data:
            new_duration = update_data["durationDays"]  # may be None (clear expiry)
            update_data["expiresAt"] = _compute_expires_at(
                news.publishedAt, new_duration
            )

        updated_news = await prisma.news.update(
            where={"id": news_id},
            data=update_data,
        )

        return _build_news_response(updated_news)

    # ─────────────────────────────────────────
    # DELETE NEWS
    # ─────────────────────────────────────────

    @staticmethod
    async def delete_news(
        news_id: str,
        deleted_by_user_id: str
    ):
        """
        Delete a news announcement.

        Policy: **ADMIN only** — Manager cannot delete.
        """
        await NewsService._require_admin(deleted_by_user_id)

        # Check news exists
        news = await prisma.news.find_unique(where={"id": news_id})
        if not news:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="News not found"
            )

        # Delete news
        await prisma.news.delete(where={"id": news_id})

        return {"message": "News deleted successfully"}