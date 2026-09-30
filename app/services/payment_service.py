"""
app/core/payment_service.py
============================
MyFatoorah Payment Gateway — core integration layer.

All five payment modules (Wallet, Booking, Membership, Package, Order)
call this single service to interact with the MyFatoorah API.

Architecture:
  - PaymentService  → Gateway sessions, payment verification, refunds, webhook HMAC
  - Each module-specific service calls PaymentService and handles its own DB logic

Reference: https://docs.myfatoorah.com/docs/

─────────────────────────────────────────────────────────────────────────────
MyFatoorah Key Types (GetPaymentStatus)
─────────────────────────────────────────────────────────────────────────────
MyFatoorah's GetPaymentStatus accepts three KeyType values:

  "PaymentId"   → the long numeric ID in the callback URL (?paymentId=XXX)
  "InvoiceId"   → the short invoice number (e.g. 2026000003) from SendPayment response
  "InvoiceValue"→ not used here

When called from the BROWSER CALLBACK:
  URL param ?paymentId = PaymentId (long) → use KeyType="PaymentId"

When called from the SERVER WEBHOOK:
  Webhook body contains InvoiceId (short) → use KeyType="InvoiceId"

This distinction is why verify_payment() accepts a key_type parameter.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
import string
from typing import Any, Optional

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

MYFATOORAH_BASE_URL: str = settings.MYFATOORAH_BASE_URL.rstrip("/")
MYFATOORAH_API_KEY: str = settings.MYFATOORAH_API_KEY

_HEADERS = {
    "Authorization": f"Bearer {MYFATOORAH_API_KEY}",
    "Content-Type": "application/json",
    "Accept": "application/json",
}

DEFAULT_CURRENCY: str = "QAR"
_TIMEOUT = httpx.Timeout(30.0)


# ─────────────────────────────────────────────────────────────────────────────
# Reference ID helpers
# ─────────────────────────────────────────────────────────────────────────────

def _make_reference_id(prefix: str) -> str:
    """
    Generate a prefixed, URL-safe random reference ID.

    Format: PREFIX-XXXXXXXXXX  (10 alphanumeric chars)
    Examples: WALLET-A3F9C2XK11, BOOKING-Z7TM3QWP4R
    """
    alphabet = string.ascii_uppercase + string.digits
    suffix = "".join(secrets.choice(alphabet) for _ in range(10))
    return f"{prefix}-{suffix}"


def make_wallet_ref() -> str:
    return _make_reference_id("WALLET")


def make_booking_ref() -> str:
    return _make_reference_id("BOOKING")


def make_membership_ref() -> str:
    return _make_reference_id("MEMBERSHIP")


def make_package_ref() -> str:
    return _make_reference_id("PACKAGE")


def make_order_ref() -> str:
    return _make_reference_id("ORDER")


# ─────────────────────────────────────────────────────────────────────────────
# PaymentService
# ─────────────────────────────────────────────────────────────────────────────

class PaymentService:
    """
    Thin wrapper around the MyFatoorah v2 REST API.
    All methods are static — no instance state required.
    """

    # ── Session / Checkout ────────────────────────────────────────────────────

    @staticmethod
    async def create_payment_session(
        amount: float,
        customer_name: str,
        customer_email: str,
        customer_reference: str,
        description: str,
        currency: str = DEFAULT_CURRENCY,
    ) -> dict[str, Any]:
        """
        Call MyFatoorah SendPayment (v2) to create a hosted payment session.

        Returns:
          - payment_url   : str  — redirect user here
          - invoice_id    : str  — MyFatoorah short InvoiceId (e.g. "2026000003")
          - reference_id  : str  — echo of customer_reference

        Raises ValueError on gateway failure.
        """
        payload = {
            "NotificationOption": "LNK",
            "InvoiceValue": round(amount, 2),
            "CustomerName": customer_name,
            "CustomerEmail": customer_email,
            "CustomerReference": customer_reference,
            "InvoiceItemsDesc": description,
            "CallBackUrl": settings.PAYMENT_SUCCESS_URL,
            "ErrorUrl": settings.PAYMENT_ERROR_URL,
            "Language": "en",
            "DisplayCurrencyIso": currency,
        }

        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(
                f"{MYFATOORAH_BASE_URL}/v2/SendPayment",
                json=payload,
                headers=_HEADERS,
            )

        data = resp.json()

        if not data.get("IsSuccess"):
            message = data.get("Message", "MyFatoorah: payment session creation failed")
            logger.error("MyFatoorah SendPayment failed: %s | ref=%s", message, customer_reference)
            raise ValueError(f"Payment gateway error: {message}")

        invoice_data = data.get("Data", {})
        return {
            "payment_url": invoice_data.get("InvoiceURL"),
            "invoice_id": str(invoice_data.get("InvoiceId", "")),
            "reference_id": customer_reference,
        }

    # ── Payment Verification ──────────────────────────────────────────────────

    @staticmethod
    async def verify_payment(
        key: str,
        key_type: str = "PaymentId",
    ) -> dict[str, Any]:
        """
        Call MyFatoorah GetPaymentStatus to retrieve payment result.

        Parameters
        ----------
        key       : The identifier value to look up.
        key_type  : One of "PaymentId" | "InvoiceId".

                    Use "PaymentId" (default) when the value comes from the
                    browser redirect URL (?paymentId=XXXXX) — this is the long
                    numeric PaymentId MyFatoorah appends to the callback URL.

                    Use "InvoiceId" when you have the short InvoiceId returned
                    by SendPayment (e.g. "2026000003") — typically from webhooks
                    or your own stored gatewayInvoiceId.

        Returns normalised dict with:
          - is_paid        : bool
          - invoice_id     : str   (short InvoiceId)
          - payment_id     : str   (long PaymentId of the successful transaction)
          - invoice_status : str   ("Paid", "Unpaid", "Failed", …)
          - amount         : float
          - currency       : str
          - reference_id   : str   (CustomerReference — our module-prefixed ref)
          - raw            : dict  (full gateway response Data object)
        """
        payload = {"Key": key, "KeyType": key_type}

        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(
                f"{MYFATOORAH_BASE_URL}/v2/GetPaymentStatus",
                json=payload,
                headers=_HEADERS,
            )

        data = resp.json()

        if not data.get("IsSuccess"):
            message = data.get("Message", "MyFatoorah: payment status check failed")
            logger.error(
                "MyFatoorah GetPaymentStatus failed: %s | key=%s keyType=%s",
                message, key, key_type,
            )
            raise ValueError(f"Payment verification error: {message}")

        invoice_data = data.get("Data", {})
        invoice_status: str = invoice_data.get("InvoiceStatus", "")
        is_paid = invoice_status.lower() == "paid"

        # Extract the successful PaymentId from the InvoiceTransactions list
        # This is the long PaymentId needed for MakeRefund calls
        payment_id = ""
        transactions = invoice_data.get("InvoiceTransactions") or []
        for txn in transactions:
            if txn.get("TransactionStatus", "").lower() == "success":
                payment_id = str(txn.get("PaymentId", ""))
                break
        # Fallback: use the key itself if it looks like a PaymentId
        if not payment_id and key_type == "PaymentId":
            payment_id = key

        return {
            "is_paid": is_paid,
            "invoice_id": str(invoice_data.get("InvoiceId", "")),
            "payment_id": payment_id,
            "invoice_status": invoice_status,
            "amount": float(invoice_data.get("InvoiceValue", 0)),
            "currency": invoice_data.get("InvoiceCurrencyIso", DEFAULT_CURRENCY),
            "reference_id": invoice_data.get("CustomerReference", ""),
            "raw": invoice_data,
        }

    # ── Refund ────────────────────────────────────────────────────────────────

    @staticmethod
    async def make_refund(
        payment_id: str,
        amount: float,
        reason: str = "Customer request",
        comment: str = "",
    ) -> dict[str, Any]:
        """
        Call MyFatoorah MakeRefund for the given PaymentId.

        payment_id  : The long PaymentId (from InvoiceTransactions, NOT InvoiceId).
        amount      : Amount to refund (partial refund supported).

        Returns dict with:
          - success      : bool
          - refund_status: str
          - raw          : dict
        """
        payload = {
            "Key": payment_id,
            "KeyType": "PaymentId",
            "RefundChargeOnCustomer": False,
            "ServiceChargeOnCustomer": False,
            "Amount": round(amount, 2),
            "Comment": comment or reason,
            "AmountDeductedFromSupplier": 0,
        }

        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(
                f"{MYFATOORAH_BASE_URL}/v2/MakeRefund",
                json=payload,
                headers=_HEADERS,
            )

        data = resp.json()

        if not data.get("IsSuccess"):
            message = data.get("Message", "Refund failed")
            logger.error("MyFatoorah MakeRefund failed: %s | paymentId=%s", message, payment_id)
            raise ValueError(f"Refund error: {message}")

        refund_data = data.get("Data", {})
        return {
            "success": True,
            "refund_status": refund_data.get("RefundStatus", "Refunded"),
            "raw": refund_data,
        }

    # ── Webhook HMAC verification ─────────────────────────────────────────────

    @staticmethod
    def verify_webhook_signature(body: bytes, signature: str) -> bool:
        """
        Validate a MyFatoorah webhook HMAC-SHA256 signature.

        MyFatoorah sends:
          Header: signature: <HMAC-SHA256-hex>
          Body:   raw JSON bytes

        The secret is MYFATOORAH_WEBHOOK_SECRET from .env.
        """
        secret = getattr(settings, "MYFATOORAH_WEBHOOK_SECRET", "")
        if not secret:
            logger.warning("MYFATOORAH_WEBHOOK_SECRET not configured — skipping HMAC check")
            return True  # Lock this down before going to production

        expected = hmac.new(
            secret.encode(),
            body,
            hashlib.sha256,
        ).hexdigest()

        return hmac.compare_digest(expected, signature)
