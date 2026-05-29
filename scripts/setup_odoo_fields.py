"""Setup Odoo custom fields via XML-RPC."""

from __future__ import annotations

import xmlrpc.client

from src.core.config import settings

FIELDS = [
    {
        "model": "sale.order",
        "name": "x_platform",
        "field_description": "E-Commerce Platform",
        "ttype": "selection",
        "selection": "[('shopee','Shopee'),('lazada','Lazada'),('tiktok','TikTok Shop')]",
    },
    {
        "model": "sale.order",
        "name": "x_platform_order_id",
        "field_description": "Platform Order ID",
        "ttype": "char",
        "index": True,
    },
    {
        "model": "sale.order",
        "name": "x_platform_order_sn",
        "field_description": "Platform Order SN",
        "ttype": "char",
    },
    {
        "model": "sale.order",
        "name": "x_sync_status",
        "field_description": "Sync Status",
        "ttype": "selection",
        "selection": "[('pending','Pending'),('synced','Synced'),('error','Error')]",
    },
    {
        "model": "sale.order",
        "name": "x_tracking_number",
        "field_description": "Tracking Number",
        "ttype": "char",
    },
]


def main() -> None:
    common = xmlrpc.client.ServerProxy(f"{settings.ODOO_URL}/xmlrpc/2/common")
    uid = common.authenticate(settings.ODOO_DB, settings.ODOO_USER, settings.ODOO_PASSWORD, {})
    models = xmlrpc.client.ServerProxy(f"{settings.ODOO_URL}/xmlrpc/2/object")

    for f in FIELDS:
        model_id = models.execute_kw(
            settings.ODOO_DB,
            uid,
            settings.ODOO_PASSWORD,
            "ir.model",
            "search",
            [[["model", "=", f["model"]]]],
        )
        if not model_id:
            print(f"Model {f['model']} not found")
            continue

        existing = models.execute_kw(
            settings.ODOO_DB,
            uid,
            settings.ODOO_PASSWORD,
            "ir.model.fields",
            "search",
            [[["name", "=", f["name"]], ["model", "=", f["model"]]]],
        )
        if existing:
            print(f"Skip existing: {f['model']}.{f['name']}")
            continue

        models.execute_kw(
            settings.ODOO_DB,
            uid,
            settings.ODOO_PASSWORD,
            "ir.model.fields",
            "create",
            [{"model_id": model_id[0], **f}],
        )
        print(f"Created: {f['model']}.{f['name']}")


if __name__ == "__main__":
    main()
