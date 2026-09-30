"""
app/services/booking_payment_service.py
=========================================
MODULE 2 — BOOKING PAYMENTS

Handles both CLASS and COURSE bookings polymorphically.
The Booking model supports both via optional classId / courseId FKs
(only one is set per row; the other is NULL).

────────────────────────────────────────────────────────────────────────────
CLASS bookings — payment flow
────────────────────────────────────────────────────────────────────────────
  WALLET  → deduct immediately → confirm booking
  GATEWAY → initiate MyFatoorah session → booking confirmed via webhook
  PACKAGE → spend one session from a caller-CHOSEN owned package instance →
            confirm booking at 0 QAR (requires membership_id)

Membership coverage (a plan purchased from the Membership catalogue, NOT a
Package) still auto-confirms at 0 QAR with no explicit choice required — this
mirrors the "Membership" price-tag already shown in the class list.

Package coverage is intentionally NOT auto-applied. A user may hold a package
that covers this class yet prefer to keep that session for a different class
and pay this one with Wallet/Gateway instead. So when one or more of the
caller's package instances cover the class, they are surfaced on the response
as `availablePackages` and the caller must explicitly send
`payment_method="PACKAGE"` + `membership_id` to spend a session. WALLET and
GATEWAY remain available at the class's full original price regardless.

────────────────────────────────────────────────────────────────────────────
COURSE bookings — payment flow
────────────────────────────────────────────────────────────────────────────
Courses are a fully independent domain. They carry NO relationship to
Packages or Memberships whatsoever — every course booking (free courses
excepted) MUST pass through the standard payment system (WALLET or GATEWAY).
There is no 0-QAR coverage path for courses, ever.

────────────────────────────────────────────────────────────────────────────
Cancellation policy (per design)
────────────────────────────────────────────────────────────────────────────
  < 3 hours before start  → NO refund
  ≥ 3 hours before start  → full refund:
      - Paid in QAR (Wallet/Gateway)  → full amount refunded to WALLET
        (never back to the original card)
      - Paid via PACKAGE              → the spent session is restored to the
        same package instance (there is no QAR to refund)
      - Paid via MEMBERSHIP coverage  → nothing to do, the price was 0 QAR
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import HTTPException, status

from app.core.payment_service import PaymentService, make_booking_ref
from app.core.notification_service import notify_user
from app.db.db_client import prisma
from app.services.wallet_service import WalletService

logger = logging.getLogger(__name__)

CANCELLATION_WINDOW_HOURS = 3  # Refund only if cancelled >= 3h before start


class BookingPaymentService:

    # ── POST /payments/bookings/pay ───────────────────────────────────────────

    @staticmethod
    async def initiate_booking_payment(
        user_id: str,
        class_id: str,
        payment_method: str,
        membership_id: Optional[str] = None,
    ) -> dict:
        """
        Step A — Book a CLASS and pay.

        MEMBERSHIP COVERAGE (auto-applies):
        If the user holds an active membership that covers this class, the
        effective price is 0 QAR and the class is booked directly — no
        payment method needed, exactly like a free class.

        PACKAGE COVERAGE (caller must opt in):
        Owning a covering package does NOT change the price shown, and does
        NOT auto-book. If one or more owned package instances cover this
        class, they are listed under `available_packages` on every response
        from this method so the frontend can offer them as a payment choice
        alongside Wallet/Gateway. To actually spend a session, the caller
        must send payment_method="PACKAGE" together with membership_id (the
        id of the chosen package instance, taken from `available_packages`).

        WALLET  → immediate confirmation at the class's full price.
        GATEWAY → returns payment_url; booking confirmed by webhook.
        PACKAGE → confirms immediately at 0 QAR, spends one session from the
                  chosen package instance. Requires membership_id.

        0 QAR UNIVERSAL RULE:
        If the class is free or membership-covered, the booking is confirmed
        directly without routing to any payment method.
        """
        # Import here to avoid circular imports (PackageService ↔ BookingPaymentService)
        from app.services.package_service import PackageService

        payment_method = payment_method.upper()
        if payment_method not in ("WALLET", "GATEWAY", "PACKAGE"):
            raise HTTPException(
                status_code=400,
                detail="payment_method must be 'WALLET', 'GATEWAY', or 'PACKAGE'",
            )
        if payment_method == "PACKAGE" and not membership_id:
            raise HTTPException(
                status_code=400,
                detail="membership_id is required when payment_method='PACKAGE'",
            )

        # Fetch class
        cls = await prisma.classes.find_unique(where={"id": class_id})
        if not cls:
            raise HTTPException(status_code=404, detail="Class not found")
        if cls.status != "SCHEDULED":
            raise HTTPException(status_code=400, detail="Class is not available for booking")

        # Seat availability
        if cls.availableSeat is not None and cls.availableSeat <= 0:
            raise HTTPException(status_code=400, detail="No available seats in this class")

        # Duplicate booking guard
        existing = await prisma.booking.find_first(
            where={"userId": user_id, "classId": class_id}
        )
        if existing and existing.status not in ("CANCELLED",):
            raise HTTPException(status_code=409, detail="You already have an active booking for this class")

        user = await prisma.user.find_unique(where={"id": user_id})
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        # ── DETERMINE EFFECTIVE PRICE ─────────────────────────────────────────
        # NOTE: package coverage no longer zeroes this — only free / membership
        # coverage does. Package instances are listed separately below.
        base_amount = cls.price if not cls.isFree else 0.0
        effective_amount, is_covered, coverage_type = await PackageService.get_effective_class_price(
            user_id=user_id,
            class_id=class_id,
            original_price=base_amount,
        )

        # Always resolve available package options — surfaced on every branch
        # below so a paid-via-WALLET/GATEWAY response still tells the caller
        # "you could have used Package XXX instead" for next time.
        available_packages = []
        if base_amount > 0:
            available_packages = await PackageService.get_covering_packages_for_class(
                user_id=user_id, class_id=class_id,
            )

        reference_id = make_booking_ref()

        # ── PACKAGE PAYMENT (explicit caller choice — spends one session) ────
        if payment_method == "PACKAGE":
            if base_amount <= 0:
                raise HTTPException(
                    status_code=400,
                    detail="This class is already free or membership-covered — no package needed.",
                )
            chosen = next(
                (p for p in available_packages if p["membershipId"] == membership_id),
                None,
            )
            if not chosen:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "The selected package does not cover this class, has no sessions "
                        "remaining, or is no longer active."
                    ),
                )

            log = await prisma.paymentlog.create(
                data={
                    "userId": user_id,
                    "module": "BOOKING",
                    "referenceId": reference_id,
                    "idempotencyKey": reference_id,
                    "amount": 0.0,
                    "currency": "QAR",
                    "paymentMethod": "WALLET",
                    "status": "INITIATED",
                    # Record which package instance funded this booking so
                    # cancel_booking can restore the session later. Json? field
                    # requires json.dumps() before persisting via prisma-client-py.
                    "gatewayResponse": json.dumps({"fundedByMembershipId": membership_id}),
                }
            )

            booking = await _confirm_booking(user_id, class_id, 0.0, "WALLET", log.id)

            await prisma.paymentlog.update(
                where={"id": log.id},
                data={"status": "SUCCESS", "referenceId": reference_id},
            )

            try:
                await PackageService.decrement_session_for_user(user_id, class_id)
            except Exception as exc:
                logger.warning(
                    "Session decrement failed (non-fatal): user=%s class=%s err=%s",
                    user_id, class_id, exc
                )

            coverage_msg = f"Class paid using your '{chosen['packageName']}' package — one session spent."

            try:
                await notify_user(
                    user_id=user_id,
                    title="Booking Confirmed",
                    message=f"Your booking for '{cls.title}' is confirmed. {coverage_msg}",
                    notification_type="SUCCESS",
                    send_via_app=True,
                    send_via_whatsapp=True,
                )
            except Exception as exc:
                logger.warning("Booking confirmation notification failed (non-fatal): %s", exc)

            return {
                "status": "CONFIRMED",
                "booking_id": booking.id,
                "class_id": class_id,
                "class_title": cls.title,
                "amount_paid": 0.0,
                "payment_method": "PACKAGE",
                "covered_by": "package",
                "used_package_membership_id": membership_id,
                "available_packages": available_packages,
                "message": f"Booking confirmed. {coverage_msg}",
            }

        # ── ZERO PRICE PATH (free class, or auto-applied MEMBERSHIP coverage) ─
        # Package coverage never lands here any more — base_amount stays > 0
        # for it, so it falls through to WALLET/GATEWAY below where it is
        # surfaced via available_packages instead of silently consuming a
        # session on the caller's behalf.
        if effective_amount == 0.0 or cls.isFree or (is_covered and coverage_type == "membership"):
            log = await prisma.paymentlog.create(
                data={
                    "userId": user_id,
                    "module": "BOOKING",
                    "referenceId": reference_id,
                    "idempotencyKey": reference_id,
                    "amount": 0.0,
                    "currency": "QAR",
                    "paymentMethod": "WALLET",
                    "status": "INITIATED",
                }
            )

            # Confirm booking immediately (0 QAR — no wallet deduction)
            booking = await _confirm_booking(user_id, class_id, 0.0, "WALLET", log.id)

            # Mark log SUCCESS
            await prisma.paymentlog.update(
                where={"id": log.id},
                data={"status": "SUCCESS", "referenceId": reference_id},
            )

            # Coverage message for response
            if coverage_type == "membership":
                coverage_msg = "Class covered by your active membership — no payment required."
            else:
                coverage_msg = "Class is free — no payment required."

            # ── Notify user ──
            try:
                await notify_user(
                    user_id=user_id,
                    title="Booking Confirmed",
                    message=(
                        f"Your booking for '{cls.title}' is confirmed. "
                        f"{coverage_msg}"
                    ),
                    notification_type="SUCCESS",
                    send_via_app=True,
                    send_via_whatsapp=True,
                )
            except Exception as exc:
                logger.warning("Booking confirmation notification failed (non-fatal): %s", exc)

            return {
                "status": "CONFIRMED",
                "booking_id": booking.id,
                "class_id": class_id,
                "class_title": cls.title,
                "amount_paid": 0.0,
                "payment_method": "WALLET",
                "covered_by": coverage_type,
                "available_packages": [],
                "message": f"Booking confirmed. {coverage_msg}",
            }

        # ── WALLET payment (paid class, not covered) ──────────────────────────
        if payment_method == "WALLET":
            log = await prisma.paymentlog.create(
                data={
                    "userId": user_id,
                    "module": "BOOKING",
                    "referenceId": reference_id,
                    "idempotencyKey": reference_id,
                    "amount": effective_amount,
                    "currency": "QAR",
                    "paymentMethod": "WALLET",
                    "status": "INITIATED",
                }
            )

            await WalletService.debit_wallet(
                user_id=user_id,
                amount=effective_amount,
                description=f"Class booking: {cls.title}",
                reference_id=reference_id,
                payment_log_id=log.id,
            )

            # Confirm booking
            booking = await _confirm_booking(user_id, class_id, effective_amount, "WALLET", log.id)

            # Mark log SUCCESS
            await prisma.paymentlog.update(
                where={"id": log.id},
                data={"status": "SUCCESS", "referenceId": reference_id},
            )

            try:
                await notify_user(
                    user_id=user_id,
                    title="Booking Confirmed",
                    message=(
                        f"Your booking for '{cls.title}' is confirmed. "
                        f"Amount paid: QAR {effective_amount:.2f}."
                    ),
                    notification_type="SUCCESS",
                    send_via_app=True,
                    send_via_whatsapp=True,
                )
            except Exception as exc:
                logger.warning("Booking confirmation notification failed (non-fatal): %s", exc)

            return {
                "status": "CONFIRMED",
                "booking_id": booking.id,
                "class_id": class_id,
                "class_title": cls.title,
                "amount_paid": effective_amount,
                "payment_method": "WALLET",
                "covered_by": "none",
                "available_packages": available_packages,
                "message": "Booking confirmed. Enjoy your class!",
            }

        # ── GATEWAY payment ───────────────────────────────────────────────────
        try:
            session = await PaymentService.create_payment_session(
                amount=effective_amount,
                customer_name=user.name,
                customer_email=user.email,
                customer_reference=reference_id,
                description=f"Class booking: {cls.title}",
            )
        except ValueError as exc:
            raise HTTPException(status_code=502, detail=str(exc))

        # Reserve seat optimistically
        if cls.availableSeat is not None:
            await prisma.classes.update(
                where={"id": class_id},
                data={"availableSeat": cls.availableSeat - 1},
            )

        # Create PENDING booking
        booking = await prisma.booking.create(
            data={
                "userId": user_id,
                "classId": class_id,
                "status": "PENDING",
                "paymentMethod": "GATEWAY",
                "amountPaid": effective_amount,
            }
        )

        log = await prisma.paymentlog.create(
            data={
                "userId": user_id,
                "module": "BOOKING",
                "referenceId": reference_id,
                "idempotencyKey": reference_id,
                "amount": effective_amount,
                "currency": "QAR",
                "paymentMethod": "GATEWAY",
                "status": "INITIATED",
                "gatewayInvoiceId": session["invoice_id"],
            }
        )

        logger.info("Booking gateway payment initiated: user=%s class=%s ref=%s", user_id, class_id, reference_id)

        return {
            "status": "PENDING",
            "booking_id": booking.id,
            "payment_url": session["payment_url"],
            "invoice_id": session["invoice_id"],
            "reference_id": reference_id,
            "amount": effective_amount,
            "currency": "QAR",
            "covered_by": "none",
            "available_packages": available_packages,
            "message": "Redirect user to payment_url to complete payment. Booking will be confirmed automatically.",
        }

    # ── POST /payments/bookings/cancel ────────────────────────────────────────

    @staticmethod
    async def cancel_booking(user_id: str, booking_id: str, reason: Optional[str] = None) -> dict:
        """
        Cancel a booking (class or course) with refund policy enforcement.

        The 3-hour window is the ONLY thing that decides eligibility — it is
        measured purely against how far in advance of the class/course start
        time the cancellation happens, nothing else:

          < 3h before start  → NO refund, regardless of how it was paid.
          ≥ 3h before start  → the booking is made whole again, using
                                whichever of these three outcomes matches how
                                it was originally paid for:

            1. Paid in QAR (Wallet or Gateway) → full amount refunded to the
               user's WALLET (never back to the original card/gateway as
               cash — wallet credit is the platform's standard refund path).
            2. Paid by spending a PACKAGE session → there is no QAR to
               refund; instead the spent session is restored to that exact
               package instance so the user can use it again.
            3. Covered by a MEMBERSHIP (auto 0 QAR) → nothing to restore,
               the price was always 0.
        """
        booking = await prisma.booking.find_unique(where={"id": booking_id})
        if not booking:
            raise HTTPException(status_code=404, detail="Booking not found")
        if booking.userId != user_id:
            raise HTTPException(status_code=403, detail="You can only cancel your own bookings")
        if booking.status in ("CANCELLED",):
            raise HTTPException(status_code=400, detail="Booking is already cancelled")

        # Resolve the booked entity (class or course) for timing + seat logic
        title = "your session"
        scheduled_at = None

        if booking.classId:
            cls = await prisma.classes.find_unique(where={"id": booking.classId})
            if not cls:
                raise HTTPException(status_code=404, detail="Class not found")
            title = cls.title
            scheduled_at = cls.scheduledAt
        elif booking.courseId:
            course = await prisma.course.find_unique(where={"id": booking.courseId})
            if not course:
                raise HTTPException(status_code=404, detail="Course not found")
            title = course.title
            scheduled_at = course.scheduledAt
        else:
            raise HTTPException(status_code=400, detail="Booking has no associated class or course")

        now = datetime.now(timezone.utc)
        start_time = scheduled_at
        if start_time.tzinfo is None:
            start_time = start_time.replace(tzinfo=timezone.utc)

        hours_until_start = (start_time - now).total_seconds() / 3600

        # Apply cancellation policy — purely a function of time-until-start.
        refund_eligible = hours_until_start >= CANCELLATION_WINDOW_HOURS
        refund_amount = booking.amountPaid if refund_eligible and booking.amountPaid else 0.0

        # ── Was this booking funded by a package session? ──────────────────
        # A booking is package-funded when it was confirmed at 0 QAR via the
        # explicit PACKAGE payment method (see initiate_booking_payment). The
        # funding instance's id was recorded on the PaymentLog at booking time
        # since the Booking row itself has no dedicated column for it.
        funded_membership_id: Optional[str] = None
        if booking.classId and booking.paymentLogId:
            funding_log = await prisma.paymentlog.find_unique(where={"id": booking.paymentLogId})
            if funding_log and funding_log.gatewayResponse:
                raw_meta = funding_log.gatewayResponse
                if isinstance(raw_meta, str):
                    try:
                        raw_meta = json.loads(raw_meta)
                    except (json.JSONDecodeError, ValueError):
                        raw_meta = {}
                if isinstance(raw_meta, dict):
                    funded_membership_id = raw_meta.get("fundedByMembershipId")

        # Cancel the booking
        await prisma.booking.update(
            where={"id": booking_id},
            data={
                "status": "CANCELLED",
                "cancelledAt": now,
                "notes": reason,
            },
        )

        # Release seat on the appropriate entity
        if booking.classId:
            cls = await prisma.classes.find_unique(where={"id": booking.classId})
            if cls and cls.availableSeat is not None:
                await prisma.classes.update(
                    where={"id": cls.id},
                    data={"availableSeat": cls.availableSeat + 1},
                )
        elif booking.courseId:
            course = await prisma.course.find_unique(where={"id": booking.courseId})
            if course and course.availableSeat is not None:
                await prisma.course.update(
                    where={"id": course.id},
                    data={"availableSeat": course.availableSeat + 1},
                )

        # Default outcome for the only case that's settled immediately — every
        # other case is decided definitively by the branches further below,
        # so no placeholder value is needed for them here.
        session_restored = False
        if not refund_eligible:
            refund_message = (
                f"No refund applies — '{title}' was cancelled within "
                f"{CANCELLATION_WINDOW_HOURS} hours of its start time."
            )
        else:
            refund_message = ""  # set definitively by one of the branches below

        if refund_eligible and funded_membership_id:
            # ── PACKAGE-funded booking: restore the session, not QAR ──────────
            try:
                restored = await _restore_package_session(funded_membership_id)
            except Exception as exc:
                logger.error(
                    "Package session restoration failed for booking=%s membership=%s err=%s",
                    booking_id, funded_membership_id, exc,
                )
                restored = False

            if restored:
                session_restored = True
                refund_message = f"Your session for '{title}' has been returned to your package."
            else:
                refund_message = (
                    f"'{title}' was cancelled, but we couldn't automatically restore your "
                    f"package session — please contact support."
                )

        elif refund_eligible and refund_amount > 0:
            # ── Paid in real QAR — refund to wallet (or gateway as fallback) ──
            original_method = booking.paymentMethod or "WALLET"

            if original_method == "WALLET":
                await WalletService.refund_to_wallet(
                    user_id=user_id,
                    amount=refund_amount,
                    description=f"Refund: cancelled '{title}'",
                    reference_id=booking_id,
                )
                refund_message = f"QAR {refund_amount:.2f} refunded to your wallet."

            else:
                # GATEWAY refund — look up PaymentLog for gatewayPaymentId
                log = await prisma.paymentlog.find_first(
                    where={"module": "BOOKING", "userId": user_id},
                    order={"createdAt": "desc"},
                )
                gateway_payment_id = log.gatewayPaymentId if log else None

                if gateway_payment_id:
                    try:
                        await PaymentService.make_refund(
                            payment_id=gateway_payment_id,
                            amount=refund_amount,
                            reason="Booking cancellation",
                        )
                        refund_message = f"QAR {refund_amount:.2f} refund initiated to your original payment method."
                    except ValueError as exc:
                        logger.error("Gateway refund failed for booking %s: %s", booking_id, exc)
                        # Fallback to wallet credit
                        await WalletService.refund_to_wallet(
                            user_id=user_id,
                            amount=refund_amount,
                            description=f"Refund (gateway fallback): {title}",
                            reference_id=booking_id,
                        )
                        refund_message = f"QAR {refund_amount:.2f} refunded to your wallet (gateway refund unavailable)."
                else:
                    await WalletService.refund_to_wallet(
                        user_id=user_id,
                        amount=refund_amount,
                        description=f"Refund: {title}",
                        reference_id=booking_id,
                    )
                    refund_message = f"QAR {refund_amount:.2f} refunded to your wallet."

        elif refund_eligible and refund_amount == 0 and not funded_membership_id:
            # Eligible by timing, but there was nothing paid in QAR and no
            # package session to restore (e.g. membership-covered, or a
            # genuinely free class) — correctly nothing further to do.
            refund_message = f"'{title}' was cancelled. No payment was made for this booking, so there is nothing to refund."

        logger.info(
            "Booking cancelled: user=%s booking=%s refund_eligible=%s amount=%.2f session_restored=%s",
            user_id, booking_id, refund_eligible, refund_amount, session_restored,
        )

        # ── Notify user (in-app + WhatsApp, concurrent, best-effort) ──
        try:
            await notify_user(
                user_id=user_id,
                title="Booking Cancelled",
                message=(
                    f"Your booking for '{title}' has been cancelled. {refund_message}"
                ),
                notification_type="WARNING",
                send_via_app=True,
                send_via_whatsapp=True,
            )
        except Exception as exc:
            logger.warning("Booking cancellation notification failed (non-fatal): %s", exc)

        return {
            "booking_id": booking_id,
            "status": "CANCELLED",
            "refund_eligible": refund_eligible,
            "refund_amount": refund_amount,
            "session_restored": session_restored,
            "refund_message": refund_message,
            "cancellation_rule": (
                f"Cancellations made within {CANCELLATION_WINDOW_HOURS} hours of the start time "
                "are non-refundable. Cancellations made earlier receive a full refund to your "
                "wallet, or — if paid using a package — the session is returned to that package."
            ),
        }

    # ── POST /payments/courses/pay ────────────────────────────────────────────

    @staticmethod
    async def initiate_course_booking_payment(
        user_id: str,
        course_id: str,
        payment_method: str,
    ) -> dict:
        """
        Step A — Book a COURSE and pay.

        COURSES ARE FULLY INDEPENDENT FROM PACKAGES AND MEMBERSHIPS.
        Course booking policy is entirely separate from class booking policy.
        There is NO package or membership coverage path for courses — every
        course booking (truly free courses excepted) MUST go through the
        standard payment system. A user can never "spend a package session"
        or "use a membership" to skip paying for a course.

        WALLET  → immediate confirmation.
        GATEWAY → returns payment_url; booking confirmed by webhook.

        FREE COURSE RULE:
        If the course itself is marked free (course.isFree or price == 0),
        the booking is confirmed directly without routing to any payment
        method — this is a property of the course, not of any package or
        membership the user might own.
        """
        payment_method = payment_method.upper()
        if payment_method not in ("WALLET", "GATEWAY"):
            raise HTTPException(status_code=400, detail="payment_method must be 'WALLET' or 'GATEWAY'")

        # Fetch course
        course = await prisma.course.find_unique(where={"id": course_id})
        if not course:
            raise HTTPException(status_code=404, detail="Course not found")
        if course.status != "SCHEDULED":
            raise HTTPException(status_code=400, detail="Course is not available for booking")

        # Seat availability
        if course.availableSeat is not None and course.availableSeat <= 0:
            raise HTTPException(status_code=400, detail="No available seats in this course")

        # Duplicate booking guard
        existing = await prisma.booking.find_first(
            where={"userId": user_id, "courseId": course_id}
        )
        if existing and existing.status not in ("CANCELLED",):
            raise HTTPException(status_code=409, detail="You already have an active booking for this course")

        user = await prisma.user.find_unique(where={"id": user_id})
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        amount = course.price if not course.isFree else 0.0
        reference_id = make_booking_ref()

        # ── WALLET payment (also handles the truly-free-course path) ─────────
        if payment_method == "WALLET" or course.isFree:
            log = await prisma.paymentlog.create(
                data={
                    "userId": user_id,
                    "module": "BOOKING",
                    "referenceId": reference_id,
                    "idempotencyKey": reference_id,
                    "amount": amount,
                    "currency": "QAR",
                    "paymentMethod": "WALLET",
                    "status": "INITIATED",
                }
            )

            if amount > 0:
                await WalletService.debit_wallet(
                    user_id=user_id,
                    amount=amount,
                    description=f"Course booking: {course.title}",
                    reference_id=reference_id,
                    payment_log_id=log.id,
                )

            booking = await _confirm_course_booking(user_id, course_id, amount, "WALLET", log.id)

            await prisma.paymentlog.update(
                where={"id": log.id},
                data={"status": "SUCCESS", "referenceId": reference_id},
            )

            coverage_msg = "Course is free — no payment required." if amount == 0 else f"Amount paid: QAR {amount:.2f}."

            try:
                await notify_user(
                    user_id=user_id,
                    title="Course Booking Confirmed",
                    message=(
                        f"Your booking for '{course.title}' is confirmed. "
                        f"{coverage_msg}"
                    ),
                    notification_type="SUCCESS",
                    send_via_app=True,
                    send_via_whatsapp=True,
                )
            except Exception as exc:
                logger.warning("Course booking confirmation notification failed (non-fatal): %s", exc)

            return {
                "status": "CONFIRMED",
                "booking_id": booking.id,
                "course_id": course_id,
                "course_title": course.title,
                "amount_paid": amount,
                "payment_method": "WALLET",
                "covered_by": "free" if amount == 0 else "none",
                "message": f"Course booking confirmed. {coverage_msg}" if amount == 0 else "Course booking confirmed. Enjoy your course!",
            }

        # ── GATEWAY payment ───────────────────────────────────────────────────
        try:
            session = await PaymentService.create_payment_session(
                amount=amount,
                customer_name=user.name,
                customer_email=user.email,
                customer_reference=reference_id,
                description=f"Course booking: {course.title}",
            )
        except ValueError as exc:
            raise HTTPException(status_code=502, detail=str(exc))

        # Reserve seat optimistically
        if course.availableSeat is not None:
            await prisma.course.update(
                where={"id": course_id},
                data={"availableSeat": course.availableSeat - 1},
            )

        # Create PENDING booking (courseId set, classId null)
        booking = await prisma.booking.create(
            data={
                "userId": user_id,
                "courseId": course_id,
                "status": "PENDING",
                "paymentMethod": "GATEWAY",
                "amountPaid": amount,
            }
        )

        log = await prisma.paymentlog.create(
            data={
                "userId": user_id,
                "module": "BOOKING",
                "referenceId": reference_id,
                "idempotencyKey": reference_id,
                "amount": amount,
                "currency": "QAR",
                "paymentMethod": "GATEWAY",
                "status": "INITIATED",
                "gatewayInvoiceId": session["invoice_id"],
            }
        )

        logger.info("Course booking gateway initiated: user=%s course=%s ref=%s", user_id, course_id, reference_id)

        return {
            "status": "PENDING",
            "booking_id": booking.id,
            "payment_url": session["payment_url"],
            "invoice_id": session["invoice_id"],
            "reference_id": reference_id,
            "amount": amount,
            "currency": "QAR",
            "covered_by": "none",
            "message": "Redirect user to payment_url to complete payment. Course booking will be confirmed automatically.",
        }

    # ── Internal: called by WebhookService ───────────────────────────────────

    @staticmethod
    async def confirm_gateway_booking(
        user_id: str,
        reference_id: str,
        invoice_id: str,
        amount: float,
    ) -> None:
        """
        Confirm a PENDING gateway booking (class or course) after payment.
        Idempotent — safe to call from both callback and webhook.
        """
        log = await prisma.paymentlog.find_first(
            where={"referenceId": reference_id, "module": "BOOKING"}
        )
        if not log:
            logger.error("Booking payment log not found for ref=%s", reference_id)
            return

        if log.status == "SUCCESS":
            logger.info("Booking already confirmed for ref=%s — skipping", reference_id)
            return

        # Find pending booking for this user (most recent)
        booking = await prisma.booking.find_first(
            where={"userId": user_id, "status": "PENDING"},
            order={"bookedAt": "desc"},
        )
        if not booking:
            logger.error("No pending booking found for user=%s ref=%s", user_id, reference_id)
            return

        await prisma.booking.update(
            where={"id": booking.id},
            data={"status": "CONFIRMED", "paymentLogId": log.id},
        )
        await prisma.paymentlog.update(
            where={"id": log.id},
            data={"status": "SUCCESS", "gatewayPaymentId": invoice_id},
        )
        logger.info("Booking gateway confirmed: user=%s booking=%s ref=%s", user_id, booking.id, reference_id)

        # ── Notify user (in-app + WhatsApp, concurrent, best-effort) ──
        try:
            title = "your session"
            if booking.classId:
                cls = await prisma.classes.find_unique(where={"id": booking.classId})
                title = cls.title if cls else "your class"
            elif booking.courseId:
                course = await prisma.course.find_unique(where={"id": booking.courseId})
                title = course.title if course else "your course"

            await notify_user(
                user_id=user_id,
                title="Booking Confirmed",
                message=(
                    f"Your booking for '{title}' is confirmed. "
                    f"Amount paid: QAR {amount:.2f}."
                ),
                notification_type="SUCCESS",
                send_via_app=True,
                send_via_whatsapp=True,
            )
        except Exception as exc:
            logger.warning("Booking gateway confirmation notification failed (non-fatal): %s", exc)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

async def _confirm_booking(
    user_id: str,
    class_id: str,
    amount: float,
    payment_method: str,
    log_id: str,
) -> object:
    """Create or update a CLASS booking to CONFIRMED and decrement seat."""
    cls = await prisma.classes.find_unique(where={"id": class_id})

    # Check if a cancelled booking exists to re-use
    existing = await prisma.booking.find_first(
        where={"userId": user_id, "classId": class_id}
    )

    if existing:
        booking = await prisma.booking.update(
            where={"id": existing.id},
            data={
                "status": "CONFIRMED",
                "paymentMethod": payment_method,
                "amountPaid": amount,
                "paymentLogId": log_id,
                "cancelledAt": None,
            },
        )
    else:
        booking = await prisma.booking.create(
            data={
                "userId": user_id,
                "classId": class_id,
                "status": "CONFIRMED",
                "paymentMethod": payment_method,
                "amountPaid": amount,
                "paymentLogId": log_id,
            }
        )

    # Decrement seat
    if cls and cls.availableSeat is not None and cls.availableSeat > 0:
        await prisma.classes.update(
            where={"id": class_id},
            data={"availableSeat": cls.availableSeat - 1},
        )

    return booking


async def _confirm_course_booking(
    user_id: str,
    course_id: str,
    amount: float,
    payment_method: str,
    log_id: str,
) -> object:
    """Create or update a COURSE booking to CONFIRMED and decrement seat."""
    course = await prisma.course.find_unique(where={"id": course_id})

    # Check if a cancelled booking exists to re-use
    existing = await prisma.booking.find_first(
        where={"userId": user_id, "courseId": course_id}
    )

    if existing:
        booking = await prisma.booking.update(
            where={"id": existing.id},
            data={
                "status": "CONFIRMED",
                "paymentMethod": payment_method,
                "amountPaid": amount,
                "paymentLogId": log_id,
                "cancelledAt": None,
            },
        )
    else:
        booking = await prisma.booking.create(
            data={
                "userId": user_id,
                "courseId": course_id,
                "status": "CONFIRMED",
                "paymentMethod": payment_method,
                "amountPaid": amount,
                "paymentLogId": log_id,
            }
        )

    # Decrement seat
    if course and course.availableSeat is not None and course.availableSeat > 0:
        await prisma.course.update(
            where={"id": course_id},
            data={"availableSeat": course.availableSeat - 1},
        )

    return booking


async def _restore_package_session(membership_id: str) -> bool:
    """
    Restore one session to the package instance identified by membership_id.

    Called by cancel_booking when a class booking that was paid for using a
    package session (payment_method="PACKAGE") is cancelled ≥ 3h before start.

    Uses Membership.progress as the sessions-used counter (same convention as
    PackageService.decrement_session_for_user — this is the exact inverse):
      progress  is decremented by 1, but never below 0.

    If the package has unlimited sessions (no numberOfSessions set on the
    template) there is nothing to restore counter-wise, but we still return
    True so the cancellation flow treats the operation as successful.

    Returns True on success, False if the membership is no longer resolvable.
    """
    from app.services.package_service import _decode_time_restriction

    membership = await prisma.membership.find_unique(where={"id": membership_id})
    if not membership:
        logger.warning("_restore_package_session: membership %s not found", membership_id)
        return False

    # Resolve the Package template to check whether it has finite sessions
    package = await prisma.package.find_first(where={"name": membership.name})
    if not package:
        # Template deleted — still treat as unlimited / no counter to restore
        logger.info(
            "_restore_package_session: package template for '%s' not found; treating as unlimited",
            membership.name,
        )
        return True

    _, total_sessions = _decode_time_restriction(package.timeRestriction)
    if total_sessions is None:
        # Unlimited sessions — counter is not tracked, nothing to restore
        return True

    current_used = int(membership.progress or 0)
    restored_used = max(0, current_used - 1)

    await prisma.membership.update(
        where={"id": membership_id},
        data={"progress": float(restored_used)},
    )

    logger.info(
        "_restore_package_session: membership=%s sessions_used %d→%d",
        membership_id, current_used, restored_used,
    )
    return True