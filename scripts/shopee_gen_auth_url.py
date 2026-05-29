#!/usr/bin/env python3
"""
Generate Shopee OAuth authorization URL.

Usage:
  python scripts/shopee_gen_auth_url.py \
      --redirect https://your.middleware.com/admin/shopee/callback

Reads SHOPEE_PARTNER_ID + SHOPEE_PARTNER_KEY + SHOPEE_IS_SANDBOX from env.
Prints URL to stdout. Paste into browser, approve, Shopee will redirect to
  <redirect>?code=XXX&shop_id=YYY
then run scripts/shopee_exchange_token.py with that code + shop_id.
"""

from __future__ import annotations

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--redirect",
        required=True,
        help="OAuth redirect URI registered with Shopee Partner Portal",
    )
    args = ap.parse_args()

    from src.connectors.shopee.oauth import build_auth_url

    try:
        result = build_auth_url(args.redirect)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2

    print(f"Environment:   {'SANDBOX' if result.sandbox else 'PRODUCTION'}")
    print(f"Timestamp:     {result.timestamp}")
    print(f"Redirect URI:  {args.redirect}")
    print()
    print("Open this URL in browser (logged in to Shopee Seller Center):")
    print()
    print(result.url)
    print()
    print("After approving, Shopee will redirect to:")
    print(f"  {args.redirect}?code=<CODE>&shop_id=<SHOP_ID>")
    print()
    print("Then run:")
    print("  python scripts/shopee_exchange_token.py --code <CODE> --shop-id <SHOP_ID>")
    return 0


if __name__ == "__main__":
    sys.exit(main())
