#!/usr/bin/env python3
"""
Exchange Shopee OAuth `code` for access + refresh tokens, persist to Redis + DB.

Prerequisite: run scripts/shopee_gen_auth_url.py first, approve in browser,
copy code + shop_id from Shopee redirect URL.

Usage:
  python scripts/shopee_exchange_token.py --code <CODE> --shop-id <SHOP_ID>

Stores tokens in:
  - Redis:  shopee:token:{shop_id}:access  (TTL ≈ 3.9h)
            shopee:token:{shop_id}:refresh (TTL ≈ 28.9d)
  - DB:     platform_config.credentials (Fernet-encrypted, if CREDENTIAL_KEYS set)
"""
from __future__ import annotations

import argparse
import asyncio
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))


async def _run(code: str, shop_id: str, dry_run: bool) -> int:
    from src.connectors.shopee.oauth import (
        auth_status, exchange_code_for_tokens, persist_tokens,
    )

    print(f"Exchanging code for shop_id={shop_id}...")
    try:
        tokens = await exchange_code_for_tokens(code, shop_id)
    except Exception as e:
        print(f"ERROR: token exchange failed: {e}", file=sys.stderr)
        return 1

    print(f"  access_token:           {tokens['access_token'][:12]}... (len={len(tokens['access_token'])})")
    print(f"  refresh_token:          {tokens['refresh_token'][:12]}... (len={len(tokens['refresh_token'])})")
    print(f"  access expire_in:       {tokens['expire_in']} sec (≈ {tokens['expire_in']/3600:.1f} h)")
    print(f"  refresh expire_in:      {tokens['refresh_token_expire_in']} sec "
          f"(≈ {tokens['refresh_token_expire_in']/86400:.1f} days)")

    if dry_run:
        print("DRY-RUN: tokens NOT persisted (--dry-run flag set).")
        return 0

    try:
        await persist_tokens(shop_id, tokens)
    except Exception as e:
        print(f"ERROR: persist failed: {e}", file=sys.stderr)
        return 1

    status = await auth_status(shop_id)
    print()
    print(f"Stored. Status:")
    print(f"  Redis access TTL:       {status['access_ttl_seconds']} sec")
    print(f"  Redis refresh TTL:      {status['refresh_ttl_seconds']} sec "
          f"({status['refresh_expires_in_days']} days)")
    print(f"  Sandbox:                {status['sandbox']}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--code", required=True, help="OAuth code from redirect")
    ap.add_argument("--shop-id", required=True, help="Shop ID from redirect")
    ap.add_argument("--dry-run", action="store_true",
                    help="Exchange but do not persist (test partner_key validity)")
    args = ap.parse_args()
    return asyncio.run(_run(args.code, args.shop_id, args.dry_run))


if __name__ == "__main__":
    sys.exit(main())
