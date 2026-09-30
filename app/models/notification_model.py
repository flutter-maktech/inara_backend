"""
app/models/notification_model.py
==================================
Pydantic schemas for the in-app Notification inbox API.

All models target the existing `Notification` table — no schema changes.
"""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field


class NotificationItem(BaseModel):
    """One row in the user's notification inbox."""
    id:        str
    title:     str
    message:   str
    type:      str
    actionUrl: Optional[str] = None
    isRead:    bool
    readAt:    Optional[datetime] = None
    createdAt: datetime


class NotificationListResponse(BaseModel):
    """Paginated list envelope."""
    items:       List[NotificationItem]
    total:       int
    unreadCount: int
    page:        int
    pageSize:    int
    totalPages:  int


class UnreadCountResponse(BaseModel):
    unreadCount: int


class MarkReadRequest(BaseModel):
    notificationIds: List[str] = Field(
        default_factory=list,
        description="If empty, mark ALL the caller's unread notifications as read.",
    )


class GenericMessageResponse(BaseModel):
    message: str
    affected: int = 0