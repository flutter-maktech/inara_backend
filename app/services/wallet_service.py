"""
app/services/wallet_service.py
================================
MODULE 1 — WALLET

Handles all wallet operations:
  - Balance query
  - Top-up initiation via MyFatoorah gateway
  - Top-up confirmation (called by WebhookService after payment verified)
  - Wallet debit (called internally by Booking, Membership, Package, Order)
  - Transaction history

─────────────────────────────────────────────────────────────────────────────
Why wallet topups appear as "Orders" in the MyFatoorah portal
─────────────────────────────────────────────────────────────────────────────
MyFatoorah's SendPayment API always creates an Invoice, which their portal
displays under "Orders". Their "Deposits" section is for manual bank wire
transfers, not API-initiated payments. This is expected behaviour — our
Wallet table (balance field) is the authoritative source of truth for user
balances, not the MF portal's categorization.
"""

from __future__ import annotations

import logging
import math
from typing import Optional

from fastapi import HTTPException, status

from app.core.payment_service import PaymentService, make_wallet_ref
from app.db.db_client import prisma

logger = logging.getLogger(__name__)


class WalletService:

    # ── Public: GET /payments/wallet/balance ─────────────────────────────────

    @staticmethod
    async def get_balance(user_id: str) -> dict:
        wallet = await prisma.wallet.find_unique(where={"userId": user_id})
        if not wallet:
            # Auto-create wallet on first query
            wallet = await prisma.wallet.create(
                data={"userId": user_id, "balance": 0.0, "currency": "QAR"}
            )
        return {
            "wallet_id": wallet.id,
            "balance": wallet.balance,
            "currency": wallet.currency,
        }

    # ── Public: POST /payments/wallet/topup ──────────────────────────────────

    @staticmethod
    async def initiate_topup(user_id: str, amount: float) -> dict:
        """
        Step A — Create a MyFatoorah checkout session for wallet top-up.

        Returns payment_url for the frontend to redirect the user.
        The wallet is credited only after the gateway confirms payment
        via the success callback or server-to-server webhook.
        """
        if amount <= 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Amount must be greater than 0",
            )

        user = await prisma.user.find_unique(where={"id": user_id})
        if not user:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

        # Ensure wallet exists before creating the payment log
        wallet = await prisma.wallet.find_unique(where={"userId": user_id})
        if not wallet:
            wallet = await prisma.wallet.create(
                data={"userId": user_id, "balance": 0.0, "currency": "QAR"}
            )

        reference_id = make_wallet_ref()

        try:
            session = await PaymentService.create_payment_session(
                amount=amount,
                customer_name=user.name,
                customer_email=user.email,
                customer_reference=reference_id,
                description=f"Wallet top-up — {user.email}",
            )
        except ValueError as exc:
            logger.error("Wallet topup gateway error: user=%s err=%s", user_id, exc)
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))

        # Record the initiated payment — credited only after gateway confirms
        await prisma.paymentlog.create(
            data={
                "userId": user_id,
                "module": "WALLET",
                "referenceId": reference_id,
                "idempotencyKey": reference_id,
                "amount": amount,
                "currency": "QAR",
                "paymentMethod": "GATEWAY",
                "status": "INITIATED",
                "gatewayInvoiceId": session["invoice_id"],
            }
        )

        logger.info(
            "Wallet topup initiated: user=%s ref=%s invoiceId=%s amount=%.2f",
            user_id, reference_id, session["invoice_id"], amount,
        )

        return {
            "payment_url": session["payment_url"],
            "invoice_id": session["invoice_id"],
            "reference_id": reference_id,
            "amount": amount,
            "currency": "QAR",
            "message": "Redirect user to payment_url to complete wallet top-up.",
        }

    # ── Internal: called by WebhookService on confirmed WALLET payment ────────

    @staticmethod
    async def credit_wallet(
        user_id: str,
        amount: float,
        reference_id: str,
        invoice_id: str,
        payment_log_id: Optional[str] = None,
    ) -> None:
        """
        Atomically credit the wallet and record a WalletTransaction.

        Idempotent — if a WalletTransaction with this reference_id already
        exists, the operation is skipped. Safe to call from both the browser
        success callback and the server-to-server webhook.
        """
        # Idempotency guard — prevent double-crediting
        existing = await prisma.wallettransaction.find_first(
            where={"referenceId": reference_id}
        )
        if existing:
            logger.info("Wallet credit already applied for ref=%s — skipping", reference_id)
            return

        wallet = await prisma.wallet.find_unique(where={"userId": user_id})
        if not wallet:
            wallet = await prisma.wallet.create(
                data={"userId": user_id, "balance": 0.0, "currency": "QAR"}
            )

        balance_before = wallet.balance
        balance_after = round(balance_before + amount, 2)

        # Update balance
        await prisma.wallet.update(
            where={"id": wallet.id},
            data={"balance": balance_after},
        )

        # Record the transaction in WalletTransaction history
        await prisma.wallettransaction.create(
            data={
                "walletId": wallet.id,
                "type": "DEPOSIT",
                "amount": amount,
                "balanceBefore": balance_before,
                "balanceAfter": balance_after,
                "description": "Wallet top-up via MyFatoorah",
                "referenceId": reference_id,
                "paymentLogId": payment_log_id,
            }
        )

        # Mark the PaymentLog as SUCCESS and store the resolved PaymentId
        log = await prisma.paymentlog.find_first(where={"referenceId": reference_id})
        if log:
            await prisma.paymentlog.update(
                where={"id": log.id},
                data={"status": "SUCCESS", "gatewayPaymentId": invoice_id},
            )

        logger.info(
            "Wallet credited: user=%s ref=%s amount=%.2f balance=%.2f→%.2f",
            user_id, reference_id, amount, balance_before, balance_after,
        )

    # ── Internal: debit wallet (Booking, Membership, Package, Order) ──────────

    @staticmethod
    async def debit_wallet(
        user_id: str,
        amount: float,
        description: str,
        reference_id: str,
        payment_log_id: Optional[str] = None,
    ) -> None:
        """
        Deduct amount from wallet. Raises HTTP 402 if balance is insufficient.
        """
        wallet = await prisma.wallet.find_unique(where={"userId": user_id})
        if not wallet:
            raise HTTPException(
                status_code=status.HTTP_402_PAYMENT_REQUIRED,
                detail="Wallet not found. Please top up your wallet first.",
            )

        if wallet.balance < amount:
            raise HTTPException(
                status_code=status.HTTP_402_PAYMENT_REQUIRED,
                detail=(
                    f"Insufficient wallet balance. "
                    f"Available: {wallet.balance:.2f} QAR, Required: {amount:.2f} QAR"
                ),
            )

        balance_before = wallet.balance
        balance_after = round(balance_before - amount, 2)

        await prisma.wallet.update(
            where={"id": wallet.id},
            data={"balance": balance_after},
        )
        await prisma.wallettransaction.create(
            data={
                "walletId": wallet.id,
                "type": "DEBIT",
                "amount": amount,
                "balanceBefore": balance_before,
                "balanceAfter": balance_after,
                "description": description,
                "referenceId": reference_id,
                "paymentLogId": payment_log_id,
            }
        )

        logger.info(
            "Wallet debited: user=%s ref=%s amount=%.2f balance=%.2f→%.2f",
            user_id, reference_id, amount, balance_before, balance_after,
        )

    # ── Internal: refund to wallet ────────────────────────────────────────────

    @staticmethod
    async def refund_to_wallet(
        user_id: str,
        amount: float,
        description: str,
        reference_id: str,
    ) -> None:
        """Credit a refund back to the wallet. Uses a REFUND- prefixed reference."""
        await WalletService.credit_wallet(
            user_id=user_id,
            amount=amount,
            reference_id=f"REFUND-{reference_id}",
            invoice_id="",
        )
        logger.info(
            "Wallet refund applied: user=%s ref=%s amount=%.2f",
            user_id, reference_id, amount,
        )

    # ── Public: GET /payments/wallet/history ─────────────────────────────────

    @staticmethod
    async def get_transaction_history(
        user_id: str, page: int = 1, page_size: int = 20
    ) -> dict:
        """
        Paginated wallet transaction history.

        Returns all DEPOSIT and DEBIT transactions ordered newest-first.
        DEPOSIT entries are created after gateway payment confirmation —
        they will appear here once the success callback or webhook fires.
        """
        wallet = await prisma.wallet.find_unique(where={"userId": user_id})
        if not wallet:
            return {
                "transactions": [],
                "total": 0,
                "page": page,
                "page_size": page_size,
                "total_pages": 0,
            }

        total = await prisma.wallettransaction.count(where={"walletId": wallet.id})
        skip = (page - 1) * page_size

        txns = await prisma.wallettransaction.find_many(
            where={"walletId": wallet.id},
            order={"createdAt": "desc"},
            skip=skip,
            take=page_size,
        )

        return {
            "transactions": [
                {
                    "id": t.id,
                    "type": t.type,
                    "amount": t.amount,
                    "balance_before": t.balanceBefore,
                    "balance_after": t.balanceAfter,
                    "description": t.description,
                    "reference_id": t.referenceId,
                    "created_at": t.createdAt.isoformat(),
                }
                for t in txns
            ],
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": math.ceil(total / page_size) if total else 0,
        }
