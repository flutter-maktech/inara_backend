"""
app/api/v1/notifications.py
============================
In-app Notification Inbox API.

Endpoints
─────────
  GET    /notifications                    paginated inbox for the caller
  GET    /notifications/unread-count       fast unread badge counter
  PATCH  /notifications/mark-read          mark one / many / all as read
  DELETE /notifications/{notification_id}  delete a single notification
  DELETE /notifications                    clear ALL the caller's notifications

All endpoints are user-scoped — callers see ONLY their own notifications,
enforced server-side on every query and mutation.
"""

from __future__ import annotations

import logging
import math
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.v1.dependencies import get_current_active_user
from app.db.db_client import prisma
from app.models.notification_model import (
    GenericMessageResponse,
    MarkReadRequest,
    NotificationItem,
    NotificationListResponse,
    UnreadCountResponse,
)

logger = logging.getLogger(__name__)
router = APIRouter()


# ════════════════════════════════════════════════════════════════════════════
# LIST
# ════════════════════════════════════════════════════════════════════════════

@router.get(
    "",
    response_model=NotificationListResponse,
    summary="My Notifications (Inbox)",
)
async def list_notifications(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    unread_only: bool = Query(False, description="If true, return only unread items."),
    type_filter: Optional[str] = Query(
        None, alias="type",
        description="Filter by NotificationType: INFO | SUCCESS | WARNING | ERROR | REMINDER",
    ),
    current_user=Depends(get_current_active_user),
) -> NotificationListResponse:
    """
    Return the authenticated user's notification inbox.

    Sorted newest first. The response also includes a global `unreadCount`
    so the frontend can paint the badge from the same call.
    """
    where: dict = {"userId": current_user.id}

    if unread_only:
        where["isRead"] = False

    if type_filter:
        valid_types = {"INFO", "SUCCESS", "WARNING", "ERROR", "REMINDER"}
        t = type_filter.upper()
        if t not in valid_types:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid type filter. Must be one of: {', '.join(sorted(valid_types))}",
            )
        where["type"] = t

    total = await prisma.notification.count(where=where)
    unread_count = await prisma.notification.count(
        where={"userId": current_user.id, "isRead": False}
    )

    skip = (page - 1) * page_size

    rows = await prisma.notification.find_many(
        where=where,
        order={"createdAt": "desc"},
        skip=skip,
        take=page_size,
    )

    items = [
        NotificationItem(
            id=r.id,
            title=r.title,
            message=r.message,
            type=str(r.type),
            actionUrl=r.actionUrl,
            isRead=r.isRead,
            readAt=r.readAt,
            createdAt=r.createdAt,
        )
        for r in rows
    ]

    return NotificationListResponse(
        items=items,
        total=total,
        unreadCount=unread_count,
        page=page,
        pageSize=page_size,
        totalPages=math.ceil(total / page_size) if total else 0,
    )


# ════════════════════════════════════════════════════════════════════════════
# UNREAD COUNT (fast badge)
# ════════════════════════════════════════════════════════════════════════════

@router.get(
    "/unread-count",
    response_model=UnreadCountResponse,
    summary="Unread Notification Count",
)
async def unread_count(
    current_user=Depends(get_current_active_user),
) -> UnreadCountResponse:
    """
    Lightweight endpoint for the bell-icon badge.
    Returns just the unread count — no row payload.
    """
    count = await prisma.notification.count(
        where={"userId": current_user.id, "isRead": False}
    )
    return UnreadCountResponse(unreadCount=count)


# ════════════════════════════════════════════════════════════════════════════
# MARK READ
# ════════════════════════════════════════════════════════════════════════════

@router.patch(
    "/mark-read",
    response_model=GenericMessageResponse,
    summary="Mark Notifications as Read",
)
async def mark_read(
    data: MarkReadRequest,
    current_user=Depends(get_current_active_user),
) -> GenericMessageResponse:
    """
    Mark one, many, or **all** of the caller's unread notifications as read.

    * `notificationIds` empty → mark ALL the caller's unread items.
    * `notificationIds` provided → only those IDs (and only if they belong
      to the caller) are flipped to read. Foreign IDs are silently ignored.
    """
    now = datetime.now(timezone.utc)
    where: dict = {"userId": current_user.id, "isRead": False}

    if data.notificationIds:
        where["id"] = {"in": data.notificationIds}

    # Count first so we can return an accurate `affected`.
    affected = await prisma.notification.count(where=where)
    if affected == 0:
        return GenericMessageResponse(
            message="No unread notifications to update.",
            affected=0,
        )

    await prisma.notification.update_many(
        where=where,
        data={"isRead": True, "readAt": now},
    )

    return GenericMessageResponse(
        message="Notifications marked as read.",
        affected=affected,
    )


# ════════════════════════════════════════════════════════════════════════════
# DELETE ONE
# ════════════════════════════════════════════════════════════════════════════

@router.delete(
    "/{notification_id}",
    response_model=GenericMessageResponse,
    summary="Delete a Notification",
)
async def delete_notification(
    notification_id: str,
    current_user=Depends(get_current_active_user),
) -> GenericMessageResponse:
    """
    Delete a single notification belonging to the caller.

    Returns 404 if the notification does not exist or is not owned by the
    caller — prevents both "not found" and ownership probes.
    """
    row = await prisma.notification.find_unique(where={"id": notification_id})
    if not row or row.userId != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Notification not found.",
        )

    await prisma.notification.delete(where={"id": notification_id})

    return GenericMessageResponse(
        message="Notification deleted.",
        affected=1,
    )


# ════════════════════════════════════════════════════════════════════════════
# CLEAR ALL
# ════════════════════════════════════════════════════════════════════════════

@router.delete(
    "",
    response_model=GenericMessageResponse,
    summary="Clear All My Notifications",
)
async def clear_all(
    current_user=Depends(get_current_active_user),
) -> GenericMessageResponse:
    """
    Delete every notification belonging to the caller. Irreversible.
    """
    total = await prisma.notification.count(where={"userId": current_user.id})
    if total == 0:
        return GenericMessageResponse(
            message="Inbox is already empty.",
            affected=0,
        )

    await prisma.notification.delete_many(where={"userId": current_user.id})

    return GenericMessageResponse(
        message="All notifications deleted.",
        affected=total,
    )