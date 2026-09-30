"""
app/services/membership_payment_service.py
============================================
MODULE 3 — MEMBERSHIP PAYMENTS

Policy:
  - NON-REFUNDABLE after purchase
  - Membership activated only after confirmed payment
  - WALLET → immediate activation
  - GATEWAY → pending until webhook confirms

FIX (Issue #1):
  Before creating a new membership from a plan, the service now checks whether
  the requesting user already has an ACTIVE membership derived from the same
  plan (matched by name + userId). If such a record exists AND it has not yet
  expired, a 409 Conflict is raised so the user cannot buy the same plan twice.
  The check is intentionally skipped for autoRenew flows (webhook confirm path)
  so that legitimate renewals still work.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone, timedelta

from fastapi import HTTPException, status

from app.core.payment_service import PaymentService, make_membership_ref
from app.core.notification_service import notify_user
from app.db.db_client import prisma
from app.services.wallet_service import WalletService

logger = logging.getLogger(__name__)


class MembershipPaymentService:

    # ── POST /payments/memberships/buy ────────────────────────────────────────

    @staticmethod
    async def initiate_membership_purchase(
        user_id: str,
        membership_plan_id: str,
        payment_method: str,
        auto_renew: bool = False,
    ) -> dict:
        """
        Step A — Purchase a membership plan.

        membership_plan_id  : ID of a Membership record created by admin
                              (acts as a plan template)

        The system creates a brand-new Membership record for the user after payment.
        NON-REFUNDABLE.

        GUARD (Issue #1): Rejects the purchase if the user already holds an
        active, non-expired membership that was cloned from this same plan.
        """
        payment_method = payment_method.upper()
        if payment_method not in ("WALLET", "GATEWAY"):
            raise HTTPException(status_code=400, detail="payment_method must be 'WALLET' or 'GATEWAY'")

        # Load the plan template
        plan = await prisma.membership.find_unique(where={"id": membership_plan_id})
        if not plan:
            raise HTTPException(status_code=404, detail="Membership plan not found")

        user = await prisma.user.find_unique(where={"id": user_id})
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        # ── DUPLICATE GUARD (Issue #1) ────────────────────────────────────
        await _assert_no_active_membership(user_id, plan)

        amount = plan.price
        reference_id = make_membership_ref()

        # ── WALLET ────────────────────────────────────────────────────────────
        if payment_method == "WALLET":
            log = await prisma.paymentlog.create(
                data={
                    "userId": user_id,
                    "module": "MEMBERSHIP",
                    "referenceId": reference_id,
                    "idempotencyKey": reference_id,
                    "amount": amount,
                    "currency": "QAR",
                    "paymentMethod": "WALLET",
                    "status": "INITIATED",
                }
            )

            await WalletService.debit_wallet(
                user_id=user_id,
                amount=amount,
                description=f"Membership: {plan.name}",
                reference_id=reference_id,
                payment_log_id=log.id,
            )

            membership = await _activate_membership(user_id, plan, "WALLET", log.id, auto_renew)

            await prisma.paymentlog.update(
                where={"id": log.id},
                data={"status": "SUCCESS"},
            )

            logger.info("Membership activated via WALLET: user=%s membership=%s", user_id, membership.id)

            # ── Notify user (in-app + WhatsApp, concurrent, best-effort) ──
            try:
                end_label = membership.endDate.strftime("%d %b %Y") if membership.endDate else "N/A"
                await notify_user(
                    user_id=user_id,
                    title="Membership Activated",
                    message=(
                        f"Your '{membership.name}' membership is active. "
                        f"Valid until {end_label}. Amount paid: QAR {amount:.2f}."
                    ),
                    notification_type="SUCCESS",
                    send_via_app=True,
                    send_via_whatsapp=True,
                )
            except Exception as exc:
                logger.warning("Membership activation notification failed (non-fatal): %s", exc)

            return {
                "status": "ACTIVE",
                "membership_id": membership.id,
                "membership_name": membership.name,
                "valid_from": membership.startDate.isoformat(),
                "valid_until": membership.endDate.isoformat() if membership.endDate else None,
                "amount_paid": amount,
                "payment_method": "WALLET",
                "message": "Membership activated successfully. NON-REFUNDABLE.",
            }

        # ── GATEWAY ───────────────────────────────────────────────────────────
        try:
            session = await PaymentService.create_payment_session(
                amount=amount,
                customer_name=user.name,
                customer_email=user.email,
                customer_reference=reference_id,
                description=f"Membership purchase: {plan.name}",
            )
        except ValueError as exc:
            raise HTTPException(status_code=502, detail=str(exc))

        # FIX: Prisma Python client requires Json? fields to be serialized
        # as a JSON string using json.dumps().
        await prisma.paymentlog.create(
            data={
                "userId": user_id,
                "module": "MEMBERSHIP",
                "referenceId": reference_id,
                "idempotencyKey": reference_id,
                "amount": amount,
                "currency": "QAR",
                "paymentMethod": "GATEWAY",
                "status": "INITIATED",
                "gatewayInvoiceId": session["invoice_id"],
                "gatewayResponse": json.dumps({
                    "plan_id": membership_plan_id,
                    "auto_renew": auto_renew,
                }),
            }
        )

        logger.info("Membership gateway payment initiated: user=%s plan=%s ref=%s", user_id, membership_plan_id, reference_id)

        return {
            "status": "PENDING",
            "payment_url": session["payment_url"],
            "invoice_id": session["invoice_id"],
            "reference_id": reference_id,
            "plan_name": plan.name,
            "amount": amount,
            "currency": "QAR",
            "message": "Redirect user to payment_url. Membership will activate automatically after payment. NON-REFUNDABLE.",
        }

    # ── Internal: called by WebhookService ───────────────────────────────────

    @staticmethod
    async def confirm_gateway_membership(
        user_id: str,
        reference_id: str,
        invoice_id: str,
        amount: float,
    ) -> None:
        """
        Activate membership after gateway payment confirmation.
        Idempotent — safe to call from callback and webhook.

        NOTE: Duplicate guard is intentionally NOT applied here because:
          - This path is reached only after a real payment was collected.
          - The idempotency check (log.status == "SUCCESS") already prevents
            double-activation on repeated webhook deliveries.
        """
        log = await prisma.paymentlog.find_first(
            where={"referenceId": reference_id, "module": "MEMBERSHIP"}
        )
        if not log:
            logger.error("Membership payment log not found: ref=%s", reference_id)
            return

        if log.status == "SUCCESS":
            logger.info("Membership already activated for ref=%s — skipping", reference_id)
            return

        # Retrieve plan info from gatewayResponse
        raw_meta = log.gatewayResponse or {}
        if isinstance(raw_meta, str):
            try:
                gateway_meta = json.loads(raw_meta)
            except (json.JSONDecodeError, ValueError):
                gateway_meta = {}
        else:
            gateway_meta = raw_meta

        plan_id = gateway_meta.get("plan_id") if isinstance(gateway_meta, dict) else None
        auto_renew = gateway_meta.get("auto_renew", False) if isinstance(gateway_meta, dict) else False

        if not plan_id:
            logger.error("No plan_id in gateway metadata for ref=%s", reference_id)
            return

        plan = await prisma.membership.find_unique(where={"id": plan_id})
        if not plan:
            logger.error("Membership plan not found: %s", plan_id)
            return

        membership = await _activate_membership(user_id, plan, "GATEWAY", log.id, auto_renew)

        await prisma.paymentlog.update(
            where={"id": log.id},
            data={"status": "SUCCESS", "gatewayPaymentId": invoice_id},
        )

        logger.info("Membership gateway activated: user=%s membership=%s ref=%s", user_id, membership.id, reference_id)

        # ── Notify user (in-app + WhatsApp, concurrent, best-effort) ──
        try:
            end_label = membership.endDate.strftime("%d %b %Y") if membership.endDate else "N/A"
            await notify_user(
                user_id=user_id,
                title="Membership Activated",
                message=(
                    f"Your '{membership.name}' membership is active. "
                    f"Valid until {end_label}. Amount paid: QAR {amount:.2f}."
                ),
                notification_type="SUCCESS",
                send_via_app=True,
                send_via_whatsapp=True,
            )
        except Exception as exc:
            logger.warning("Membership gateway activation notification failed (non-fatal): %s", exc)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

async def _assert_no_active_membership(user_id: str, plan) -> None:
    """
    Raise HTTP 409 if the user already holds a valid (non-expired) membership
    that originated from the given plan (matched by plan.name).

    This prevents Issue #1: users buying the same Membership plan repeatedly
    while the current one is still active.

    Design decision — match on plan name rather than a foreign-key planId:
      The schema stores user memberships and plan templates in the same
      Membership table (no separate 'Plan' model). The most reliable way to
      link a user's purchased copy back to its template is therefore the
      plan's unique name. If two plans share a name that is a schema-level
      problem, not a payment-service problem.
    """
    now = datetime.now(timezone.utc)

    existing = await prisma.membership.find_first(
        where={
            "userId": user_id,
            "name":   plan.name,
            "status": "ACTIVE",
            "endDate": {"gt": now},
        }
    )

    if existing:
        end_label = existing.endDate.strftime("%d %b %Y") if existing.endDate else "unknown date"
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"You already have an active '{plan.name}' membership that is valid "
                f"until {end_label}. You cannot purchase the same plan while it is "
                "still active."
            ),
        )


async def _activate_membership(user_id, plan, payment_method, log_id, auto_renew) -> object:
    """Create a new active Membership record for the user from a plan template."""
    now = datetime.now(timezone.utc)
    end_date = now + timedelta(days=plan.durationDays)

    membership = await prisma.membership.create(
        data={
            "userId":          user_id,
            "name":            plan.name,
            "description":     plan.description,
            "price":           plan.price,
            "durationDays":    plan.durationDays,
            "allowedClasses":  plan.allowedClasses,
            "allowedCourses":  plan.allowedCourses,
            "timeRestriction": plan.timeRestriction,
            "status":          "ACTIVE",
            "progress":        0.0,
            "startDate":       now,
            "endDate":         end_date,
            "enrolledAt":      now,
            "autoRenew":       auto_renew,
            "paymentMethod":   payment_method,
            "paymentLogId":    log_id,
            "isPaid":          True,
        }
    )
    return membership