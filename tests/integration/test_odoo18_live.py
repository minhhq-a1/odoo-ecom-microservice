"""
Live Odoo 18 connectivity smoke test.
Skipped automatically if ODOO_LIVE_URL not set.

Run:
  ODOO_LIVE_URL=http://127.0.0.1:8180 \
  ODOO_LIVE_DB=odoo18c-dev-test \
  ODOO_LIVE_USER=admin ODOO_LIVE_PASSWORD=admin \
  python3 -m pytest tests/integration/test_odoo18_live.py -v
"""

from __future__ import annotations

import os
import xmlrpc.client

import pytest

ODOO_URL = os.environ.get("ODOO_LIVE_URL")
ODOO_DB = os.environ.get("ODOO_LIVE_DB")
ODOO_USER = os.environ.get("ODOO_LIVE_USER", "admin")
ODOO_PASSWORD = os.environ.get("ODOO_LIVE_PASSWORD", "admin")

pytestmark = pytest.mark.skipif(
    not (ODOO_URL and ODOO_DB),
    reason="ODOO_LIVE_URL + ODOO_LIVE_DB required",
)


@pytest.fixture(scope="module")
def authenticated() -> tuple[int, xmlrpc.client.ServerProxy]:
    common = xmlrpc.client.ServerProxy(f"{ODOO_URL}/xmlrpc/2/common", allow_none=True)
    uid = common.authenticate(ODOO_DB, ODOO_USER, ODOO_PASSWORD, {})
    assert uid, "auth failed"
    models = xmlrpc.client.ServerProxy(f"{ODOO_URL}/xmlrpc/2/object", allow_none=True)
    return uid, models


def test_version_is_odoo_18() -> None:
    common = xmlrpc.client.ServerProxy(f"{ODOO_URL}/xmlrpc/2/common", allow_none=True)
    info = common.version()
    series = info.get("server_serie", "")
    assert series.startswith("18."), f"expected Odoo 18, got {series}"


def test_can_read_res_partner(authenticated) -> None:
    uid, models = authenticated
    count = models.execute_kw(
        ODOO_DB,
        uid,
        ODOO_PASSWORD,
        "res.partner",
        "search_count",
        [[]],
    )
    assert count >= 0


def test_vietnam_country_exists(authenticated) -> None:
    uid, models = authenticated
    rows = models.execute_kw(
        ODOO_DB,
        uid,
        ODOO_PASSWORD,
        "res.country",
        "search_read",
        [[["code", "=", "VN"]]],
        {"fields": ["id", "name"], "limit": 1},
    )
    assert rows, "Vietnam country must exist in fresh Odoo 18 install"
    assert rows[0]["name"] in ("Vietnam", "Việt Nam")


def test_xmlrpc_paths_stable_on_odoo_18(authenticated) -> None:
    """Validates src/odoo/client.py paths still work on Odoo 18."""
    uid, models = authenticated
    res = models.execute_kw(
        ODOO_DB,
        uid,
        ODOO_PASSWORD,
        "ir.model.fields",
        "search_count",
        [[["model", "=", "res.partner"]]],
    )
    assert res > 0
