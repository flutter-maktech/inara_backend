"""
app/models/payment_model.py
============================
Pydantic request / response models for all 5 payment modules.

Kept clean and minimal — only what the route layer needs.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


# ─────────────────────────────────────────────────────────────────────────────
# Shared
# ─────────────────────────────────────────────────────────────────────────────

class PaymentMethod(str):
    WALLET = "WALLET"
    GATEWAY = "GATEWAY"


# ─────────────────────────────────────────────────────────────────────────────
# MODULE 1 — WALLET
# ─────────────────────────────────────────────────────────────────────────────

class WalletTopupRequest(BaseModel):
    """Initiate a wallet top-up via MyFatoorah gateway."""
    amount: float = Field(..., gt=0, description="Amount to deposit (QAR, must be > 0)")


class WalletBalanceResponse(BaseModel):
    wallet_id: str
    balance: float
    currency: str


class WalletTopupInitResponse(BaseModel):
    """Step A — returned after creating a checkout session."""
    payment_url: str
    invoice_id: str
    reference_id: str
    amount: float
    currency: str
    message: str


class WalletTransactionItem(BaseModel):
    id: str
    type: str          # "DEPOSIT" | "DEBIT"
    amount: float
    balance_before: float
    balance_after: float
    description: Optional[str]
    reference_id: Optional[str]
    created_at: str


class WalletHistoryResponse(BaseModel):
    transactions: List[WalletTransactionItem]
    total: int
    page: int
    page_size: int
    total_pages: int


# ─────────────────────────────────────────────────────────────────────────────
# MODULE 2 — BOOKING (Classes)
# ─────────────────────────────────────────────────────────────────────────────

class BookingPaymentRequest(BaseModel):
    """Pay for a class booking."""
    class_id: str = Field(..., description="ID of the Classes record to book")
    payment_method: str = Field(
        ...,
        description=(
            "Payment source: 'WALLET' (immediate), 'GATEWAY' (MyFatoorah redirect), "
            "or 'PACKAGE' (spend one session from an owned package — requires membership_id)"
        ),
    )
    membership_id: Optional[str] = Field(
        None,
        description=(
            "Required only when payment_method='PACKAGE'. The id of the user's owned "
            "package instance (returned as 'id' inside availablePackages on the class "
            "response) to spend a session from. Ignored for WALLET / GATEWAY."
        ),
    )


class AvailablePackageOption(BaseModel):
    """
    One of the caller's owned package instances that covers a specific class —
    surfaced so the frontend can render a "Pay with Package XXX" choice instead
    of the system silently spending a session on the user's behalf.
    """
    membershipId: str = Field(..., description="Pass this back as membership_id when paying with payment_method='PACKAGE'")
    packageName: str
    sessionsRemaining: Optional[int] = None   # None = unlimited sessions on this package
    expiresAt: Optional[str] = None           # ISO datetime string


class BookingCancelRequest(BaseModel):
    """Cancel a booking (class or course) and apply refund policy."""
    booking_id: str
    reason: Optional[str] = None


# ─────────────────────────────────────────────────────────────────────────────
# MODULE 2B — BOOKING (Courses)
# ─────────────────────────────────────────────────────────────────────────────

class CourseBookingPaymentRequest(BaseModel):
    """Pay for a course booking."""
    course_id: str = Field(..., description="ID of the Course record to book")
    payment_method: str = Field(
        ...,
        description="Payment source: 'WALLET' (immediate) or 'GATEWAY' (MyFatoorah redirect)",
    )


# ─────────────────────────────────────────────────────────────────────────────
# MODULE 3 — MEMBERSHIP
# ─────────────────────────────────────────────────────────────────────────────

class MembershipPaymentRequest(BaseModel):
    """
    Purchase a membership plan.
    The membership_plan_id refers to an existing Membership record that acts
    as a template (created by admin) — the system clones it for the user.
    """
    membership_plan_id: str = Field(..., description="Template Membership ID to purchase")
    payment_method: str = Field(..., description="'WALLET' or 'GATEWAY'")
    auto_renew: bool = False


# ─────────────────────────────────────────────────────────────────────────────
# MODULE 4 — PACKAGE
# ─────────────────────────────────────────────────────────────────────────────

class PackagePaymentRequest(BaseModel):
    """Purchase a package."""
    package_id: str = Field(..., description="Package ID to purchase")
    payment_method: str = Field(..., description="'WALLET' or 'GATEWAY'")
    auto_renew: bool = False


# ─────────────────────────────────────────────────────────────────────────────
# MODULE 5 — STORE ORDER
# ─────────────────────────────────────────────────────────────────────────────

class OrderItem(BaseModel):
    product_id: str
    quantity: int = Field(..., ge=1, description="Must be at least 1")


class StoreOrderRequest(BaseModel):
    """Place a store order with one or more products."""
    items: List[OrderItem] = Field(..., min_length=1)
    payment_method: str = Field(..., description="'WALLET' or 'GATEWAY'")
    notes: Optional[str] = None


# ─────────────────────────────────────────────────────────────────────────────
# SHARED GATEWAY RESPONSE SHAPES
# ─────────────────────────────────────────────────────────────────────────────

class GatewayInitResponse(BaseModel):
    """
    Returned by any 'initiate' endpoint when payment_method == GATEWAY.
    Frontend redirects to payment_url.
    """
    payment_url: str
    invoice_id: str
    reference_id: str
    amount: float
    currency: str = "QAR"
    message: str = "Redirect user to payment_url to complete payment."


class PaymentSuccessResponse(BaseModel):
    """Returned on confirmed successful payment."""
    success: bool = True
    module: str
    reference_id: str
    message: str
    data: Optional[Dict[str, Any]] = None


class PaymentErrorResponse(BaseModel):
    """Returned on payment failure or cancellation callback."""
    success: bool = False
    module: str
    reference_id: str
    message: str = "Payment was not completed. Please try again."