"""
app/Scripts/seed.py
===================
PRODUCTION BOOTSTRAP SEED — Inara Backend

Creates only what a brand-new production database needs to be usable:

  1. The Role Matrix (PermissionRule) defaults — only when the table is empty.
  2. One ADMIN account — created if missing; never duplicated.

It is NON-DESTRUCTIVE and IDEMPOTENT: it never deletes or overwrites business
data, so it is safe to run at any time, any number of times. (The previous
demo seed wiped every table, including the read-only service API keys; that
behaviour is gone.)

Run it inside the backend container (Coolify → backend → Terminal):

    python -m app.Scripts.seed
    python -m app.Scripts.seed --reset-admin-password     # re-apply the admin password

To remove demo/seed data that is already in a database, use the one-time
tool instead:  python -m app.Scripts.cleanup_seed_data   (dry-run by default).

Admin credentials
-----------------
    Email    : imanelmahdi@gmail.com
    Password : Admin123!

Both can be overridden without touching code via the environment variables
BOOTSTRAP_ADMIN_EMAIL / BOOTSTRAP_ADMIN_PASSWORD. Change the password from the
dashboard after the first login.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from typing import Any, Tuple

from app.core.security import get_password_hash
from app.db.db_client import connect_db, disconnect_db, prisma

# ════════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ════════════════════════════════════════════════════════════════════════════

ADMIN_EMAIL: str = os.getenv("BOOTSTRAP_ADMIN_EMAIL", "imanelmahdi@gmail.com").strip()
ADMIN_PASSWORD: str = os.getenv("BOOTSTRAP_ADMIN_PASSWORD", "Admin123!")
ADMIN_NAME: str = "Admin"

# Role Matrix defaults — what each role can see in the dashboard.
PERMISSION_RULES = [
    {"moduleName": "Dashboard/Overview",   "adminAccess": "VISIBLE", "managerAccess": "HIDDEN",  "instructorAccess": "HIDDEN",  "displayOrder": 1},
    {"moduleName": "Classes & Courses",    "adminAccess": "VISIBLE", "managerAccess": "VISIBLE", "instructorAccess": "VISIBLE", "displayOrder": 2},
    {"moduleName": "Member Database",      "adminAccess": "VISIBLE", "managerAccess": "VISIBLE", "instructorAccess": "VISIBLE", "displayOrder": 3},
    {"moduleName": "Instructor Directory", "adminAccess": "VISIBLE", "managerAccess": "VISIBLE", "instructorAccess": "VISIBLE", "displayOrder": 4},
    {"moduleName": "App Settings",         "adminAccess": "VISIBLE", "managerAccess": "VISIBLE", "instructorAccess": "HIDDEN",  "displayOrder": 5},
    {"moduleName": "Financial Reporting",  "adminAccess": "VISIBLE", "managerAccess": "HIDDEN",  "instructorAccess": "HIDDEN",  "displayOrder": 6},
    {"moduleName": "Role Matrix",          "adminAccess": "VISIBLE", "managerAccess": "HIDDEN",  "instructorAccess": "HIDDEN",  "displayOrder": 7},
    {"moduleName": "Legal Documents",      "adminAccess": "VISIBLE", "managerAccess": "HIDDEN",  "instructorAccess": "HIDDEN",  "displayOrder": 8},
    {"moduleName": "Payment Logs",         "adminAccess": "VISIBLE", "managerAccess": "HIDDEN",  "instructorAccess": "HIDDEN",  "displayOrder": 9},
    {"moduleName": "Wallet Management",    "adminAccess": "VISIBLE", "managerAccess": "VISIBLE", "instructorAccess": "HIDDEN",  "displayOrder": 10},
]


# ════════════════════════════════════════════════════════════════════════════
# SEED STEPS  (each accepts any Prisma-like client, so they also work inside a
# transaction — see cleanup_seed_data.py)
# ════════════════════════════════════════════════════════════════════════════

async def ensure_permission_rules(db: Any = prisma) -> int:
    """Create the default Role Matrix, only if the table is empty. Returns rows created."""
    if await db.permissionrule.count() > 0:
        return 0

    for rule in PERMISSION_RULES:
        await db.permissionrule.create(data=rule)
    return len(PERMISSION_RULES)


async def ensure_admin(db: Any = prisma, *, reset_password: bool = False) -> Tuple[str, str]:
    """
    Guarantee exactly one usable ADMIN account for ADMIN_EMAIL.

    Returns (action, email) where action is one of:
      "created"          — new admin created with ADMIN_PASSWORD
      "password-reset"   — existing account promoted/refreshed and password re-applied
      "unchanged"        — existing account already an active, verified ADMIN
                           (password left untouched)
    """
    existing = await db.user.find_unique(where={"email": ADMIN_EMAIL})

    if existing is None:
        await db.user.create(
            data={
                "email": ADMIN_EMAIL,
                "passwordHash": get_password_hash(ADMIN_PASSWORD),
                "name": ADMIN_NAME,
                "role": "ADMIN",
                "isActive": True,
                "isVerified": True,
            }
        )
        return "created", ADMIN_EMAIL

    update: dict = {}
    if existing.role != "ADMIN":
        update["role"] = "ADMIN"
    if not existing.isActive:
        update["isActive"] = True
    if not existing.isVerified:
        update["isVerified"] = True
    if reset_password:
        update["passwordHash"] = get_password_hash(ADMIN_PASSWORD)

    if update:
        await db.user.update(where={"id": existing.id}, data=update)

    return ("password-reset" if reset_password else "unchanged"), ADMIN_EMAIL


# ════════════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ════════════════════════════════════════════════════════════════════════════

async def _main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="seed",
        description="Idempotent production bootstrap: Role Matrix defaults + one admin account.",
    )
    parser.add_argument(
        "--reset-admin-password",
        action="store_true",
        help="Re-apply the bootstrap admin password to an existing admin account.",
    )
    args = parser.parse_args(argv)

    await connect_db()
    try:
        rules_created = await ensure_permission_rules()
        action, email = await ensure_admin(reset_password=args.reset_admin_password)
    except Exception as exc:  # surface failures to CI / the shell instead of swallowing them
        print(f"❌ Seed failed: {exc}", file=sys.stderr)
        return 1
    finally:
        await disconnect_db()

    print("=" * 60)
    print(" INARA — production bootstrap")
    print("=" * 60)
    print(f" Role Matrix : {'created ' + str(rules_created) + ' default rules' if rules_created else 'already present — untouched'}")
    print(f" Admin       : {email} ({action})")
    if action == "created" and ADMIN_PASSWORD == "Admin123!":
        print(" ⚠  The bootstrap password is in use — change it after first login.")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
