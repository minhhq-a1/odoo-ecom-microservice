"""Scenario 3 — Odoo down 5 phút: 100 webhooks during outage.

Pre-step (manual):  docker compose stop odoo  (or block egress)
Run locust          (this file) sending 100 webhooks slowly
Post-step (manual): docker compose start odoo
Verify:             all webhooks eventually synced via outbox relay, 0 dead_letter.

Run:
  LOAD_PARTNER_KEY=xxx \
  locust -f tests/load/locustfile_odoo_down.py \
         -H https://staging.example.com -u 2 -r 1 -t 5m --headless --csv=odoo_down
"""

from __future__ import annotations

from locust import HttpUser, between, task

from tests.load._payload import make_order_payload


class SteadyUser(HttpUser):
    wait_time = between(2.5, 3.5)

    @task
    def shopee_order(self) -> None:
        body, sig = make_order_payload()
        self.client.post(
            "/webhook/shopee",
            data=body,
            headers={"X-Shopee-Signature": sig, "Content-Type": "application/json"},
            name="POST /webhook/shopee [odoo_down]",
        )
