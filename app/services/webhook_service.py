"""
app/services/webhook_service.py
=================================
Unified webhook router.

MyFatoorah sends a single webhook for all payment events.
This service:
  1. Verifies payment with MyFatoorah (GetPaymentStatus)
  2. Reads the CustomerReference to identify the module
  3. Routes to the correct module's confirmation handler

Reference prefix → Module mapping:
  WALLET-*     → WalletService.credit_wallet
  BOOKING-*    → BookingPaymentService.confirm_gateway_booking
  MEMBERSHIP-* → MembershipPaymentService.confirm_gateway_membership
  PACKAGE-*    → PackagePaymentService.confirm_gateway_package
  ORDER-*      → StorePaymentService.confirm_gateway_order

─────────────────────────────────────────────────────────────────────────────
Key Type Routing
─────────────────────────────────────────────────────────────────────────────
MyFatoorah uses two different ID types that are easy to confuse:

  PaymentId  (long ~20 digit number) — what appears in the callback URL:
             ?paymentId=07076740103349125372
             Use KeyType="PaymentId" with GetPaymentStatus.

  InvoiceId  (short number like 2026000003) — returned by SendPayment,
             also sent inside the server-to-server webhook body.
             Use KeyType="InvoiceId" with GetPaymentStatus.

route_event() accepts a key_type parameter so each caller passes the
correct type. The browser callback uses "PaymentId"; the webhook uses "InvoiceId".
"""

from __future__ import annotations

import logging

from fastapi import HTTPException

from app.core.payment_service import PaymentService
from app.db.db_client import prisma

logger = logging.getLogger(__name__)


class WebhookService:

    @staticmethod
    async def route_event(
        invoice_id: str,
        customer_reference: str,
        key_type: str = "PaymentId",
    ) -> dict:
        """
        Verify payment with MyFatoorah, then dispatch to the correct module.

        Parameters
        ----------
        invoice_id         : The key value to pass to GetPaymentStatus.
        customer_reference : Our prefixed reference (e.g. "WALLET-A3F9C2").
                             May be "" when called from the browser callback —
                             we resolve it from the GetPaymentStatus response.
        key_type           : "PaymentId" (browser callback) or "InvoiceId" (webhook).
                             Defaults to "PaymentId" since the callback is the
                             more common entry point.

        Both paths are idempotent — module services guard against double-processing.
        """
        # Late import to avoid circular imports
        from app.services.wallet_service import WalletService
        from app.services.booking_payment_service import BookingPaymentService
        from app.services.membership_payment_service import MembershipPaymentService
        from app.services.package_payment_service import PackagePaymentService
        from app.services.store_payment_service import StorePaymentService

        try:
            result = await PaymentService.verify_payment(
                key=invoice_id,
                key_type=key_type,
            )
        except ValueError as exc:
            logger.error(
                "Payment verification failed: key=%s keyType=%s err=%s",
                invoice_id, key_type, exc,
            )
            raise HTTPException(status_code=502, detail=str(exc))

        if not result["is_paid"]:
            logger.info(
                "route_event: key=%s status=%s — not paid, skipping",
                invoice_id, result["invoice_status"],
            )
            return {
                "processed": False,
                "reason": f"Payment status: {result['invoice_status']}",
            }

        amount = result["amount"]

        # Resolve CustomerReference: prefer what was passed in (webhook case),
        # then fall back to what GetPaymentStatus returned (browser callback case).
        reference_id = customer_reference or result.get("reference_id", "")

        if not reference_id:
            logger.error(
                "Cannot route payment: no CustomerReference found for key=%s keyType=%s. "
                "Ensure CustomerReference is set when creating the payment session.",
                invoice_id, key_type,
            )
            return {"processed": False, "reason": "missing_customer_reference"}

        # Determine module from prefix (WALLET-xxx → "WALLET")
        module = reference_id.split("-")[0].upper()

        # Retrieve the PaymentLog to get user_id
        log = await prisma.paymentlog.find_first(
            where={"referenceId": reference_id}
        )
        if not log:
            logger.error(
                "PaymentLog not found for ref=%s. "
                "This happens if the initiation step did not complete.",
                reference_id,
            )
            return {"processed": False, "reason": "payment_log_not_found"}

        user_id = log.userId

        # Store the resolved PaymentId on the log for future refund calls
        # result["payment_id"] contains the long PaymentId from InvoiceTransactions
        resolved_payment_id = result.get("payment_id", "")
        resolved_invoice_id = result.get("invoice_id", invoice_id)

        logger.info(
            "Routing payment: module=%s ref=%s user=%s amount=%.2f "
            "invoiceId=%s paymentId=%s",
            module, reference_id, user_id, amount,
            resolved_invoice_id, resolved_payment_id,
        )

        if module == "WALLET":
            await WalletService.credit_wallet(
                user_id=user_id,
                amount=amount,
                reference_id=reference_id,
                invoice_id=resolved_payment_id or invoice_id,
                payment_log_id=log.id,
            )
            return {
                "module": "WALLET",
                "processed": True,
                "amount": amount,
                "reference_id": reference_id,
            }

        elif module == "BOOKING":
            await BookingPaymentService.confirm_gateway_booking(
                user_id=user_id,
                reference_id=reference_id,
                invoice_id=resolved_payment_id or invoice_id,
                amount=amount,
            )
            return {
                "module": "BOOKING",
                "processed": True,
                "reference_id": reference_id,
            }

        elif module == "MEMBERSHIP":
            await MembershipPaymentService.confirm_gateway_membership(
                user_id=user_id,
                reference_id=reference_id,
                invoice_id=resolved_payment_id or invoice_id,
                amount=amount,
            )
            return {
                "module": "MEMBERSHIP",
                "processed": True,
                "reference_id": reference_id,
            }

        elif module == "PACKAGE":
            await PackagePaymentService.confirm_gateway_package(
                user_id=user_id,
                reference_id=reference_id,
                invoice_id=resolved_payment_id or invoice_id,
                amount=amount,
            )
            return {
                "module": "PACKAGE",
                "processed": True,
                "reference_id": reference_id,
            }

        elif module == "ORDER":
            await StorePaymentService.confirm_gateway_order(
                user_id=user_id,
                reference_id=reference_id,
                invoice_id=resolved_payment_id or invoice_id,
                amount=amount,
            )
            return {
                "module": "ORDER",
                "processed": True,
                "reference_id": reference_id,
            }

        else:
            logger.error(
                "Unknown module prefix '%s' in reference '%s'. "
                "Valid prefixes: WALLET, BOOKING, MEMBERSHIP, PACKAGE, ORDER.",
                module, reference_id,
            )
            return {
                "processed": False,
                "reason": f"unknown_module_prefix: {module}",
                "reference_id": reference_id,
            }
