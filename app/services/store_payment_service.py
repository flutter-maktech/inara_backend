"""
app/services/store_payment_service.py
=======================================
MODULE 5 — STORE ORDER PAYMENTS

Order lifecycle: PENDING → PAID → PROCESSING → PICKEDUP

On inventory:
  - Reserved (decremented) immediately on order creation
  - Released back if payment fails

WALLET → order created as PAID immediately
GATEWAY → order PENDING until webhook confirms
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import List

from fastapi import HTTPException, status

from app.core.payment_service import PaymentService, make_order_ref
from app.core.notification_service import notify_user
from app.db.db_client import prisma
from app.services.wallet_service import WalletService

logger = logging.getLogger(__name__)


class StorePaymentService:

    # ── POST /payments/orders/checkout ────────────────────────────────────────

    @staticmethod
    async def create_order_and_pay(
        user_id: str,
        items: List[dict],
        payment_method: str,
        notes: str | None = None,
    ) -> dict:
        """
        Step A — Create order, reserve inventory, initiate payment.

        items: [{"product_id": "...", "quantity": N}, ...]
        """
        payment_method = payment_method.upper()
        if payment_method not in ("WALLET", "GATEWAY"):
            raise HTTPException(status_code=400, detail="payment_method must be 'WALLET' or 'GATEWAY'")

        user = await prisma.user.find_unique(where={"id": user_id})
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        # ── Validate products & compute total ─────────────────────────────────
        order_items = []
        subtotal = 0.0
        reserved_products = []  # track for rollback

        for item in items:
            product = await prisma.product.find_unique(where={"id": item["product_id"]})
            if not product:
                raise HTTPException(status_code=404, detail=f"Product {item['product_id']} not found")
            if product.status == "OUT_OF_STOCK" or product.stockQuantity < item["quantity"]:
                raise HTTPException(
                    status_code=400,
                    detail=f"Insufficient stock for '{product.name}'. Available: {product.stockQuantity}",
                )

            unit_price = product.discountPrice or product.price
            line_total = round(unit_price * item["quantity"], 2)
            subtotal += line_total
            order_items.append({
                "product_id": item["product_id"],
                "product": product,
                "quantity": item["quantity"],
                "price": unit_price,
                "total": line_total,
            })

        subtotal = round(subtotal, 2)
        total = subtotal  # No tax/discount in base spec; extend here if needed

        reference_id = make_order_ref()
        order_number = f"ORD-{uuid.uuid4().hex[:8].upper()}"

        # ── Reserve inventory atomically ──────────────────────────────────────
        for oi in order_items:
            await prisma.product.update(
                where={"id": oi["product_id"]},
                data={"stockQuantity": oi["product"].stockQuantity - oi["quantity"]},
            )
            reserved_products.append(oi)

        # ── Create PENDING order ──────────────────────────────────────────────
        try:
            order = await prisma.order.create(
                data={
                    "orderNumber": order_number,
                    "userId": user_id,
                    "orderType": "PRODUCT",
                    "subtotal": subtotal,
                    "discount": 0.0,
                    "tax": 0.0,
                    "total": total,
                    "status": "PENDING",
                    "paymentMethod": payment_method,
                    "notes": notes,
                }
            )

            for oi in order_items:
                await prisma.orderitem.create(
                    data={
                        "orderId": order.id,
                        "productId": oi["product_id"],
                        "quantity": oi["quantity"],
                        "price": oi["price"],
                        "total": oi["total"],
                    }
                )
        except Exception as exc:
            # Rollback inventory
            await _release_inventory(reserved_products)
            logger.error("Order creation failed, inventory released: %s", exc)
            raise HTTPException(status_code=500, detail="Order creation failed. Inventory has been released.")

        # ── WALLET payment ────────────────────────────────────────────────────
        if payment_method == "WALLET":
            log = await prisma.paymentlog.create(
                data={
                    "userId": user_id,
                    "module": "ORDER",
                    "referenceId": reference_id,
                    "idempotencyKey": reference_id,
                    "amount": total,
                    "currency": "QAR",
                    "paymentMethod": "WALLET",
                    "status": "INITIATED",
                }
            )

            try:
                await WalletService.debit_wallet(
                    user_id=user_id,
                    amount=total,
                    description=f"Store order {order_number}",
                    reference_id=reference_id,
                    payment_log_id=log.id,
                )
            except HTTPException:
                # Rollback: delete order items, order, release inventory
                await prisma.orderitem.delete_many(where={"orderId": order.id})
                await prisma.order.delete(where={"id": order.id})
                await _release_inventory(reserved_products)
                await prisma.paymentlog.update(
                    where={"id": log.id},
                    data={"status": "FAILED", "errorMessage": "Insufficient wallet balance"},
                )
                raise

            # Mark order PAID
            now = datetime.now(timezone.utc)
            await prisma.order.update(
                where={"id": order.id},
                data={
                    "status": "PAID",
                    "paidAt": now,
                    "paymentLogId": log.id,
                    "transactionId": reference_id,
                },
            )

            # Update salesCount on products
            await _increment_sales_count(order_items)

            await prisma.paymentlog.update(
                where={"id": log.id},
                data={"status": "SUCCESS"},
            )

            logger.info("Store order PAID via WALLET: user=%s order=%s total=%.2f", user_id, order_number, total)

            # ── Notify user (in-app + WhatsApp, concurrent, best-effort) ──
            try:
                await notify_user(
                    user_id=user_id,
                    title="Order Confirmed",
                    message=(
                        f"Order #{order_number} confirmed. "
                        f"Total: QAR {total:.2f}. We'll let you know when it's ready for pickup."
                    ),
                    notification_type="SUCCESS",
                    send_via_app=True,
                    send_via_whatsapp=True,
                )
            except Exception as exc:
                logger.warning("Order wallet-paid notification failed (non-fatal): %s", exc)

            return {
                "status": "PAID",
                "order_id": order.id,
                "order_number": order_number,
                "total": total,
                "currency": "QAR",
                "payment_method": "WALLET",
                "message": "Order placed and payment confirmed.",
            }

        # ── GATEWAY payment ───────────────────────────────────────────────────
        try:
            session = await PaymentService.create_payment_session(
                amount=total,
                customer_name=user.name,
                customer_email=user.email,
                customer_reference=reference_id,
                description=f"Store order {order_number} — {len(order_items)} item(s)",
            )
        except ValueError as exc:
            # Rollback order and inventory
            await prisma.orderitem.delete_many(where={"orderId": order.id})
            await prisma.order.delete(where={"id": order.id})
            await _release_inventory(reserved_products)
            raise HTTPException(status_code=502, detail=str(exc))

        # FIX: Prisma Python client requires Json? fields to be serialized
        # as a JSON string using json.dumps(). Passing a raw Python dict
        # causes MissingRequiredValueError because the client cannot
        # distinguish between a relation input and a Json scalar value.
        log = await prisma.paymentlog.create(
            data={
                "userId": user_id,
                "module": "ORDER",
                "referenceId": reference_id,
                "idempotencyKey": reference_id,
                "amount": total,
                "currency": "QAR",
                "paymentMethod": "GATEWAY",
                "status": "INITIATED",
                "gatewayInvoiceId": session["invoice_id"],
                "gatewayResponse": json.dumps({"order_id": order.id}),
            }
        )

        await prisma.order.update(
            where={"id": order.id},
            data={"paymentLogId": log.id},
        )

        logger.info("Store order gateway payment initiated: user=%s order=%s ref=%s", user_id, order_number, reference_id)

        return {
            "status": "PENDING",
            "order_id": order.id,
            "order_number": order_number,
            "payment_url": session["payment_url"],
            "invoice_id": session["invoice_id"],
            "reference_id": reference_id,
            "total": total,
            "currency": "QAR",
            "message": "Redirect user to payment_url. Order will be confirmed automatically after payment.",
        }

    # ── Internal: called by WebhookService ───────────────────────────────────

    @staticmethod
    async def confirm_gateway_order(
        user_id: str,
        reference_id: str,
        invoice_id: str,
        amount: float,
    ) -> None:
        """Mark a PENDING order as PAID after gateway confirmation. Idempotent."""
        log = await prisma.paymentlog.find_first(
            where={"referenceId": reference_id, "module": "ORDER"}
        )
        if not log:
            logger.error("Order payment log not found: ref=%s", reference_id)
            return

        if log.status == "SUCCESS":
            logger.info("Order already confirmed for ref=%s — skipping", reference_id)
            return

        # gatewayResponse may be a dict (Prisma returns parsed Json) or a JSON string
        raw_meta = log.gatewayResponse or {}
        if isinstance(raw_meta, str):
            try:
                gateway_meta = json.loads(raw_meta)
            except (json.JSONDecodeError, ValueError):
                gateway_meta = {}
        else:
            gateway_meta = raw_meta

        order_id = gateway_meta.get("order_id") if isinstance(gateway_meta, dict) else None

        order = await prisma.order.find_first(
            where={"userId": user_id, "status": "PENDING"},
            order={"createdAt": "desc"},
        )
        if order_id:
            order = await prisma.order.find_unique(where={"id": order_id})

        if not order:
            logger.error("No pending order found for user=%s ref=%s", user_id, reference_id)
            return

        now = datetime.now(timezone.utc)
        await prisma.order.update(
            where={"id": order.id},
            data={
                "status": "PAID",
                "paidAt": now,
                "transactionId": invoice_id,
            },
        )

        # Update sales counts
        items = await prisma.orderitem.find_many(where={"orderId": order.id})
        await _increment_sales_count_by_items(items)

        await prisma.paymentlog.update(
            where={"id": log.id},
            data={"status": "SUCCESS", "gatewayPaymentId": invoice_id},
        )

        logger.info("Store order gateway confirmed: user=%s order=%s ref=%s", user_id, order.id, reference_id)

        # ── Notify user (in-app + WhatsApp, concurrent, best-effort) ──
        try:
            await notify_user(
                user_id=user_id,
                title="Order Confirmed",
                message=(
                    f"Order #{order.orderNumber} confirmed. "
                    f"Total: QAR {order.total:.2f}. We'll let you know when it's ready for pickup."
                ),
                notification_type="SUCCESS",
                send_via_app=True,
                send_via_whatsapp=True,
            )
        except Exception as exc:
            logger.warning("Order gateway-confirm notification failed (non-fatal): %s", exc)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

async def _release_inventory(reserved_items: list) -> None:
    for oi in reserved_items:
        try:
            product = await prisma.product.find_unique(where={"id": oi["product_id"]})
            if product:
                await prisma.product.update(
                    where={"id": oi["product_id"]},
                    data={"stockQuantity": product.stockQuantity + oi["quantity"]},
                )
        except Exception as exc:
            logger.error("Inventory release failed for product %s: %s", oi["product_id"], exc)


async def _increment_sales_count(order_items: list) -> None:
    for oi in order_items:
        try:
            product = await prisma.product.find_unique(where={"id": oi["product_id"]})
            if product:
                await prisma.product.update(
                    where={"id": oi["product_id"]},
                    data={"salesCount": product.salesCount + oi["quantity"]},
                )
        except Exception as exc:
            logger.error("salesCount update failed for product %s: %s", oi["product_id"], exc)


async def _increment_sales_count_by_items(items) -> None:
    for item in items:
        try:
            product = await prisma.product.find_unique(where={"id": item.productId})
            if product:
                await prisma.product.update(
                    where={"id": item.productId},
                    data={"salesCount": product.salesCount + item.quantity},
                )
        except Exception as exc:
            logger.error("salesCount update failed for product %s: %s", item.productId, exc)