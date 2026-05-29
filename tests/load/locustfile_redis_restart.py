"""Scenario 4 — Redis restart during ingestion.

Pre-step:  start sending webhooks (this locustfile)
Mid-step:  docker compose restart redis   (manual)
Verify:    outbox relay job recovers, 0 webhooks lost (every signed payload becomes
           an outbox row regardless of Redis state).

Run:
  LOAD_PARTNER_KEY=xxx \
  locust -f tests/load/locustfile_redis_restart.py \
         -H https://staging.example.com -u 5 -r 1 -t 3m --headless --csv=redis_restart
"""
from __future__ import annotations

from locust import HttpUser, between, task

from tests.load._payload import make_order_payload


class IngestUser(HttpUser):
    wait_time = between(0.5, 1.5)

    @task
    def shopee_order(self) -> None:
        body, sig = make_order_payload()
        self.client.post(
            "/webhook/shopee", data=body,
            headers={"X-Shopee-Signature": sig, "Content-Type": "application/json"},
            name="POST /webhook/shopee [redis_restart]",
        )
