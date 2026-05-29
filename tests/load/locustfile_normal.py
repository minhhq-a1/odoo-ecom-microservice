"""Scenario 1 — Normal load: 300 webhook/phút trong 10 phút.

Pass: queue drain time < 2 phút, error rate < 0.1%.

Run:
  LOAD_PARTNER_KEY=xxx LOAD_SHOP_ID=123 \
  locust -f tests/load/locustfile_normal.py \
         -H https://staging.example.com -u 5 -r 1 -t 10m --headless --csv=normal
"""

from __future__ import annotations

from locust import HttpUser, between, events, task

from tests.load._payload import make_order_payload


class WebhookUser(HttpUser):
    wait_time = between(0.8, 1.2)  # ~5 req/s per user × 5 users = 25/s ≈ 1500/min — adjust -u

    @task(8)
    def shopee_order(self) -> None:
        body, sig = make_order_payload()
        with self.client.post(
            "/webhook/shopee",
            data=body,
            headers={"X-Shopee-Signature": sig, "Content-Type": "application/json"},
            catch_response=True,
            name="POST /webhook/shopee [order]",
        ) as resp:
            if resp.status_code != 200:
                resp.failure(f"status={resp.status_code} body={resp.text[:200]}")

    @task(2)
    def shopee_logistics(self) -> None:
        from tests.load._payload import make_logistics_payload

        body, sig = make_logistics_payload()
        with self.client.post(
            "/webhook/shopee",
            data=body,
            headers={"X-Shopee-Signature": sig, "Content-Type": "application/json"},
            catch_response=True,
            name="POST /webhook/shopee [logistics]",
        ) as resp:
            if resp.status_code != 200:
                resp.failure(f"status={resp.status_code}")


@events.test_stop.add_listener
def _on_stop(environment, **kwargs) -> None:
    stats = environment.stats.total
    print(f"Total requests: {stats.num_requests}")
    print(f"Failures:       {stats.num_failures}")
    print(f"P95:            {stats.get_response_time_percentile(0.95):.0f}ms")
    if stats.num_failures / max(1, stats.num_requests) > 0.001:
        print("FAIL: error rate > 0.1%")
        environment.process_exit_code = 1
