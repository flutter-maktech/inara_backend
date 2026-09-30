"""
app/models/history_model.py
============================
Pydantic response models for the unified Payment / Booking History API.

Powers 10 endpoints under /payments/*:
  - GET /payments/bookings/history          (user — own class + course bookings)
  - GET /payments/bookings/all-history      (admin/manager/instructor — all bookings)
  - GET /payments/memberships/history       (user — own memberships)
  - GET /payments/memberships/all-history   (admin/manager — all memberships sold)
  - GET /payments/packages/history          (user — own package purchases)
  - GET /payments/packages/all-history      (admin/manager — all packages sold)
  - GET /payments/orders/history            (user — own store orders)
  - GET /payments/packages/all-orders       (admin/manager — all store orders)

Design notes
------------
* Every list response uses the same pagination envelope shape (`items`,
  `total`, `page`, `page_size`, `total_pages`) so the frontend can use a
  single generic list renderer.
* BookingHistoryItem is polymorphic — it carries both class_* and course_*
  fields; exactly one set is populated per row depending on booking_type.
* User-facing entries surface money + status + key identifiers so the
  UI doesn't need a second round-trip.
* Admin-facing entries additionally expose the purchaser's identity.
* All datetimes are serialized as ISO-8601 strings via .isoformat() in
  the service layer — keeps responses stable regardless of pydantic
  version and avoids tz-naive surprises in the client.
"""

from __future__ import annotations

from typing import Generic, List, Optional, TypeVar
from pydantic import BaseModel, Field


# ─────────────────────────────────────────────────────────────────────────────
# Generic pagination envelope
# ─────────────────────────────────────────────────────────────────────────────

T = TypeVar("T")


class PaginatedHistoryResponse(BaseModel, Generic[T]):
    """Standard paginated response wrapper for all history endpoints."""
    items: List[T]
    total: int
    page: int
    page_size: int
    total_pages: int


# ─────────────────────────────────────────────────────────────────────────────
# Shared "actor" sub-model (used by admin/all-history endpoints)
# ─────────────────────────────────────────────────────────────────────────────

class HistoryActor(BaseModel):
    """The user who performed the transaction (booked, bought, ordered)."""
    id: str
    name: str
    email: str
    phone: Optional[str] = None
    avatar: Optional[str] = None


# ─────────────────────────────────────────────────────────────────────────────
# 1. BOOKING HISTORY  (polymorphic — Class or Course)
# ─────────────────────────────────────────────────────────────────────────────

class BookingHistoryItem(BaseModel):
    """
    My booking — what the user sees in their booking history list.

    Polymorphic: booking_type indicates whether this is a class or course booking.
    Exactly one of (class_id, course_id) will be non-null per row.
    """
    booking_id: str
    booking_type: str                       # "CLASS" | "COURSE"

    # Class booking fields (populated when booking_type == "CLASS")
    class_id: Optional[str] = None
    class_title: Optional[str] = None
    class_image_url: Optional[str] = None
    class_scheduled_at: Optional[str] = None
    class_location: Optional[str] = None

    # Course booking fields (populated when booking_type == "COURSE")
    course_id: Optional[str] = None
    course_title: Optional[str] = None
    course_image_url: Optional[str] = None
    course_scheduled_at: Optional[str] = None
    course_location: Optional[str] = None

    # Common fields
    instructor_name: Optional[str] = None
    status: str                             # BookingStatus enum value
    amount_paid: Optional[float] = None
    currency: str = "QAR"
    payment_method: Optional[str] = None
    payment_status: Optional[str] = None    # from PaymentLog
    reference_id: Optional[str] = None
    booked_at: str
    cancelled_at: Optional[str] = None
    notes: Optional[str] = None


class AdminBookingHistoryItem(BookingHistoryItem):
    """All-bookings view — adds the purchaser identity for admins/managers/instructors."""
    user: HistoryActor


# ─────────────────────────────────────────────────────────────────────────────
# 2. MEMBERSHIP HISTORY
# ─────────────────────────────────────────────────────────────────────────────

class MembershipHistoryItem(BaseModel):
    """My membership — what the user sees in their membership history list."""
    membership_id: str
    name: str
    description: Optional[str] = None
    price: float
    currency: str = "QAR"
    duration_days: int
    status: str                         # MembershipStatus enum value
    progress: float
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    enrolled_at: Optional[str] = None
    cancelled_at: Optional[str] = None
    auto_renew: bool
    payment_method: Optional[str] = None
    payment_status: Optional[str] = None
    reference_id: Optional[str] = None
    is_paid: bool


class AdminMembershipHistoryItem(MembershipHistoryItem):
    """All-memberships view — adds the purchaser identity for admins/managers."""
    user: HistoryActor


# ─────────────────────────────────────────────────────────────────────────────
# 3. PACKAGE HISTORY
# Packages are purchased → instantiated as Memberships (per existing service
# code). We surface them by joining PaymentLog (module=PACKAGE) with the
# Membership row that was created from each successful purchase.
# ─────────────────────────────────────────────────────────────────────────────

class PackageHistoryItem(BaseModel):
    """My package — what the user sees in their package history list.

    DESIGN: Packages and Memberships are fully independent domain objects.
    This response exposes package purchase / payment data only.
    Membership fields (membership_id, membership_status) are intentionally
    absent — they belong to a separate domain endpoint.
    The validity window (valid_from / valid_until / duration_days) describes
    the purchased package plan activation period and is sourced from the
    package activation record, NOT from the Membership domain.
    """
    payment_log_id: str
    reference_id: Optional[str] = None
    package_id: Optional[str] = None
    package_name: Optional[str] = None
    package_description: Optional[str] = None
    amount_paid: float
    currency: str = "QAR"
    payment_method: str
    payment_status: str
    purchased_at: str
    valid_from: Optional[str] = None
    valid_until: Optional[str] = None
    duration_days: Optional[int] = None
    auto_renew: Optional[bool] = None


class AdminPackageHistoryItem(PackageHistoryItem):
    """All-packages view — adds the purchaser identity for admins/managers."""
    user: HistoryActor


# ─────────────────────────────────────────────────────────────────────────────
# 4. STORE ORDER HISTORY
# ─────────────────────────────────────────────────────────────────────────────

class OrderHistoryItemLine(BaseModel):
    """One product line inside an order."""
    product_id: str
    product_name: str
    product_image: Optional[str] = None
    quantity: int
    unit_price: float
    line_total: float


class OrderHistoryItem(BaseModel):
    """My order — what the user sees in their order history list."""
    order_id: str
    order_number: str
    items: List[OrderHistoryItemLine]
    item_count: int                     # total quantity across all lines
    subtotal: float
    discount: float
    tax: float
    total: float
    currency: str = "QAR"
    status: str                         # OrderStatus enum value
    payment_method: str
    payment_status: Optional[str] = None
    transaction_id: Optional[str] = None
    reference_id: Optional[str] = None
    paid_at: Optional[str] = None
    pickup_note: Optional[str] = None
    notes: Optional[str] = None
    created_at: str


class AdminOrderHistoryItem(OrderHistoryItem):
    """All-orders view — adds the purchaser identity for admins/managers."""
    user: HistoryActor