"""Measure Odoo XML-RPC write latency under concurrency.

Bypasses the Shopee fetch + transformer chain so the signal is pure
XML-RPC write throughput (sale.order create). Connects to a live Odoo 18
container.

Usage:
  ODOO_LIVE_URL=http://127.0.0.1:8180 \\
  ODOO_LIVE_DB=odoo18c-dev-test \\
  ODOO_LIVE_USER=admin ODOO_LIVE_PASSWORD=admin \\
  CONCURRENCY=8 TOTAL=200 \\
  python3 tests/load/odoo_xmlrpc_pressure.py
"""

from __future__ import annotations

import asyncio
import os
import statistics
import time
import xmlrpc.client
from concurrent.futures import ThreadPoolExecutor

URL = os.environ["ODOO_LIVE_URL"]
DB = os.environ["ODOO_LIVE_DB"]
USER = os.environ.get("ODOO_LIVE_USER", "admin")
PASSWORD = os.environ.get("ODOO_LIVE_PASSWORD", "admin")
CONCURRENCY = int(os.environ.get("CONCURRENCY", "8"))
TOTAL = int(os.environ.get("TOTAL", "200"))


def _authenticate() -> int:
    common = xmlrpc.client.ServerProxy(f"{URL}/xmlrpc/2/common", allow_none=True)
    uid = common.authenticate(DB, USER, PASSWORD, {})
    if not uid:
        raise SystemExit("Odoo authentication failed")
    return uid


def _bootstrap(uid: int) -> tuple[int, int]:
    """Bootstrap test partner and product. Idempotent under concurrent runs.

    Odoo product.product has UNIQUE(default_code), so concurrent creates race.
    Try create first; on Fault (duplicate), search again. Partner has no UNIQUE
    on name, so we rely on phone being effectively unique in test data.
    """
    models = xmlrpc.client.ServerProxy(f"{URL}/xmlrpc/2/object", allow_none=True)

    # Partner: try create first, fall back to search on duplicate
    try:
        partner_id = models.execute_kw(
            DB,
            uid,
            PASSWORD,
            "res.partner",
            "create",
            [{"name": "Load Test Buyer", "phone": "0900000000"}],
        )
    except xmlrpc.client.Fault:
        partner_ids = models.execute_kw(
            DB,
            uid,
            PASSWORD,
            "res.partner",
            "search",
            [[["phone", "=", "0900000000"]]],
            {"limit": 1},
        )
        if not partner_ids:
            raise SystemExit("Partner bootstrap failed: create raised Fault but search found nothing")
        partner_id = partner_ids[0]

    # Product: try create first, fall back to search on UNIQUE(default_code) violation
    try:
        product_id = models.execute_kw(
            DB,
            uid,
            PASSWORD,
            "product.product",
            "create",
            [
                {
                    "name": "Load Test SKU",
                    "default_code": "LOAD-SKU-001",
                    "list_price": 100000.0,
                    "type": "consu",
                }
            ],
        )
    except xmlrpc.client.Fault:
        product_ids = models.execute_kw(
            DB,
            uid,
            PASSWORD,
            "product.product",
            "search",
            [[["default_code", "=", "LOAD-SKU-001"]]],
            {"limit": 1},
        )
        if not product_ids:
            raise SystemExit("Product bootstrap failed: create raised Fault but search found nothing")
        product_id = product_ids[0]

    return partner_id, product_id


def _create_order(uid: int, partner_id: int, product_id: int, order_sn: str) -> float:
    models = xmlrpc.client.ServerProxy(f"{URL}/xmlrpc/2/object", allow_none=True)
    t = time.perf_counter()
    models.execute_kw(
        DB,
        uid,
        PASSWORD,
        "sale.order",
        "create",
        [
            {
                "partner_id": partner_id,
                "client_order_ref": order_sn,
                "x_platform": "shopee",
                "x_platform_order_id": order_sn,
                "x_platform_order_sn": order_sn,
                "x_sync_status": "synced",
                "order_line": [
                    (
                        0,
                        0,
                        {
                            "product_id": product_id,
                            "product_uom_qty": 1.0,
                            "price_unit": 100000.0,
                        },
                    )
                ],
            }
        ],
    )
    return (time.perf_counter() - t) * 1000.0


async def main() -> None:
    print(f"Odoo: {URL} db={DB}")
    print(f"Concurrency={CONCURRENCY} total={TOTAL}")
    uid = _authenticate()
    partner_id, product_id = _bootstrap(uid)
    print(f"Bootstrap: partner_id={partner_id} product_id={product_id}")

    loop = asyncio.get_event_loop()
    executor = ThreadPoolExecutor(max_workers=CONCURRENCY)

    async def task(idx: int) -> tuple[float, bool]:
        order_sn = f"LOADXMLRPC{int(time.time())}{idx:06d}"
        try:
            latency = await loop.run_in_executor(
                executor,
                _create_order,
                uid,
                partner_id,
                product_id,
                order_sn,
            )
            return latency, True
        except Exception as e:
            print(f"  err idx={idx}: {e}")
            return 0.0, False

    started = time.perf_counter()
    results = await asyncio.gather(*(task(i) for i in range(TOTAL)))
    elapsed = time.perf_counter() - started
    executor.shutdown(wait=False)

    successes = [lat for lat, ok in results if ok]
    failures = sum(1 for _, ok in results if not ok)

    print()
    print(f"Elapsed:     {elapsed:.2f}s")
    print(f"Throughput:  {len(successes)/elapsed:.2f} writes/s")
    print(f"Successes:   {len(successes)}/{TOTAL}")
    print(f"Failures:    {failures}")
    if successes:
        successes.sort()
        n = len(successes)
        print(f"P50:   {successes[n//2]:.0f}ms")
        print(f"P95:   {successes[int(n*0.95)]:.0f}ms")
        print(f"P99:   {successes[min(n-1, int(n*0.99))]:.0f}ms")
        print(f"Max:   {successes[-1]:.0f}ms")
        print(f"Mean:  {statistics.mean(successes):.0f}ms")


if __name__ == "__main__":
    asyncio.run(main())
