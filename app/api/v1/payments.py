"""
app/api/v1/payments.py
=======================
Unified payment router for all 5 modules + Payment / Booking History views.

Endpoint map:

  WALLET
    GET  /payments/wallet/balance
    POST /payments/wallet/topup
    GET  /payments/wallet/history

  BOOKING (Classes)
    POST /payments/bookings/pay
    POST /payments/bookings/cancel
    GET  /payments/bookings/history          (user)
    GET  /payments/bookings/all-history      (admin/manager/instructor)

  BOOKING (Courses) — NEW
    POST /payments/courses/pay               ← Book & pay for a course
    POST /payments/courses/cancel            ← Cancel a course booking (reuses bookings/cancel logic)

  MEMBERSHIP
    POST /payments/memberships/buy
    GET  /payments/memberships/history       (user)
    GET  /payments/memberships/all-history   (admin/manager)

  PACKAGE
    POST /payments/packages/buy
    GET  /payments/packages/history          (user)
    GET  /payments/packages/all-history      (admin/manager)

  STORE ORDER
    POST /payments/orders/checkout
    GET  /payments/orders/history            (user)
    GET  /payments/packages/all-orders       (admin/manager — spec-defined path)

  SHARED CALLBACKS
    GET  /payments/callback/success   ← MyFatoorah redirects here on success
    GET  /payments/callback/error     ← MyFatoorah redirects here on failure

  WEBHOOK
    POST /payments/webhook            ← MyFatoorah server-to-server event

All authenticated endpoints use the existing get_current_active_user dependency.
The webhook endpoint validates HMAC instead of JWT.

─────────────────────────────────────────────────────────────────────────────
MyFatoorah Callback URL Shape
─────────────────────────────────────────────────────────────────────────────
MyFatoorah appends these query params when redirecting to CallBackUrl / ErrorUrl:

  ?paymentId=<PaymentId>&Id=<PaymentId>

Both params contain the PaymentId (the long transaction-level ID).
This is NOT the InvoiceId. Use KeyType="PaymentId" with GetPaymentStatus.

The InvoiceId (short number, e.g. 2026000003) only comes from:
  - SendPayment response (stored as gatewayInvoiceId in PaymentLog)
  - Server-to-server webhook body (use KeyType="InvoiceId")

Our CustomerReference is stored inside the invoice — NOT in the URL.
It is returned by GetPaymentStatus and resolved inside WebhookService.
"""

import logging
import textwrap
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse

from app.api.v1.dependencies import get_current_active_user
from app.core.config import settings
from app.core.payment_service import PaymentService
from app.models.payment_model import (
    BookingCancelRequest,
    BookingPaymentRequest,
    CourseBookingPaymentRequest,
    MembershipPaymentRequest,
    PackagePaymentRequest,
    StoreOrderRequest,
    WalletTopupRequest,
)
from app.services.booking_payment_service import BookingPaymentService
from app.services.history_service import HistoryService
from app.services.membership_payment_service import MembershipPaymentService
from app.services.package_payment_service import PackagePaymentService
from app.services.store_payment_service import StorePaymentService
from app.services.wallet_service import WalletService
from app.services.webhook_service import WebhookService

logger = logging.getLogger(__name__)
router = APIRouter()


# ═══════════════════════════════════════════════════════════════════
# INTERNAL HELPERS — Payment Callback HTML Pages
# ═══════════════════════════════════════════════════════════════════

def _build_callback_html(
    *,
    success: bool,
    deep_link_url: str,
    fallback_url: str,
    reference_id: str,
    payment_id: str,
    module: str,
    message: str,
) -> str:
    """
    Build a self-contained HTML redirect page that:

    1. Immediately attempts to open the mobile app via a deep link URI.
    2. Detects whether the app opened (document.hidden) and skips the
       fallback redirect if it did.
    3. Falls back to ``fallback_url`` after 1.5 s if the app is not
       installed or the deep link was not handled.
    4. Shows a polished countdown UI so the user always has a manual
       "Open in App" button as a safety net.
    """

    if success:
        icon          = ""
        heading       = "Payment Successful!"
        sub_heading   = "Your transaction was completed successfully."
        accent_color  = "#22c55e"   # green-500
        bg_gradient   = "linear-gradient(135deg, #052e16 0%, #14532d 50%, #052e16 100%)"
        badge_bg      = "rgba(34,197,94,0.15)"
        badge_border  = "rgba(34,197,94,0.3)"
        status_label  = "SUCCESS"
        button_label  = "Open App"
    else:
        icon          = "❌"
        heading       = "Payment Failed"
        sub_heading   = message or "Your payment could not be completed. Please try again."
        accent_color  = "#ef4444"   # red-500
        bg_gradient   = "linear-gradient(135deg, #1c0505 0%, #450a0a 50%, #1c0505 100%)"
        badge_bg      = "rgba(239,68,68,0.15)"
        badge_border  = "rgba(239,68,68,0.3)"
        status_label  = "FAILED"
        button_label  = "Return to App"

    # Sanitise values that will be interpolated into HTML/JS
    _safe = lambda s: str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
    safe_ref     = _safe(reference_id)
    safe_pid     = _safe(payment_id)
    safe_module  = _safe(module)
    safe_deep    = _safe(deep_link_url)
    safe_fb      = _safe(fallback_url)

    html = textwrap.dedent(f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
      <meta charset="UTF-8" />
      <meta name="viewport" content="width=device-width, initial-scale=1.0" />
      <title>{heading} — {settings.APP_NAME}</title>
      <meta name="robots" content="noindex, nofollow" />
      <style>
        *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}

        body {{
          min-height: 100vh;
          display: flex;
          align-items: center;
          justify-content: center;
          font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto,
                       "Helvetica Neue", Arial, sans-serif;
          background: {bg_gradient};
          padding: 1.5rem;
          color: #f1f5f9;
        }}

        .card {{
          background: rgba(15, 23, 42, 0.85);
          backdrop-filter: blur(20px);
          -webkit-backdrop-filter: blur(20px);
          border: 1px solid rgba(255,255,255,0.1);
          border-radius: 1.5rem;
          padding: 2.5rem 2rem;
          max-width: 420px;
          width: 100%;
          text-align: center;
          box-shadow: 0 25px 50px rgba(0,0,0,0.5);
        }}

        .icon {{
          font-size: 4rem;
          line-height: 1;
          margin-bottom: 1rem;
          display: block;
          animation: pop 0.4s cubic-bezier(0.34,1.56,0.64,1) both;
        }}

        @keyframes pop {{
          from {{ transform: scale(0.4); opacity: 0; }}
          to   {{ transform: scale(1);   opacity: 1; }}
        }}

        h1 {{
          font-size: 1.5rem;
          font-weight: 700;
          color: {accent_color};
          margin-bottom: 0.5rem;
          letter-spacing: -0.02em;
        }}

        .sub {{
          font-size: 0.9rem;
          color: #94a3b8;
          margin-bottom: 1.75rem;
          line-height: 1.5;
        }}

        .badge-row {{
          display: flex;
          flex-direction: column;
          gap: 0.5rem;
          margin-bottom: 1.75rem;
        }}

        .badge {{
          background: {badge_bg};
          border: 1px solid {badge_border};
          border-radius: 0.5rem;
          padding: 0.5rem 0.75rem;
          font-size: 0.78rem;
          display: flex;
          justify-content: space-between;
          align-items: center;
          gap: 0.5rem;
          overflow: hidden;
        }}

        .badge-label {{
          color: #64748b;
          font-weight: 600;
          text-transform: uppercase;
          letter-spacing: 0.05em;
          white-space: nowrap;
          flex-shrink: 0;
        }}

        .badge-value {{
          color: #e2e8f0;
          font-family: "SF Mono", "Fira Code", monospace;
          font-size: 0.75rem;
          text-align: right;
          word-break: break-all;
        }}

        .btn {{
          display: inline-flex;
          align-items: center;
          justify-content: center;
          gap: 0.5rem;
          width: 100%;
          padding: 0.875rem 1.5rem;
          border-radius: 0.875rem;
          font-size: 1rem;
          font-weight: 700;
          letter-spacing: 0.01em;
          cursor: pointer;
          border: none;
          transition: transform 0.15s, opacity 0.15s, box-shadow 0.15s;
          text-decoration: none;
          margin-bottom: 0.75rem;
          color: #fff;
        }}

        .btn:last-child {{ margin-bottom: 0; }}

        .btn-primary {{
          background: {accent_color};
          box-shadow: 0 4px 20px rgba(0,0,0,0.3);
        }}

        .btn-primary:hover {{
          transform: translateY(-1px);
          box-shadow: 0 8px 28px rgba(0,0,0,0.4);
          opacity: 0.92;
        }}

        .btn-secondary {{
          background: rgba(255,255,255,0.08);
          border: 1px solid rgba(255,255,255,0.15);
          font-size: 0.875rem;
          font-weight: 600;
        }}

        .btn-secondary:hover {{
          background: rgba(255,255,255,0.13);
          transform: translateY(-1px);
        }}

        .timer-block {{
          margin-top: 1.5rem;
          padding-top: 1.25rem;
          border-top: 1px solid rgba(255,255,255,0.07);
          font-size: 0.8rem;
          color: #64748b;
        }}

        .timer-block span {{
          color: {accent_color};
          font-weight: 700;
          font-variant-numeric: tabular-nums;
        }}

        .status-pill {{
          display: inline-block;
          background: {badge_bg};
          border: 1px solid {badge_border};
          color: {accent_color};
          font-size: 0.7rem;
          font-weight: 800;
          letter-spacing: 0.1em;
          padding: 0.2rem 0.6rem;
          border-radius: 999px;
          margin-bottom: 1rem;
          text-transform: uppercase;
        }}
      </style>
    </head>
    <body>
      <div class="card">
        <span class="status-pill">{status_label}</span>
        <span class="icon">{icon}</span>
        <h1>{heading}</h1>
        <p class="sub">{sub_heading}</p>

        <div class="badge-row">
          <div class="badge">
            <span class="badge-label">Module</span>
            <span class="badge-value">{safe_module}</span>
          </div>
          <div class="badge">
            <span class="badge-label">Reference</span>
            <span class="badge-value">{safe_ref}</span>
          </div>
          <div class="badge">
            <span class="badge-label">Payment ID</span>
            <span class="badge-value">{safe_pid}</span>
          </div>
        </div>

        <button class="btn btn-primary" id="btn-open" onclick="openApp()">
           {button_label}
        </button>

        <div class="timer-block" id="timer-block">
          Redirecting to app in <span id="countdown">3</span>s…
        </div>
      </div>

      <script>
        (function () {{
          var DEEP_LINK   = "{safe_deep}";
          var FALLBACK    = "{safe_fb}";
          var countdown   = 3;
          var redirected  = false;

          // ── Attempt deep-link immediately ──────────────
          function openApp() {{
            if (redirected) return;
            window.location = DEEP_LINK;

            // After 1.5 s, if the browser tab is still visible (i.e. the
            // app did not open), fall through to the fallback URL.
            setTimeout(function () {{
              if (!document.hidden && !redirected) {{
                redirected = true;
                window.location.href = FALLBACK;
              }}
            }}, 1500);
          }}

          // ── Countdown display ──────────────────────────
          var el = document.getElementById("countdown");
          var tick = setInterval(function () {{
            countdown -= 1;
            if (el) el.textContent = countdown;
            if (countdown <= 0) {{
              clearInterval(tick);
              openApp();
            }}
          }}, 1000);

          // ── Expose for manual button ───────────────────
          window.openApp = openApp;

          // ── If the user comes back to the tab, stop the
          //    automatic redirect so they don't loop. ─────
          document.addEventListener("visibilitychange", function () {{
            if (!document.hidden) {{
              clearInterval(tick);
              var tb = document.getElementById("timer-block");
              if (tb) tb.style.display = "none";
            }}
          }});
        }})();
      </script>
    </body>
    </html>
    """).strip()

    return html


# ═══════════════════════════════════════════════════════════════════
# MODULE 1 — WALLET
# ═══════════════════════════════════════════════════════════════════

@router.get(
    "/wallet/balance",
    summary="Get Wallet Balance",
    tags=["Payments – Wallet"],
)
async def get_wallet_balance(current_user=Depends(get_current_active_user)):
    """
    Return the authenticated user's current wallet balance and currency.

    **Response:**
    ```json
    { "balance": 135.00, "currency": "QAR", "wallet_id": "..." }
    ```
    """
    return await WalletService.get_balance(current_user.id)


@router.post(
    "/wallet/topup",
    summary="Initiate Wallet Top-Up",
    status_code=status.HTTP_201_CREATED,
    tags=["Payments – Wallet"],
)
async def initiate_wallet_topup(
    data: WalletTopupRequest,
    current_user=Depends(get_current_active_user),
):
    """
    **Step A — Create Checkout Session** for wallet top-up.

    Frontend must redirect to `payment_url`.
    MyFatoorah will redirect back to the success/error callback URLs automatically.

    **Request:**
    ```json
    { "amount": 50.00 }
    ```

    **Response includes:**
    - `payment_url` → redirect user here
    - `invoice_id` → store for reference
    - `reference_id` → prefixed WALLET-xxxxxx
    """
    return await WalletService.initiate_topup(current_user.id, data.amount)


@router.get(
    "/wallet/history",
    summary="Wallet Transaction History",
    tags=["Payments – Wallet"],
)
async def get_wallet_history(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user=Depends(get_current_active_user),
):
    """
    Paginated wallet transaction history (top-ups + debits).
    Used to populate the Payment History list on the Wallet screen.
    """
    return await WalletService.get_transaction_history(current_user.id, page, page_size)


# ═══════════════════════════════════════════════════════════════════
# MODULE 2 — BOOKING
# ═══════════════════════════════════════════════════════════════════

@router.post(
    "/bookings/pay",
    summary="Pay for Class Booking",
    status_code=status.HTTP_201_CREATED,
    tags=["Payments – Bookings"],
)
async def pay_for_booking(
    data: BookingPaymentRequest,
    current_user=Depends(get_current_active_user),
):
    """
    **Step A — Create Booking & Pay.**

    `payment_method`: `"WALLET"`, `"GATEWAY"`, or `"PACKAGE"`

    - **WALLET**: Booking confirmed immediately. Class's full price is deducted from wallet.
    - **GATEWAY**: Returns `payment_url`. Booking confirmed via webhook after payment.
    - **PACKAGE**: Confirms immediately at 0 QAR by spending one session from the specified
      package instance. Requires `membership_id` (the `membershipId` value from
      `availablePackages` on the class detail response).

    When the user holds a package that covers this class, `available_packages` is returned
    in every response so the frontend can offer "Pay with Package XXX" as a picker — the
    class's full price is still charged for WALLET/GATEWAY; only PACKAGE spends a session.

    Membership coverage (a plan purchased from the Membership catalogue) auto-applies and
    the booking is confirmed at 0 QAR without any extra field needed.

    **Request:**
    ```json
    {
      "class_id": "...",
      "payment_method": "WALLET"
    }
    ```
    Or to use a package:
    ```json
    {
      "class_id": "...",
      "payment_method": "PACKAGE",
      "membership_id": "<membershipId from availablePackages>"
    }
    ```
    """
    return await BookingPaymentService.initiate_booking_payment(
        user_id=current_user.id,
        class_id=data.class_id,
        payment_method=data.payment_method,
        membership_id=data.membership_id,
    )


@router.post(
    "/bookings/cancel",
    summary="Cancel Booking",
    tags=["Payments – Bookings"],
)
async def cancel_booking(
    data: BookingCancelRequest,
    current_user=Depends(get_current_active_user),
):
    """
    Cancel a booking and apply refund policy:
    - **≥ 3 hours before class start** → full refund to original payment source
    - **< 3 hours before class start** → NO refund

    Cancellation Rule is also shown in the UI per design.

    **Request:**
    ```json
    { "booking_id": "...", "reason": "optional reason" }
    ```
    """
    return await BookingPaymentService.cancel_booking(
        user_id=current_user.id,
        booking_id=data.booking_id,
        reason=data.reason,
    )


# ═══════════════════════════════════════════════════════════════════
# MODULE 2B — COURSE BOOKINGS
# Same cancellation policy and refund logic as class bookings.
# The Booking model is polymorphic — courseId is set, classId is NULL.
# ═══════════════════════════════════════════════════════════════════

@router.post(
    "/courses/pay",
    summary="Pay for Course Booking",
    status_code=status.HTTP_201_CREATED,
    tags=["Payments – Bookings"],
)
async def pay_for_course(
    data: CourseBookingPaymentRequest,
    current_user=Depends(get_current_active_user),
):
    """
    **Step A — Create Course Booking & Pay.**

    `payment_method`: `"WALLET"` or `"GATEWAY"`

    - **WALLET**: Booking confirmed immediately. Returns `status: "CONFIRMED"`.
    - **GATEWAY**: Returns `payment_url`. Booking confirmed via webhook after payment.

    Cancellation policy:
    - **≥ 3 hours before course start** → full refund to original payment source
    - **< 3 hours before course start** → NO refund

    **Request:**
    ```json
    {
      "course_id": "...",
      "payment_method": "WALLET"
    }
    ```
    """
    return await BookingPaymentService.initiate_course_booking_payment(
        user_id=current_user.id,
        course_id=data.course_id,
        payment_method=data.payment_method,
    )


@router.post(
    "/courses/cancel",
    summary="Cancel Course Booking",
    tags=["Payments – Bookings"],
)
async def cancel_course_booking(
    data: BookingCancelRequest,
    current_user=Depends(get_current_active_user),
):
    """
    Cancel a course booking and apply refund policy:
    - **≥ 3 hours before course start** → full refund to original payment source
    - **< 3 hours before course start** → NO refund

    **Request:**
    ```json
    { "booking_id": "...", "reason": "optional reason" }
    ```
    """
    return await BookingPaymentService.cancel_booking(
        user_id=current_user.id,
        booking_id=data.booking_id,
        reason=data.reason,
    )


# ─── Booking History (user) ──────────────────────────────────────────

@router.get(
    "/bookings/history",
    summary="My Booking History",
    tags=["Payments – Bookings"],
)
async def get_my_bookings_history(
    page: int = Query(1, ge=1, description="Page number (1-indexed)"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page (max 100)"),
    status_filter: Optional[str] = Query(
        None, alias="status",
        description="Filter by booking status: PENDING | CONFIRMED | CANCELLED | ATTENDED | MISSED",
    ),
    search: Optional[str] = Query(None, description="Search title or location"),
    booking_type: Optional[str] = Query(
        None,
        description="Filter by booking type: CLASS | COURSE (omit for both)",
    ),
    current_user=Depends(get_current_active_user),
):
    """
    **My Booking History** — every class and course booking made by the authenticated user.

    Sorted by booking date (newest first). Each item includes:
    - `booking_type`: `"CLASS"` or `"COURSE"` discriminator
    - Class or course info (title, image, scheduled date, location, instructor)
    - Booking status (CONFIRMED, CANCELLED, ATTENDED, etc.)
    - Amount paid + payment method + payment status

    Use `status` to filter by booking status, `booking_type` to show only
    class or course bookings, and `search` to find by title or location.
    """
    return await HistoryService.get_my_bookings_history(
        user_id=current_user.id,
        page=page,
        page_size=page_size,
        status_filter=status_filter,
        search=search,
        booking_type=booking_type,
    )


# ─── Booking History (admin / manager / instructor) ─────────────────

@router.get(
    "/bookings/all-history",
    summary="All Class Bookings — Admin / Manager / Instructor",
    tags=["Payments – Bookings"],
)
async def get_all_bookings_history(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status_filter: Optional[str] = Query(
        None, alias="status",
        description="Filter by booking status: PENDING | CONFIRMED | CANCELLED | ATTENDED | MISSED",
    ),
    search: Optional[str] = Query(
        None,
        description="Search by title/location or by user name/email",
    ),
    booking_type: Optional[str] = Query(
        None,
        description="Filter by booking type: CLASS | COURSE (omit for both)",
    ),
    current_user=Depends(get_current_active_user),
):
    """
    **All Bookings Across the App** (Classes + Courses).

    **Access:**
    - **ADMIN, MANAGER** → see every booking from every user.
    - **INSTRUCTOR**     → see bookings only for classes/courses they instruct.

    Each entry carries a `booking_type` discriminator (`"CLASS"` or `"COURSE"`)
    and surfaces both the entity info and the user (booker) identity.

    Use `booking_type` to filter to only class or only course bookings.
    """
    return await HistoryService.get_all_bookings_history(
        actor_user_id=current_user.id,
        page=page,
        page_size=page_size,
        status_filter=status_filter,
        search=search,
        booking_type=booking_type,
    )


# ═══════════════════════════════════════════════════════════════════
# MODULE 3 — MEMBERSHIP
# Admin-only creation is handled by existing memberships.py.
# This endpoint handles PURCHASE by any authenticated user.
# ═══════════════════════════════════════════════════════════════════

@router.post(
    "/memberships/buy",
    summary="Purchase Membership Plan",
    status_code=status.HTTP_201_CREATED,
    tags=["Payments – Memberships"],
)
async def buy_membership(
    data: MembershipPaymentRequest,
    current_user=Depends(get_current_active_user),
):
    """
    **Step A — Purchase a membership plan.**

    ⚠️ **NON-REFUNDABLE** — no refund is possible after purchase.

    `payment_method`: `"WALLET"` or `"GATEWAY"`

    - **WALLET**: Membership activated immediately.
    - **GATEWAY**: Returns `payment_url`. Activated via webhook.

    **Request:**
    ```json
    {
      "membership_plan_id": "...",
      "payment_method": "GATEWAY",
      "auto_renew": false
    }
    ```
    """
    return await MembershipPaymentService.initiate_membership_purchase(
        user_id=current_user.id,
        membership_plan_id=data.membership_plan_id,
        payment_method=data.payment_method,
        auto_renew=data.auto_renew,
    )


# ─── Membership History (user) ──────────────────────────────────────

@router.get(
    "/memberships/history",
    summary="My Membership History",
    tags=["Payments – Memberships"],
)
async def get_my_memberships_history(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status_filter: Optional[str] = Query(
        None, alias="status",
        description="Filter by membership status: ACTIVE | EXPIRED | CANCELLED | PAUSED",
    ),
    search: Optional[str] = Query(None, description="Search membership name or description"),
    current_user=Depends(get_current_active_user),
):
    """
    **My Membership History** — every membership the user has purchased.

    Active, expired, paused and cancelled memberships are all returned;
    use `status` to narrow to one state. Items are sorted by enrolment date
    (newest first).
    """
    return await HistoryService.get_my_memberships_history(
        user_id=current_user.id,
        page=page,
        page_size=page_size,
        status_filter=status_filter,
        search=search,
    )


# ─── Membership History (admin / manager) ──────────────────────────

@router.get(
    "/memberships/all-history",
    summary="All Memberships — Admin / Manager",
    tags=["Payments – Memberships"],
)
async def get_all_memberships_history(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status_filter: Optional[str] = Query(
        None, alias="status",
        description="Filter by membership status: ACTIVE | EXPIRED | CANCELLED | PAUSED",
    ),
    search: Optional[str] = Query(
        None,
        description="Search by membership name/description or by buyer name/email",
    ),
    current_user=Depends(get_current_active_user),
):
    """
    **All Memberships Sold Across the App.**

    **Access:** ADMIN, MANAGER only.

    Lists every membership purchase ever made, with the buyer identity attached.
    """
    return await HistoryService.get_all_memberships_history(
        actor_user_id=current_user.id,
        page=page,
        page_size=page_size,
        status_filter=status_filter,
        search=search,
    )


# ═══════════════════════════════════════════════════════════════════
# MODULE 4 — PACKAGE
# Admin-only creation handled by existing packages.py.
# This endpoint handles PURCHASE by any authenticated user.
# ═══════════════════════════════════════════════════════════════════

@router.post(
    "/packages/buy",
    summary="Purchase Package",
    status_code=status.HTTP_201_CREATED,
    tags=["Payments – Packages"],
)
async def buy_package(
    data: PackagePaymentRequest,
    current_user=Depends(get_current_active_user),
):
    """
    **Step A — Purchase a package.**

    ⚠️ **NON-REFUNDABLE** — no refund is possible after purchase.

    `payment_method`: `"WALLET"` or `"GATEWAY"`

    - **WALLET**: Package (membership) activated immediately.
    - **GATEWAY**: Returns `payment_url`. Activated via webhook.

    **Request:**
    ```json
    {
      "package_id": "...",
      "payment_method": "WALLET",
      "auto_renew": false
    }
    ```
    """
    return await PackagePaymentService.initiate_package_purchase(
        user_id=current_user.id,
        package_id=data.package_id,
        payment_method=data.payment_method,
        auto_renew=data.auto_renew,
    )


# ─── Package History (user) ─────────────────────────────────────────

@router.get(
    "/packages/history",
    summary="My Package History",
    tags=["Payments – Packages"],
)
async def get_my_packages_history(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status_filter: Optional[str] = Query(
        None, alias="status",
        description="Filter by payment status: INITIATED | PENDING | SUCCESS | FAILED | REFUNDED | CANCELLED",
    ),
    search: Optional[str] = Query(None, description="Search by reference ID"),
    current_user=Depends(get_current_active_user),
):
    """
    **My Package History** — every package the user has purchased.

    Each entry exposes the originating package details, the resulting
    membership validity window, the amount paid, the payment method and
    the payment status. Sorted newest first.
    """
    return await HistoryService.get_my_packages_history(
        user_id=current_user.id,
        page=page,
        page_size=page_size,
        status_filter=status_filter,
        search=search,
    )


# ─── Package History (admin / manager) ─────────────────────────────

@router.get(
    "/packages/all-history",
    summary="All Packages Sold — Admin / Manager",
    tags=["Payments – Packages"],
)
async def get_all_packages_history(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status_filter: Optional[str] = Query(
        None, alias="status",
        description="Filter by payment status: INITIATED | PENDING | SUCCESS | FAILED | REFUNDED | CANCELLED",
    ),
    search: Optional[str] = Query(
        None,
        description="Search by reference ID or by buyer name/email",
    ),
    current_user=Depends(get_current_active_user),
):
    """
    **All Package Purchases Across the App.**

    **Access:** ADMIN, MANAGER only.

    Lists every package purchase ever made, with the buyer identity attached.
    """
    return await HistoryService.get_all_packages_history(
        actor_user_id=current_user.id,
        page=page,
        page_size=page_size,
        status_filter=status_filter,
        search=search,
    )


# ═══════════════════════════════════════════════════════════════════
# MODULE 5 — STORE ORDER
# ═══════════════════════════════════════════════════════════════════

@router.post(
    "/orders/checkout",
    summary="Place Store Order",
    status_code=status.HTTP_201_CREATED,
    tags=["Payments – Store Orders"],
)
async def checkout_order(
    data: StoreOrderRequest,
    current_user=Depends(get_current_active_user),
):
    """
    **Step A — Place store order & pay.**

    Inventory is reserved immediately. Released if payment fails.
    Order lifecycle: `PENDING → PAID → PROCESSING → PICKEDUP`

    `payment_method`: `"WALLET"` or `"GATEWAY"`

    - **WALLET**: Order set to PAID instantly.
    - **GATEWAY**: Returns `payment_url`. Order confirmed via webhook.

    **Request:**
    ```json
    {
      "items": [
        { "product_id": "...", "quantity": 2 }
      ],
      "payment_method": "GATEWAY",
      "notes": "optional note"
    }
    ```
    """
    return await StorePaymentService.create_order_and_pay(
        user_id=current_user.id,
        items=[{"product_id": i.product_id, "quantity": i.quantity} for i in data.items],
        payment_method=data.payment_method,
        notes=data.notes,
    )


# ─── Store Order History (user) ─────────────────────────────────────

@router.get(
    "/orders/history",
    summary="My Order History",
    tags=["Payments – Store Orders"],
)
async def get_my_orders_history(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status_filter: Optional[str] = Query(
        None, alias="status",
        description="Filter by order status: PENDING | PAID | PROCESSING | PICKEDUP | FAILED | REFUNDED | CANCELLED | COMPLETED",
    ),
    search: Optional[str] = Query(None, description="Search by order number, transaction id or notes"),
    current_user=Depends(get_current_active_user),
):
    """
    **My Order History** — every store order the user has checked out.

    Each entry includes the full line-item breakdown (product name, image,
    quantity, unit price, total), pricing totals, order status, payment
    method and status. Sorted newest first.
    """
    return await HistoryService.get_my_orders_history(
        user_id=current_user.id,
        page=page,
        page_size=page_size,
        status_filter=status_filter,
        search=search,
    )


# ─── Store Order History (admin / manager) ─────────────────────────
#
# Path note: the original specification calls for this endpoint at
#   GET /payments/packages/all-orders
# That URL is preserved verbatim so the existing API contract is honoured.
# An additional alias at /orders/all-history is exposed for discoverability.

@router.get(
    "/packages/all-orders",
    summary="All Store Orders — Admin / Manager",
    tags=["Payments – Store Orders"],
)
async def get_all_orders_history(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status_filter: Optional[str] = Query(
        None, alias="status",
        description="Filter by order status: PENDING | PAID | PROCESSING | PICKEDUP | FAILED | REFUNDED | CANCELLED | COMPLETED",
    ),
    search: Optional[str] = Query(
        None,
        description="Search by order number/transaction id/notes or by buyer name/email",
    ),
    current_user=Depends(get_current_active_user),
):
    """
    **All Store Orders Across the App.**

    **Access:** ADMIN, MANAGER only.

    Lists every store order ever placed, with the buyer identity and
    line-item breakdown attached.
    """
    return await HistoryService.get_all_orders_history(
        actor_user_id=current_user.id,
        page=page,
        page_size=page_size,
        status_filter=status_filter,
        search=search,
    )


@router.get(
    "/orders/all-history",
    summary="All Store Orders — Admin / Manager (alias)",
    tags=["Payments – Store Orders"],
    include_in_schema=True,
)
async def get_all_orders_history_alias(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status_filter: Optional[str] = Query(
        None, alias="status",
        description="Filter by order status",
    ),
    search: Optional[str] = Query(None),
    current_user=Depends(get_current_active_user),
):
    """
    Alias for `/payments/packages/all-orders` — exposes the same endpoint
    under a more discoverable path. Both routes return identical responses.

    **Access:** ADMIN, MANAGER only.
    """
    return await HistoryService.get_all_orders_history(
        actor_user_id=current_user.id,
        page=page,
        page_size=page_size,
        status_filter=status_filter,
        search=search,
    )


# ═══════════════════════════════════════════════════════════════════
# SHARED CALLBACKS (MyFatoorah redirects browser here)
# ═══════════════════════════════════════════════════════════════════

@router.get(
    "/callback/success",
    summary="Payment Success Callback",
    tags=["Payments – Callbacks"],
    response_class=HTMLResponse,
)
async def payment_success_callback(
    paymentId: str = Query(None, description="MyFatoorah PaymentId from redirect URL"),
    Id: str = Query(None, description="MyFatoorah Id from redirect URL"),
):
    """
    **Step B — Success URL.**

    MyFatoorah redirects the user's browser here after successful payment.

    **MyFatoorah appends these params automatically:**
    ```
    ?paymentId=<PaymentId>&Id=<PaymentId>
    ```

    Both params carry the **PaymentId** (long transaction-level ID, ~20 digits).
    This endpoint uses `KeyType="PaymentId"` when calling GetPaymentStatus.

    Flow:
    1. Extracts paymentId from URL
    2. Calls MyFatoorah GetPaymentStatus with KeyType="PaymentId"
    3. Retrieves CustomerReference from the invoice (our WALLET-xxx / BOOKING-xxx ref)
    4. Routes to the correct module to activate the purchase / credit wallet
    5. Returns a self-contained HTML page that deep-links back into the
       mobile app, with a graceful web fallback if the app is not installed.

    **HTML redirect page behaviour:**
    - Immediately fires the deep link: ``<APP_DEEP_LINK_SCHEME>://payment/success``
    - Detects via ``document.hidden`` whether the app opened (tab hides = app opened)
    - Falls back to ``APP_FALLBACK_URL`` after 1.5 s if the app is not installed
    - Shows a 3-second countdown with a manual "Open App" button as a safety net
    """
    # Use paymentId first, fall back to Id
    payment_id = paymentId or Id
    if not payment_id:
        logger.warning("Success callback received with no paymentId or Id param")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing paymentId. MyFatoorah did not provide a payment reference.",
        )

    module       = "UNKNOWN"
    reference_id = payment_id

    try:
        # key_type="PaymentId" — the value from the callback URL is always a PaymentId
        result = await WebhookService.route_event(
            invoice_id=payment_id,
            customer_reference="",   # resolved from GetPaymentStatus inside route_event
            key_type="PaymentId",
        )

        module       = result.get("module", "UNKNOWN")
        reference_id = result.get("reference_id", payment_id)

        logger.info(
            "Success callback processed: paymentId=%s module=%s ref=%s",
            payment_id, module, reference_id,
        )

    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Success callback error: paymentId=%s err=%s", payment_id, str(exc))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Payment confirmation failed. Please contact support.",
        )

    deep_link_url = (
        f"{settings.APP_DEEP_LINK_SCHEME}://payment/success"
        f"?reference_id={reference_id}&payment_id={payment_id}&module={module}"
    )

    html = _build_callback_html(
        success=True,
        deep_link_url=deep_link_url,
        fallback_url=settings.APP_FALLBACK_URL,
        reference_id=reference_id,
        payment_id=payment_id,
        module=module,
        message="",
    )
    return HTMLResponse(content=html, status_code=200)


@router.get(
    "/callback/error",
    summary="Payment Error Callback",
    tags=["Payments – Callbacks"],
    response_class=HTMLResponse,
)
async def payment_error_callback(
    paymentId: str = Query(None, description="MyFatoorah PaymentId from redirect URL"),
    Id: str = Query(None, description="MyFatoorah Id from redirect URL"),
):
    """
    **Step C — Error URL.**

    MyFatoorah redirects the user's browser here on payment failure or cancellation.

    **MyFatoorah appends these params automatically:**
    ```
    ?paymentId=<PaymentId>&Id=<PaymentId>
    ```

    Returns a self-contained HTML page that deep-links back into the mobile
    app's error screen, with a graceful web fallback if the app is not installed.

    **HTML redirect page behaviour:**
    - Immediately fires the deep link: ``<APP_DEEP_LINK_SCHEME>://payment/error``
    - Falls back to ``APP_FALLBACK_URL`` after 1.5 s if the app is not installed
    - Shows a 3-second countdown with a manual "Return to App" button
    """
    payment_id = paymentId or Id
    logger.warning("Payment error callback: paymentId=%s Id=%s", paymentId, Id)

    # Best-effort: resolve module name so frontend can show context-aware error
    module       = "UNKNOWN"
    reference_id = payment_id or ""

    if payment_id:
        try:
            status_result = await PaymentService.verify_payment(
                key=payment_id,
                key_type="PaymentId",
            )
            reference_id = status_result.get("reference_id", payment_id)
            if reference_id and "-" in reference_id:
                module = reference_id.split("-")[0]
        except Exception:
            pass  # Best-effort only — we still render the error page below

    deep_link_url = (
        f"{settings.APP_DEEP_LINK_SCHEME}://payment/error"
        f"?reference_id={reference_id}&payment_id={payment_id or ''}&module={module}"
    )

    html = _build_callback_html(
        success=False,
        deep_link_url=deep_link_url,
        fallback_url=settings.APP_FALLBACK_URL,
        reference_id=reference_id,
        payment_id=payment_id or "",
        module=module,
        message="Your payment could not be completed. Please try again.",
    )
    return HTMLResponse(content=html, status_code=200)


# ═══════════════════════════════════════════════════════════════════
# UNIFIED WEBHOOK (MyFatoorah server-to-server)
# ═══════════════════════════════════════════════════════════════════

@router.post(
    "/webhook",
    summary="MyFatoorah Webhook",
    status_code=status.HTTP_200_OK,
    tags=["Payments – Webhook"],
)
async def myfatoorah_webhook(
    request: Request,
    signature: str = Header(None, alias="signature"),
):
    """
    Single webhook endpoint for all MyFatoorah payment events.
    Routes to the correct module handler based on CustomerReference prefix.

    Security: HMAC-SHA256 signature validation using MYFATOORAH_WEBHOOK_SECRET.
    Returns 200 immediately to acknowledge receipt (MyFatoorah expects this).

    The webhook body contains the short InvoiceId — use KeyType="InvoiceId".
    """
    body = await request.body()

    # Validate HMAC signature
    if signature:
        if not PaymentService.verify_webhook_signature(body, signature):
            logger.warning("Webhook signature mismatch — rejecting request")
            raise HTTPException(status_code=401, detail="Invalid webhook signature")
    else:
        logger.warning("Webhook received without signature header")

    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    # MyFatoorah sends different event shapes — normalise
    invoice_id = str(
        payload.get("InvoiceId")
        or payload.get("invoiceId")
        or payload.get("Data", {}).get("InvoiceId", "")
    )
    customer_reference = (
        payload.get("CustomerReference")
        or payload.get("customerReference")
        or payload.get("Data", {}).get("CustomerReference", "")
        or ""
    )
    invoice_status = (
        payload.get("InvoiceStatus")
        or payload.get("invoiceStatus")
        or payload.get("Data", {}).get("InvoiceStatus", "")
        or ""
    )

    if not invoice_id:
        logger.warning("Webhook missing invoice_id: %s", payload)
        return {"received": True, "processed": False, "reason": "missing_invoice_id"}

    logger.info(
        "Webhook received: invoice=%s ref=%s status=%s",
        invoice_id, customer_reference, invoice_status,
    )

    # Only process paid events
    if invoice_status and invoice_status.lower() != "paid":
        logger.info("Webhook non-paid event ignored: status=%s", invoice_status)
        return {"received": True, "processed": False, "reason": "not_paid"}

    try:
        # Webhook body carries the short InvoiceId → use KeyType="InvoiceId"
        result = await WebhookService.route_event(
            invoice_id=invoice_id,
            customer_reference=customer_reference,
            key_type="InvoiceId",
        )
        return {"received": True, "processed": True, "result": result}
    except HTTPException as exc:
        logger.error("Webhook routing error: %s", exc.detail)
        # Return 200 to prevent MyFatoorah retries for business logic errors
        return {"received": True, "processed": False, "error": exc.detail}
    except Exception as exc:
        logger.exception("Unexpected webhook error: %s", str(exc))
        raise HTTPException(status_code=500, detail="Webhook processing error")