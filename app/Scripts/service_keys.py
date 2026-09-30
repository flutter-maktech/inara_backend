"""
app/Scripts/service_keys.py
===========================
Command-line tool for managing read-only Service API Keys.

Run it inside the backend container (Coolify → backend → Terminal):

    python -m app.Scripts.service_keys create --name "AI Reporting" --days 365
    python -m app.Scripts.service_keys list
    python -m app.Scripts.service_keys revoke <fingerprint>

`create` prints the raw key ONCE. Only its SHA-256 hash is stored, so a key
that is lost cannot be recovered — issue a new one and revoke the old.

To rotate with no downtime: create a new key, hand it over, confirm the
client has switched, then revoke the old fingerprint.

Staging and production have separate databases and separate
SERVICE_KEY_ENV_LABEL values ("stg" / "live"), so a key only works in the
environment where it was created.

WARNING: app/Scripts/seed.py wipes the Otp table, which deletes all service
keys. Never run the seed script against production.
"""

from __future__ import annotations

import argparse
import asyncio

from app.core.config import settings
from app.core.service_keys import issue_key, list_keys, revoke_key
from app.db.db_client import connect_db, disconnect_db


async def _main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="service_keys", description="Manage read-only service API keys")
    sub = parser.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("create", help="Issue a new read-only key")
    c.add_argument("--name", default="AI Reporting", help="Service account name")
    c.add_argument("--days", type=int, default=365, help="Validity in days (default 365)")

    sub.add_parser("list", help="List active keys (fingerprints only)")

    r = sub.add_parser("revoke", help="Revoke a key by fingerprint")
    r.add_argument("fingerprint")

    args = parser.parse_args(argv)
    await connect_db()
    try:
        if args.cmd == "create":
            raw, fp = await issue_key(args.name, args.days)
            print("=" * 72)
            print(f" Environment : {settings.SERVICE_KEY_ENV_LABEL}")
            print(f" Fingerprint : {fp}")
            print(f" Valid for   : {args.days} days")
            print(f" Header      : {settings.SERVICE_KEY_HEADER_NAME}: <key>")
            print(" KEY (shown once — deliver via a secure channel):")
            print(f"   {raw}")
            print("=" * 72)
        elif args.cmd == "list":
            rows = await list_keys()
            if not rows:
                print("No service keys.")
            for row in rows:
                print(f"{row['fingerprint']}  {row['account']:<45} created={row['createdAt']}  expires={row['expiresAt']}")
        elif args.cmd == "revoke":
            n = await revoke_key(args.fingerprint)
            print(f"Revoked {n} key(s). Takes effect within {settings.SERVICE_KEY_CACHE_TTL_SECONDS}s on every worker.")
    finally:
        await disconnect_db()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
