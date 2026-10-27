"""
app/Scripts/cleanup_seed_data.py
================================
ONE-TIME production cleanup — removes all demo / seed / test data.

KEEPS (and nothing else):
  • The admin account  (BOOTSTRAP_ADMIN_EMAIL, default imanelmahdi@gmail.com)
  • The Role Matrix    (every PermissionRule row, untouched)
  • Read-only service API keys and their service accounts
    (every service-key Otp type — reporting and website — + users @service.inara.internal)

DELETES every other row in every business table: users, classes, courses,
bookings, memberships, packages, products, orders, wallets, payment logs,
reviews, wishlists, carts, notifications, news, About Us, legal documents,
invitations and all non-service OTP / refresh-token rows.

Safety
------
  • DRY-RUN by default — prints exactly what would be removed, changes nothing.
  • Real run needs BOTH flags:   --execute --confirm "WIPE SEED DATA"
  • The whole purge + admin bootstrap runs in ONE database transaction and is
    verified before commit; any failure rolls everything back.
  • Idempotent — running it again on a clean database deletes nothing.

Usage (backend container terminal):

    python -m app.Scripts.cleanup_seed_data                      # dry-run report
    python -m app.Scripts.cleanup_seed_data --execute --confirm "WIPE SEED DATA"

TAKE A DATABASE BACKUP FIRST (Coolify → database → Backups, or pg_dump).
All users are signed out by this cleanup (refresh tokens are removed).
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import timedelta
from typing import Any, Dict, List, Tuple

from app.Scripts.seed import ADMIN_EMAIL, ensure_admin, ensure_permission_rules
from app.core.config import settings
from app.core.service_keys import ALL_SERVICE_KEY_OTP_TYPES, SERVICE_ACCOUNT_EMAIL_DOMAIN
from app.db.db_client import connect_db, disconnect_db, prisma

CONFIRM_PHRASE = "WIPE SEED DATA"

# Prisma client accessor names, ordered CHILDREN FIRST so no foreign key is
# violated (users are handled last, OTPs just before them). Verified against
# prisma/schema.prisma — every model except PermissionRule is covered.
PURGE_ORDER: List[Tuple[str, str]] = [
    ("Invitation",        "invitation"),
    ("MemberInvitation",  "memberinvitation"),
    ("LegalDocument",     "legaldocument"),
    ("AboutUs",           "aboutus"),
    ("News",              "news"),
    ("Review",            "review"),
    ("Wishlist",          "wishlist"),
    ("CartItem",          "cartitem"),
    ("OrderItem",         "orderitem"),
    ("Order",             "order"),
    ("Notification",      "notification"),
    ("Booking",           "booking"),
    ("Membership",        "membership"),
    ("Classes",           "classes"),
    ("Course",            "course"),
    ("Package",           "package"),
    ("Product",           "product"),
    ("WalletTransaction", "wallettransaction"),
    ("Wallet",            "wallet"),
    ("PaymentLog",        "paymentlog"),
]

# Rows that must survive.
_SERVICE_EMAIL_SUFFIX = "@" + SERVICE_ACCOUNT_EMAIL_DOMAIN
OTP_PURGE_WHERE: Dict[str, Any] = {"NOT": {"type": {"in": ALL_SERVICE_KEY_OTP_TYPES}}}
USER_PURGE_WHERE: Dict[str, Any] = {
    "NOT": [
        {"email": ADMIN_EMAIL},
        {"email": {"endswith": _SERVICE_EMAIL_SUFFIX}},
    ]
}


def _db_target() -> str:
    """host/dbname from DATABASE_URL — never prints credentials."""
    url = settings.DATABASE_URL
    rest = url.split("://", 1)[-1]
    hostpart = rest.rpartition("@")[2]          # robust to '@' inside the password
    return hostpart.split("?", 1)[0]


async def _plan(db: Any) -> List[Tuple[str, int]]:
    """Row counts that WOULD be deleted, in deletion order."""
    plan = [(label, await getattr(db, accessor).count()) for label, accessor in PURGE_ORDER]
    plan.append(("Otp (non-service)", await db.otp.count(where=OTP_PURGE_WHERE)))
    plan.append(("User (non-admin, non-service)", await db.user.count(where=USER_PURGE_WHERE)))
    return plan


async def _kept(db: Any) -> List[Tuple[str, int]]:
    return [
        (f"Admin ({ADMIN_EMAIL})", await db.user.count(where={"email": ADMIN_EMAIL})),
        ("Role Matrix rules", await db.permissionrule.count()),
        ("Service accounts", await db.user.count(where={"email": {"endswith": _SERVICE_EMAIL_SUFFIX}})),
        ("Service API keys", await db.otp.count(where={"type": {"in": ALL_SERVICE_KEY_OTP_TYPES}})),
    ]


def _print_table(title: str, rows: List[Tuple[str, int]]) -> None:
    print(f"\n{title}")
    print("-" * 52)
    for label, n in rows:
        print(f"  {label:<38}{n:>10}")


async def _purge(db: Any) -> None:
    for _, accessor in PURGE_ORDER:
        await getattr(db, accessor).delete_many()
    await db.otp.delete_many(where=OTP_PURGE_WHERE)
    await db.user.delete_many(where=USER_PURGE_WHERE)


async def _run(execute: bool, reset_password: bool) -> int:
    print("=" * 60)
    print(" INARA — seed data cleanup")
    print(f" Database : {_db_target()}")
    print(f" Mode     : {'EXECUTE' if execute else 'DRY-RUN (nothing will be changed)'}")
    print("=" * 60)

    plan = await _plan(prisma)
    _print_table("Rows to DELETE", plan)
    _print_table("Rows KEPT", await _kept(prisma))

    if not execute:
        print("\nDry-run only. To apply:\n"
              f'  python -m app.Scripts.cleanup_seed_data --execute --confirm "{CONFIRM_PHRASE}"\n')
        return 0

    async with prisma.tx(max_wait=timedelta(seconds=10), timeout=timedelta(minutes=2)) as tx:
        await _purge(tx)
        await ensure_permission_rules(tx)          # only fills an EMPTY Role Matrix
        action, email = await ensure_admin(tx, reset_password=reset_password)

        # Verify before commit — raising here rolls the whole transaction back.
        leftovers = [(l, n) for l, n in await _plan(tx) if n]
        if leftovers:
            raise RuntimeError(f"Verification failed, rolling back: {leftovers}")
        if await tx.user.count(where={"email": ADMIN_EMAIL, "role": "ADMIN"}) != 1:
            raise RuntimeError("Verification failed: admin account missing, rolling back")

    _print_table("Rows KEPT (after cleanup)", await _kept(prisma))
    print(f"\n✅ Cleanup complete. Admin {email}: {action}.")
    if action == "unchanged":
        print("   The admin already existed, so its password was left as is.\n"
              "   Add --reset-admin-password to re-apply the bootstrap password.")
    print("   All users were signed out. About Us / Privacy / Terms / FAQ stay empty\n"
          "   (HTTP 404) until the admin publishes real content.\n")
    return 0


async def _main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="cleanup_seed_data",
        description="One-time removal of all seed/demo data. Keeps the admin, the Role Matrix and service API keys. Dry-run by default.",
    )
    parser.add_argument("--execute", action="store_true", help="Actually delete (default is a dry-run).")
    parser.add_argument("--confirm", default="", help=f'Must equal "{CONFIRM_PHRASE}" together with --execute.')
    parser.add_argument("--reset-admin-password", action="store_true",
                        help="Re-apply the bootstrap password if the admin account already exists.")
    args = parser.parse_args(argv)

    if args.execute and args.confirm != CONFIRM_PHRASE:
        print(f'Refusing to run: add  --confirm "{CONFIRM_PHRASE}"  to proceed.', file=sys.stderr)
        return 2

    await connect_db()
    try:
        return await _run(args.execute, args.reset_admin_password)
    except Exception as exc:
        print(f"\n❌ Cleanup failed — transaction rolled back, no data changed.\n   {exc}", file=sys.stderr)
        return 1
    finally:
        await disconnect_db()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
