"""Load test fixtures + signed payload builder."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import random
import time
import uuid


def sign(body: bytes, partner_key: str) -> str:
    return hmac.new(partner_key.encode(), body, hashlib.sha256).hexdigest()


def make_order_payload(order_sn: str | None = None) -> tuple[bytes, str]:
    payload = {
        "code": 3,
        "shop_id": int(os.environ.get("LOAD_SHOP_ID", "123456")),
        "timestamp": int(time.time()),
        "data": {
            "ordersn": order_sn or f"LOAD{int(time.time())}{random.randint(1000, 9999)}",
            "status": random.choice(["READY_TO_SHIP", "UNPAID", "SHIPPED"]),
            "update_time": int(time.time()),
        },
    }
    body = json.dumps(payload).encode()
    return body, sign(body, os.environ["LOAD_PARTNER_KEY"])


def make_logistics_payload() -> tuple[bytes, str]:
    payload = {
        "code": 4,
        "shop_id": int(os.environ.get("LOAD_SHOP_ID", "123456")),
        "timestamp": int(time.time()),
        "data": {
            "ordersn": f"LOAD{int(time.time())}{random.randint(1000, 9999)}",
            "tracking_no": f"SPX{uuid.uuid4().hex[:12].upper()}",
            "logistics_status": "LOGISTICS_PICKUP_DONE",
        },
    }
    body = json.dumps(payload).encode()
    return body, sign(body, os.environ["LOAD_PARTNER_KEY"])
