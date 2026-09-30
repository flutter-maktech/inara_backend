"""
app/core/account_deletion_service.py
=====================================
Shared, transaction-safe "delete a User and everything they own" helper.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
WHY THIS EXISTS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Every table that references `users.id` does so with `ON DELETE RESTRICT`
(confirmed directly in prisma/migrations/20260506092616_inara_db/migration.sql):

    bookings, memberships, cart_items, orders, wallets, payment_logs,
    reviews, wishlists, notifications        → ALL "ON DELETE RESTRICT"

`ON DELETE RESTRICT` means Postgres refuses to delete the `users` row while
ANY of those child rows still exist. A plain `prisma.user.delete(...)` will
raise a foreign-key-violation for any account that has ever placed a
booking, held a wallet, written a review, etc. — i.e. almost any real user.

This mirrors the exact "Enterprise Standard" delete strategy already used
in app/services/product_service.py (purge RESTRICT-blocked children inside
a single atomic transaction, then delete the parent row). This module
generalises that pattern for `User` rows so it isn't duplicated three times
across self-delete (users.py), instructor-delete (instructor_service.py),
and manager-delete (manager_service.py).

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
SCOPE — what this does NOT handle
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
`courses.instructor_id` and `classes.instructor_id` are ALSO RESTRICT, but
those are deliberately NOT purged here — silently deleting an instructor's
taught classes/courses would destroy other members' booking/review history
(classId/courseId would SET NULL on their bookings, and their wishlists/
reviews for that class or course would CASCADE-delete). That is a decision
for the caller, not this helper. See InstructorService.delete_instructor,
which only calls this function once it has verified zero classes/courses
remain — i.e. this helper assumes that check already happened.

`invitations.invited_by` and `member_invitations.invited_by` ARE genuine
`ON DELETE CASCADE` in the schema, so nothing needs to be done for those —
Postgres handles them automatically.
"""

from __future__ import annotations

from typing import Any, Dict

from app.db.db_client import prisma


async def purge_and_delete_user(user_id: str) -> Dict[str, Any]:
    """
    Permanently delete a User row and every row that would otherwise block
    that delete via an ON DELETE RESTRICT foreign key.

    Runs as a SINGLE atomic transaction — either everything is removed, or
    (on any error) nothing is, so the account is never left half-deleted.

    Counts are captured BEFORE the transaction because `tx.delete_many()`
    returns None inside a Prisma Python transaction, not a BatchPayload
    (same constraint documented in product_service.py's delete_product).

    Returns a dict of how many rows were removed per table, e.g.:
        {
            "walletTransactionsRemoved": 12,
            "bookingsRemoved": 34,
            "membershipsRemoved": 1,
            "cartItemsRemoved": 0,
            "ordersRemoved": 5,
            "paymentLogsRemoved": 9,
            "reviewsRemoved": 2,
            "wishlistsRemoved": 3,
            "notificationsRemoved": 18,
        }

    Caller responsibility: verify the user exists and is eligible for
    deletion (role checks, "has no classes/courses" checks, etc.) BEFORE
    calling this — it performs no authorization or eligibility checks of
    its own, only the mechanical purge + delete.
    """
    wallet = await prisma.wallet.find_unique(where={"userId": user_id})

    counts: Dict[str, Any] = {
        "walletTransactionsRemoved": (
            await prisma.wallettransaction.count(where={"walletId": wallet.id})
            if wallet else 0
        ),
        "bookingsRemoved":      await prisma.booking.count(where={"userId": user_id}),
        "membershipsRemoved":   await prisma.membership.count(where={"userId": user_id}),
        "cartItemsRemoved":     await prisma.cartitem.count(where={"userId": user_id}),
        "ordersRemoved":        await prisma.order.count(where={"userId": user_id}),
        "paymentLogsRemoved":   await prisma.paymentlog.count(where={"userId": user_id}),
        "reviewsRemoved":       await prisma.review.count(where={"userId": user_id}),
        "wishlistsRemoved":     await prisma.wishlist.count(where={"userId": user_id}),
        "notificationsRemoved": await prisma.notification.count(where={"userId": user_id}),
    }

    # Deletion order matters: children first, parent last.
    #   1. wallet_transactions  (RESTRICT → wallets)   — must precede the wallet itself
    #   2. wallet                (RESTRICT → users)
    #   3. bookings               (RESTRICT → users)
    #   4. memberships             (RESTRICT → users)
    #   5. cart_items                (RESTRICT → users)
    #   6. orders                     (RESTRICT → users; order_items CASCADE automatically)
    #   7. payment_logs                (RESTRICT → users)
    #   8. reviews                      (RESTRICT → users)
    #   9. wishlists                     (RESTRICT → users)
    #  10. notifications                  (RESTRICT → users)
    #  11. user row itself                 (now safe — no dangling child rows remain)
    async with prisma.tx() as tx:
        if wallet:
            await tx.wallettransaction.delete_many(where={"walletId": wallet.id})
            await tx.wallet.delete(where={"id": wallet.id})
        await tx.booking.delete_many(where={"userId": user_id})
        await tx.membership.delete_many(where={"userId": user_id})
        await tx.cartitem.delete_many(where={"userId": user_id})
        await tx.order.delete_many(where={"userId": user_id})
        await tx.paymentlog.delete_many(where={"userId": user_id})
        await tx.review.delete_many(where={"userId": user_id})
        await tx.wishlist.delete_many(where={"userId": user_id})
        await tx.notification.delete_many(where={"userId": user_id})
        await tx.user.delete(where={"id": user_id})

    return counts