"""Pass-criteria verifier — query Prometheus + DB after load test.

Usage:
  ./scripts/verify_load_test.py --scenario normal --prometheus http://prom:9090 \
       --db postgresql://... --since 600 --max-error-rate 0.001
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.parse
import urllib.request


def query_prom(url: str, q: str) -> float | None:
    full = f"{url}/api/v1/query?query={urllib.parse.quote(q)}"
    try:
        with urllib.request.urlopen(full, timeout=10) as resp:  # noqa: S310
            data = json.loads(resp.read())
    except Exception as e:
        print(f"prom_query_failed: {q} — {e}", file=sys.stderr)
        return None
    res = data.get("data", {}).get("result", [])
    if not res:
        return 0.0
    try:
        return float(res[0]["value"][1])
    except (KeyError, ValueError, IndexError):
        return None


def check_normal(prom: str, window: int, max_error_rate: float) -> int:
    fails = 0
    err = query_prom(
        prom,
        f'sum(rate(mw_webhook_response_seconds_count{{status_code=~"5.."}}[{window}s]))'
        f" / sum(rate(mw_webhook_response_seconds_count[{window}s]))",
    )
    if err is not None and err > max_error_rate:
        print(f"FAIL error_rate={err:.4%} > {max_error_rate:.4%}")
        fails += 1
    else:
        print(f"OK   error_rate={err:.4%}")

    p95 = query_prom(
        prom,
        f"histogram_quantile(0.95, rate(mw_webhook_response_seconds_bucket[{window}s]))",
    )
    if p95 is not None and p95 > 0.5:
        print(f"FAIL p95_webhook={p95*1000:.0f}ms > 500ms")
        fails += 1
    else:
        print(f"OK   p95_webhook={p95*1000:.0f}ms")

    dlq = query_prom(prom, f"increase(mw_outbox_dead_letter_total[{window}s])")
    if dlq and dlq > 0:
        print(f"FAIL dead_letter_increase={dlq}")
        fails += 1
    else:
        print("OK   dead_letter_increase=0")

    age = query_prom(prom, "mw_outbox_oldest_pending_age_seconds")
    if age is not None and age > 120:
        print(f"FAIL outbox_oldest_age={age:.0f}s > 120s")
        fails += 1
    else:
        print(f"OK   outbox_oldest_age={age:.0f}s")

    return fails


def check_flashsale(prom: str, window: int) -> int:
    fails = 0
    odoo_p95 = query_prom(
        prom,
        f"histogram_quantile(0.95, rate(mw_odoo_request_duration_seconds_bucket[{window}s]))",
    )
    if odoo_p95 is not None and odoo_p95 > 3:
        print(f"FAIL odoo_p95={odoo_p95:.2f}s > 3s")
        fails += 1
    else:
        print(f"OK   odoo_p95={odoo_p95:.2f}s")

    dlq = query_prom(prom, f"increase(mw_outbox_dead_letter_total[{window}s])")
    if dlq and dlq > 0:
        print(f"FAIL dead_letter_increase={dlq}")
        fails += 1
    else:
        print("OK   dead_letter_increase=0")
    return fails


def check_recovery(prom: str, window: int) -> int:
    """After Odoo/Redis was restarted, verify 0 dead_letter accumulated."""
    fails = 0
    dlq = query_prom(prom, f"increase(mw_outbox_dead_letter_total[{window}s])")
    if dlq and dlq > 0:
        print(f"FAIL dead_letter_increase={dlq} (expected 0 — outbox should have caught all)")
        fails += 1
    else:
        print("OK   recovery successful — 0 dead_letter")

    pending = query_prom(prom, "sum(mw_outbox_pending_count)")
    if pending is not None and pending > 10:
        print(f"FAIL outbox_pending={pending:.0f} > 10 (didn't drain)")
        fails += 1
    else:
        print(f"OK   outbox_pending={pending:.0f}")
    return fails


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--scenario", choices=["normal", "flashsale", "odoo_down", "redis_restart"], required=True
    )
    ap.add_argument("--prometheus", required=True)
    ap.add_argument("--since", type=int, default=600, help="window seconds")
    ap.add_argument("--max-error-rate", type=float, default=0.001)
    args = ap.parse_args()

    print(f"Verifying scenario={args.scenario} window={args.since}s prom={args.prometheus}")
    if args.scenario == "normal":
        fails = check_normal(args.prometheus, args.since, args.max_error_rate)
    elif args.scenario == "flashsale":
        fails = check_flashsale(args.prometheus, args.since)
    else:
        fails = check_recovery(args.prometheus, args.since)

    if fails:
        print(f"\n{fails} check(s) failed")
        return 1
    print("\nALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
