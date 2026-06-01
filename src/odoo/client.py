"""Odoo XML-RPC client wrapped with circuit breaker. Runs blocking call in threadpool."""

from __future__ import annotations

import asyncio
import http.client
import xmlrpc.client
from decimal import ROUND_HALF_UP, Decimal
from functools import cached_property
from typing import TYPE_CHECKING, Any

from rapidfuzz import fuzz

from src.core.circuit_breaker import odoo_breaker
from src.core.config import settings
from src.core.exceptions import (
    OdooConnectionError,
    OdooError,
    OdooPermissionError,
    OdooValidationError,
    ProductNotFoundError,
)
from src.core.logging import get_logger
from src.core.utils import normalize_vn_phone

if TYPE_CHECKING:
    from src.schemas.unified import UnifiedAddress, UnifiedOrder, UnifiedOrderItem

logger = get_logger(__name__)


class _TimeoutTransport(xmlrpc.client.Transport):
    """xmlrpc.Transport that honors a socket-level timeout. The default
    Transport uses the global default timeout, which is `None`, so a
    stalled Odoo can hang worker threads indefinitely."""

    def __init__(self, timeout: float) -> None:
        super().__init__()
        self._timeout = timeout

    def make_connection(self, host):  # type: ignore[override]
        if self._connection and host == self._connection[0]:
            return self._connection[1]
        chost, self._extra_headers, _ = self.get_host_info(host)
        conn = http.client.HTTPConnection(chost, timeout=self._timeout)
        self._connection = host, conn
        return conn


class _SafeTimeoutTransport(_TimeoutTransport):
    def make_connection(self, host):  # type: ignore[override]
        if self._connection and host == self._connection[0]:
            return self._connection[1]
        chost, self._extra_headers, _ = self.get_host_info(host)
        conn = http.client.HTTPSConnection(chost, timeout=self._timeout)
        self._connection = host, conn
        return conn


def _make_transport(url: str, timeout: float) -> xmlrpc.client.Transport:
    return (
        _SafeTimeoutTransport(timeout) if url.startswith("https://") else _TimeoutTransport(timeout)
    )


class OdooClient:
    def __init__(self) -> None:
        self.url = settings.ODOO_URL
        self.db = settings.ODOO_DB
        self.user = settings.ODOO_USER
        self.password = settings.ODOO_PASSWORD
        self._uid: int | None = None

    @cached_property
    def _common(self) -> xmlrpc.client.ServerProxy:
        return xmlrpc.client.ServerProxy(
            f"{self.url}/xmlrpc/2/common",
            allow_none=True,
            transport=_make_transport(self.url, settings.ODOO_TIMEOUT),
        )

    @cached_property
    def _models(self) -> xmlrpc.client.ServerProxy:
        return xmlrpc.client.ServerProxy(
            f"{self.url}/xmlrpc/2/object",
            allow_none=True,
            transport=_make_transport(self.url, settings.ODOO_TIMEOUT),
        )

    @property
    def uid(self) -> int:
        if self._uid is None:
            try:
                self._uid = self._common.authenticate(self.db, self.user, self.password, {})
            except (TimeoutError, xmlrpc.client.Fault, ConnectionError, OSError) as e:
                raise OdooConnectionError(f"Auth failed: {e}") from e
            if not self._uid:
                raise OdooPermissionError("Odoo authentication returned no uid")
        return self._uid

    async def _execute(
        self, model: str, method: str, args: list, kwargs: dict | None = None
    ) -> Any:
        kwargs = kwargs or {}

        def _run() -> Any:
            try:
                return self._models.execute_kw(
                    self.db,
                    self.uid,
                    self.password,
                    model,
                    method,
                    args,
                    kwargs,
                )
            except xmlrpc.client.Fault as e:
                if "AccessError" in e.faultString:
                    raise OdooPermissionError(e.faultString) from e
                if "ValidationError" in e.faultString or "ValueError" in e.faultString:
                    raise OdooValidationError(e.faultString) from e
                raise OdooError(e.faultString) from e
            except (ConnectionError, OSError, xmlrpc.client.ProtocolError) as e:
                raise OdooConnectionError(str(e)) from e

        return await odoo_breaker.call(asyncio.to_thread, _run)

    async def search_read(
        self,
        model: str,
        domain: list,
        fields: list[str],
        limit: int | None = None,
    ) -> list[dict]:
        kw: dict[str, Any] = {"fields": fields}
        if limit is not None:
            kw["limit"] = limit
        return await self._execute(model, "search_read", [domain], kw)

    async def create(self, model: str, values: dict) -> int:
        if settings.MIDDLEWARE_DRY_RUN:
            logger.info("dry_run_create_skipped", model=model)
            return -1
        return await self._execute(model, "create", [values])

    async def write(self, model: str, ids: list[int], values: dict) -> bool:
        if settings.MIDDLEWARE_DRY_RUN:
            logger.info("dry_run_write_skipped", model=model, ids=ids, fields=list(values))
            return True
        return await self._execute(model, "write", [ids, values])

    async def call_method(self, model: str, method: str, ids: list[int], **kwargs: Any) -> Any:
        if settings.MIDDLEWARE_DRY_RUN:
            logger.info("dry_run_call_method_skipped", model=model, method=method, ids=ids)
            return True
        return await self._execute(model, method, [ids], kwargs)

    async def check_order_exists(self, platform: str, platform_order_id: str) -> int | None:
        result = await self.search_read(
            "sale.order",
            [["x_platform", "=", platform], ["x_platform_order_id", "=", platform_order_id]],
            ["id"],
            limit=1,
        )
        return result[0]["id"] if result else None

    async def update_order_tracking(
        self,
        platform: str,
        platform_order_id: str,
        tracking_number: str,
    ) -> bool:
        result = await self.search_read(
            "sale.order",
            [["x_platform", "=", platform], ["x_platform_order_id", "=", platform_order_id]],
            ["id", "x_tracking_number"],
            limit=1,
        )
        if not result:
            return False
        order_id = result[0]["id"]
        if result[0].get("x_tracking_number") == tracking_number:
            return True
        return await self.write(
            "sale.order",
            [order_id],
            {"x_tracking_number": tracking_number},
        )

    async def get_product_id_by_sku(self, sku: str) -> int | None:
        result = await self.search_read(
            "product.product",
            [["default_code", "=", sku]],
            ["id"],
            limit=1,
        )
        return result[0]["id"] if result else None

    async def get_stock_quantity(self, sku: str) -> int:
        result = await self.search_read(
            "product.product",
            [["default_code", "=", sku]],
            ["qty_available", "virtual_available", "id"],
            limit=1,
        )
        if not result:
            return 0
        return int(result[0]["virtual_available"] or 0)

    async def get_product_marketplace_controls(
        self,
        sku: str,
    ) -> dict[str, Any] | None:
        result = await self.search_read(
            "product.product",
            [["default_code", "=", sku]],
            [
                "id",
                "virtual_available",
                "lst_price",
                "x_marketplace_buffer_pct",
                "x_block_marketplace_sync",
            ],
            limit=1,
        )
        return result[0] if result else None

    async def get_product_price(self, sku: str) -> float:
        result = await self.search_read(
            "product.product",
            [["default_code", "=", sku]],
            ["lst_price"],
            limit=1,
        )
        return float(result[0]["lst_price"]) if result else 0.0

    async def _get_vietnam_id(self) -> int:
        res = await self.search_read("res.country", [["code", "=", "VN"]], ["id"], limit=1)
        if not res:
            raise OdooError("Vietnam country not found")
        return res[0]["id"]

    async def _get_pricelist_vnd(self) -> int:
        res = await self.search_read(
            "product.pricelist",
            [["currency_id.name", "=", "VND"]],
            ["id"],
            limit=1,
        )
        if not res:
            raise OdooError("VND pricelist not found")
        return res[0]["id"]

    async def get_or_create_partner(
        self,
        address: UnifiedAddress,
        platform: str,
        buyer_platform_id: str,
    ) -> int:
        phone = normalize_vn_phone(address.phone)
        if not phone:
            phone = f"unknown-{platform}-{buyer_platform_id}"

        candidates = await self.search_read(
            "res.partner",
            [["phone", "=", phone]],
            ["id", "name", "x_platform_source", "x_platform_buyer_id"],
            limit=10,
        )
        for c in candidates:
            if fuzz.ratio((c["name"] or "").lower(), address.full_name.lower()) > 80:
                # Backfill platform fields if not set
                if not c.get("x_platform_source"):
                    await self.write(
                        "res.partner",
                        [c["id"]],
                        {
                            "x_platform_source": platform,
                            "x_platform_buyer_id": buyer_platform_id,
                        },
                    )
                    logger.info(
                        "partner_platform_fields_backfilled",
                        partner_id=c["id"],
                        platform=platform,
                        buyer_id=buyer_platform_id,
                    )
                return c["id"]

        if candidates:
            logger.warning(
                "partner_phone_collision_create_new",
                phone=phone,
                existing=len(candidates),
                platform=platform,
            )
            name = f"{address.full_name} [{platform}:{buyer_platform_id[:8]}]"
        else:
            name = address.full_name

        country_id = await self._get_vietnam_id()
        return await self.create(
            "res.partner",
            {
                "name": name,
                "phone": phone,
                "street": address.address_line,
                "city": address.district,
                "comment": f"{address.ward or ''}, {address.district}, {address.province}",
                "country_id": country_id,
                "x_platform_source": platform,
                "x_platform_buyer_id": buyer_platform_id,
            },
        )

    async def create_sale_order(self, order: UnifiedOrder) -> tuple[int, str]:
        if settings.MIDDLEWARE_DRY_RUN:
            logger.info(
                "dry_run_create_sale_order_skipped",
                platform=order.platform.value,
                platform_order_id=order.platform_order_id,
            )
            return -1, "DRY-RUN"

        partner_id = await self.get_or_create_partner(
            order.shipping_address,
            order.platform.value,
            order.buyer_platform_id,
        )
        pricelist_id = await self._get_pricelist_vnd()
        lines = await self._build_order_lines(order.items, order.platform.value)

        so_vals = {
            "partner_id": partner_id,
            "partner_invoice_id": partner_id,
            "partner_shipping_id": partner_id,
            "pricelist_id": pricelist_id,
            "origin": f"{order.platform.value}/{order.platform_order_id}",
            "x_platform": order.platform.value,
            "x_platform_order_id": order.platform_order_id,
            "x_platform_order_sn": order.platform_order_sn or "",
            "x_sync_status": "synced",
            "note": self._build_order_note(order),
            "order_line": lines,
        }
        tracking = order.logistics.tracking_number if order.logistics else None
        if tracking:
            so_vals["x_tracking_number"] = tracking

        so_id = await self.create("sale.order", so_vals)
        so = await self.search_read("sale.order", [["id", "=", so_id]], ["name"], limit=1)

        if settings.ENABLE_AUTO_CONFIRM_ORDER:
            await self.call_method("sale.order", "action_confirm", [so_id])

        return so_id, so[0]["name"] if so else f"SO{so_id}"

    async def _build_order_lines(
        self,
        items: list[UnifiedOrderItem],
        platform: str | None = None,
    ) -> list:
        """Build sale.order.line list. Expands bundle SKUs into their
        configured component lines using ProductMapping; falls back to the
        platform SKU if no mapping is found (simple items).

        For bundles we duplicate the platform price/discount across all
        component lines proportionally to component quantity, so the SO
        total remains close to what the platform charged. Pricing nuance
        belongs in the buyer-facing breakdown; reconciliation cares about
        amount_total tolerance, which this preserves.
        """
        from src.services.mapping_service import MappingService

        lines: list = []
        for item in items:
            mapping = None
            if platform and item.platform_variant_id:
                # Shopee variant SKU is "item_id:model_id" in our mappings
                key = f"{item.platform_item_id}:{item.platform_variant_id}"
                mapping = await MappingService.get_by_platform_sku(platform, key)
            if mapping is None and platform and item.platform_item_id:
                mapping = await MappingService.get_by_platform_sku(
                    platform,
                    str(item.platform_item_id),
                )

            if mapping and mapping.get("mapping_type") == "bundle":
                comps = await MappingService.get_bundle_components(int(mapping["id"]))
                if not comps:
                    logger.warning(
                        "bundle_no_components_fallback_to_platform_sku",
                        platform=platform,
                        platform_sku=item.sku,
                        mapping_id=mapping.get("id"),
                    )
                    comps = [{"odoo_sku": mapping["odoo_sku"], "quantity": 1}]
                # Round 21 P2-21B: Decimal split with remainder-on-last-component.
                # Float division leaves residue per line; Odoo per-line currency
                # rounding then drifts amount_total → false reconciliation alerts.
                # Quantization unit = settings.CURRENCY_ROUNDING (default "1" for VND).
                currency_q = Decimal(str(getattr(settings, "CURRENCY_ROUNDING", "1")))
                total_units = sum(int(c["quantity"]) for c in comps) or 1
                line_subtotal = Decimal(str(item.discounted_price)) * Decimal(item.quantity)
                base_per_unit = (
                    Decimal(str(item.discounted_price)) / Decimal(total_units)
                ).quantize(currency_q, rounding=ROUND_HALF_UP)
                remaining_subtotal = line_subtotal
                last_idx = len(comps) - 1
                for idx, comp in enumerate(comps):
                    comp_sku = comp["odoo_sku"]
                    comp_qty_per_unit = int(comp["quantity"])
                    qty = comp_qty_per_unit * item.quantity
                    price_groups: list[tuple[int, Decimal]]
                    if idx == last_idx and qty > 0:
                        price_unit = (remaining_subtotal / Decimal(qty)).quantize(
                            currency_q, rounding=ROUND_HALF_UP
                        )
                        drift = abs(price_unit * Decimal(qty) - remaining_subtotal)
                        if drift > currency_q and qty > 1:
                            low_unit = price_unit
                            if low_unit * Decimal(qty) > remaining_subtotal:
                                low_unit -= currency_q
                            high_unit = low_unit + currency_q
                            high_qty = int(
                                (
                                    (remaining_subtotal - low_unit * Decimal(qty)) / currency_q
                                ).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
                            )
                            high_qty = max(0, min(qty, high_qty))
                            price_groups = [
                                (qty - high_qty, low_unit),
                                (high_qty, high_unit),
                            ]
                        else:
                            price_groups = [(qty, price_unit)]
                    else:
                        price_unit = base_per_unit
                        remaining_subtotal -= price_unit * Decimal(qty)
                        price_groups = [(qty, price_unit)]
                    product_id = await self.get_product_id_by_sku(comp_sku)
                    if not product_id:
                        raise ProductNotFoundError(comp_sku)
                    for line_qty, line_price_unit in price_groups:
                        if line_qty <= 0:
                            continue
                        lines.append(
                            (
                                0,
                                0,
                                {
                                    "product_id": product_id,
                                    "name": (
                                        f"{item.product_name} - {item.variant_name or ''}"
                                        f" [bundle:{comp_sku}]"
                                    ).strip(" -"),
                                    "product_uom_qty": line_qty,
                                    "price_unit": float(line_price_unit),
                                    "discount": 0,
                                },
                            )
                        )
                continue

            # Simple mapping (or no mapping found): use Odoo SKU from
            # mapping if available, else fall back to the platform SKU.
            target_sku = mapping["odoo_sku"] if mapping else item.sku
            product_id = await self.get_product_id_by_sku(target_sku)
            if not product_id:
                raise ProductNotFoundError(target_sku)
            lines.append(
                (
                    0,
                    0,
                    {
                        "product_id": product_id,
                        "name": f"{item.product_name} - {item.variant_name or ''}".strip(" -"),
                        "product_uom_qty": item.quantity,
                        "price_unit": float(item.discounted_price),
                        "discount": 0,
                    },
                )
            )
        return lines

    @staticmethod
    def _build_order_note(order: UnifiedOrder) -> str:
        parts = [
            f"Platform: {order.platform.value}",
            f"Order: {order.platform_order_id}",
        ]
        if order.logistics and order.logistics.tracking_number:
            parts.append(f"Tracking: {order.logistics.tracking_number}")
        return " · ".join(parts)
