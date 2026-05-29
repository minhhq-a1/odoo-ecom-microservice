"""Scenario 2 — Flash sale spike: 500 webhook trong 60 giây (burst).

Pass: không mất event, Odoo response time < 3s, outbox catches up < 5 phút.

Run:
  LOAD_PARTNER_KEY=xxx \
  locust -f tests/load/locustfile_flashsale.py \
         -H https://staging.example.com -u 50 -r 50 -t 60s --headless --csv=flashsale
"""
from __future__ import annotations

from locust import HttpUser, constant, task

from tests.load._payload import make_order_payload


class BurstUser(HttpUser):
    wait_time = constant(0.05)  # 50 users × 20 req/s = 1000/min burst

    @task
    def burst(self) -> None:
        body, sig = make_order_payload()
        self.client.post(
            "/webhook/shopee", data=body,
            headers={"X-Shopee-Signature": sig, "Content-Type": "application/json"},
            name="POST /webhook/shopee [burst]",
        )
