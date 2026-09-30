"""
app/services/package_payment_service.py
==========================================
MODULE 4 — PACKAGE PAYMENTS

Policy:
  - NON-REFUNDABLE after purchase
  - Package (creates a Membership) activated only after confirmed payment
  - WALLET → immediate activation
  - GATEWAY → pending until webhook confirms

FIX (Issue #1):
  Before creating a membership from a package template, the service now checks
  whether the requesting user already has an ACTIVE, non-expired membership
  created from the same package (matched by package.name). If so, a 409
  Conflict is raised preventing a repeat purchase.
  The guard is intentionally skipped for the webhook confirm path because money
  has already been collected, and the idempotency check (log.status == "SUCCESS")
  is sufficient there.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone, timedelta

from fastapi import HTTPException, status

from app.core.payment_service import PaymentService, make_package_ref
from app.core.notification_service import notify_user
from app.db.db_client import prisma
from app.services.wallet_service import WalletService

logger = logging.getLogger(__name__)


class PackagePaymentService:

    # ── POST /payments/packages/buy ───────────────────────────────────────────

    @staticmethod
    async def initiate_package_purchase(
        user_id: str,
        package_id: str,
        payment_method: str,
        auto_renew: bool = False,
    ) -> dict:
        """
        Step A — Purchase a package.

        On payment confirmation: creates a Membership from the package template.
        NON-REFUNDABLE.

        GUARD (Issue #1): Rejects the purchase if the user already holds an
        active, non-expired membership derived from this same package.
        """
        payment_method = payment_method.upper()
        if payment_method not in ("WALLET", "GATEWAY"):
            raise HTTPException(status_code=400, detail="payment_method must be 'WALLET' or 'GATEWAY'")

        package = await prisma.package.find_unique(where={"id": package_id})
        if not package:
            raise HTTPException(status_code=404, detail="Package not found")
        if not package.isActive:
            raise HTTPException(status_code=400, detail="Package is not available for purchase")

        user = await prisma.user.find_unique(where={"id": user_id})
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        # ── DUPLICATE GUARD (Issue #1) ────────────────────────────────────
        await _assert_no_active_package_membership(user_id, package)

        # Apply discount price if set
        amount = package.discountPrice if package.discountPrice else package.price
        reference_id = make_package_ref()

        # ── WALLET ────────────────────────────────────────────────────────────
        if payment_method == "WALLET":
            log = await prisma.paymentlog.create(
                data={
                    "userId": user_id,
                    "module": "PACKAGE",
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
                description=f"Package purchase: {package.name}",
                reference_id=reference_id,
                payment_log_id=log.id,
            )

            membership = await _activate_package_membership(user_id, package, "WALLET", log.id, auto_renew)

            await prisma.paymentlog.update(
                where={"id": log.id},
                data={"status": "SUCCESS"},
            )

            logger.info("Package activated via WALLET: user=%s package=%s membership=%s", user_id, package_id, membership.id)

            # ── Notify user (in-app + WhatsApp, concurrent, best-effort) ──
            try:
                end_label = membership.endDate.strftime("%d %b %Y") if membership.endDate else "N/A"
                await notify_user(
                    user_id=user_id,
                    title="Package Activated",
                    message=(
                        f"Your '{package.name}' package is active. "
                        f"Valid until {end_label}. Amount paid: QAR {amount:.2f}."
                    ),
                    notification_type="SUCCESS",
                    send_via_app=True,
                    send_via_whatsapp=True,
                )
            except Exception as exc:
                logger.warning("Package activation notification failed (non-fatal): %s", exc)

            return {
                "status": "ACTIVE",
                "membership_id": membership.id,
                "package_name": package.name,
                "valid_from": membership.startDate.isoformat(),
                "valid_until": membership.endDate.isoformat() if membership.endDate else None,
                "amount_paid": amount,
                "payment_method": "WALLET",
                "message": "Package activated successfully. NON-REFUNDABLE.",
            }

        # ── GATEWAY ───────────────────────────────────────────────────────────
        try:
            session = await PaymentService.create_payment_session(
                amount=amount,
                customer_name=user.name,
                customer_email=user.email,
                customer_reference=reference_id,
                description=f"Package purchase: {package.name}",
            )
        except ValueError as exc:
            raise HTTPException(status_code=502, detail=str(exc))

        # FIX: Prisma Python client requires Json? fields to be serialized
        # as a JSON string using json.dumps().
        await prisma.paymentlog.create(
            data={
                "userId": user_id,
                "module": "PACKAGE",
                "referenceId": reference_id,
                "idempotencyKey": reference_id,
                "amount": amount,
                "currency": "QAR",
                "paymentMethod": "GATEWAY",
                "status": "INITIATED",
                "gatewayInvoiceId": session["invoice_id"],
                "gatewayResponse": json.dumps({
                    "package_id": package_id,
                    "auto_renew": auto_renew,
                }),
            }
        )

        logger.info("Package gateway payment initiated: user=%s package=%s ref=%s", user_id, package_id, reference_id)

        return {
            "status": "PENDING",
            "payment_url": session["payment_url"],
            "invoice_id": session["invoice_id"],
            "reference_id": reference_id,
            "package_name": package.name,
            "amount": amount,
            "currency": "QAR",
            "message": "Redirect user to payment_url. Package will activate automatically after payment. NON-REFUNDABLE.",
        }

    # ── Internal: called by WebhookService ───────────────────────────────────

    @staticmethod
    async def confirm_gateway_package(
        user_id: str,
        reference_id: str,
        invoice_id: str,
        amount: float,
    ) -> None:
        """
        Activate package after gateway payment confirmation. Idempotent.

        NOTE: Duplicate guard is intentionally NOT applied here because:
          - Money has already been collected; refusing activation would
            be worse than allowing a rare edge-case duplicate.
          - The idempotency check (log.status == "SUCCESS") already prevents
            double-activation on repeated webhook deliveries.
        """
        log = await prisma.paymentlog.find_first(
            where={"referenceId": reference_id, "module": "PACKAGE"}
        )
        if not log:
            logger.error("Package payment log not found: ref=%s", reference_id)
            return

        if log.status == "SUCCESS":
            logger.info("Package already activated for ref=%s — skipping", reference_id)
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

        package_id = gateway_meta.get("package_id") if isinstance(gateway_meta, dict) else None
        auto_renew = gateway_meta.get("auto_renew", False) if isinstance(gateway_meta, dict) else False

        if not package_id:
            logger.error("No package_id in gateway metadata for ref=%s", reference_id)
            return

        package = await prisma.package.find_unique(where={"id": package_id})
        if not package:
            logger.error("Package not found: %s", package_id)
            return

        membership = await _activate_package_membership(user_id, package, "GATEWAY", log.id, auto_renew)

        await prisma.paymentlog.update(
            where={"id": log.id},
            data={"status": "SUCCESS", "gatewayPaymentId": invoice_id},
        )

        logger.info("Package gateway activated: user=%s membership=%s ref=%s", user_id, membership.id, reference_id)

        # ── Notify user (in-app + WhatsApp, concurrent, best-effort) ──
        try:
            end_label = membership.endDate.strftime("%d %b %Y") if membership.endDate else "N/A"
            await notify_user(
                user_id=user_id,
                title="Package Activated",
                message=(
                    f"Your '{package.name}' package is active. "
                    f"Valid until {end_label}. Amount paid: QAR {amount:.2f}."
                ),
                notification_type="SUCCESS",
                send_via_app=True,
                send_via_whatsapp=True,
            )
        except Exception as exc:
            logger.warning("Package gateway activation notification failed (non-fatal): %s", exc)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

async def _assert_no_active_package_membership(user_id: str, package) -> None:
    """
    Raise HTTP 409 if the user already holds a valid (non-expired) activation
    record that was created from a prior purchase of this exact package.

    DESIGN: Packages and Memberships are fully independent domain objects.
    A user who has an active *Membership* (from the membership catalogue)
    with the same display name as a Package is NOT blocked — those are
    different products in different domains.

    We scope the check strictly to package-backed activation records by:
      1. Finding successful PaymentLog rows for this user with module=PACKAGE.
      2. Checking whether any of those logs produced an active (non-expired)
         activation record (a Membership row) for the same package name,
         linked via paymentLogId.

    This ensures name collisions between the Package catalogue and the
    Membership catalogue cannot produce false-positive 409 responses.
    """
    now = datetime.now(timezone.utc)

    # Step 1 — find all successful package PaymentLogs for this user
    package_logs = await prisma.paymentlog.find_many(
        where={
            "userId": user_id,
            "module": "PACKAGE",
            "status": "SUCCESS",
        }
    )

    if not package_logs:
        return  # No prior successful package purchases — allow

    log_ids = [log.id for log in package_logs]

    # Step 2 — check for an active activation record linked to one of those logs
    existing = await prisma.membership.find_first(
        where={
            "userId":       user_id,
            "name":         package.name,
            "status":       "ACTIVE",
            "endDate":      {"gt": now},
            "paymentLogId": {"in": log_ids},
        }
    )

    if existing:
        end_label = existing.endDate.strftime("%d %b %Y") if existing.endDate else "unknown date"
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"You already have an active '{package.name}' package that is valid "
                f"until {end_label}. You cannot purchase the same package while it is "
                "still active."
            ),
        )


async def _activate_package_membership(user_id, package, payment_method, log_id, auto_renew) -> object:
    """
    Create a Membership record from a Package template.

    The package's timeRestriction field (which encodes numberOfSessions) is
    copied verbatim into the Membership row so that session tracking can be
    performed on the user-specific instance without needing the Package template.

    Session tracking uses Membership.progress:
    - progress = 0.0 at activation (0 sessions used)
    - Incremented by 1 each time the user books a class via this package
    """
    now = datetime.now(timezone.utc)
    end_date = now + timedelta(days=package.durationDays)

    membership = await prisma.membership.create(
        data={
            "userId":          user_id,
            "name":            package.name,
            "description":     package.description,
            "price":           package.discountPrice or package.price,
            "durationDays":    package.durationDays,
            "allowedClasses":  package.allowedClasses,
            "allowedCourses":  package.allowedCourses,
            # Copy the encoded timeRestriction (contains numberOfSessions if set)
            # so the user instance retains the session limit even if the package
            # template is later modified or deleted.
            "timeRestriction": package.timeRestriction,
            "status":          "ACTIVE",
            "progress":        0.0,  # sessions used = 0 at activation
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