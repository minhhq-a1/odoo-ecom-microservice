"""Shopee connector — async httpx client with auto token refresh + circuit breaker."""
from __future__ import annotations

from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Any

import httpx

from src.connectors.base import BaseConnector
from src.connectors.shopee import auth
from src.connectors.shopee.signing import sign_request
from src.core.circuit_breaker import shopee_breaker
from src.core.config import settings
from src.core.exceptions import (
    ShopeeAuthError,
    ShopeeError,
    ShopeeRateLimitError,
)
from src.core.logging import get_logger

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from src.schemas.unified import (
        StockUpdateRequest,
        UnifiedOrder,
    )

logger = get_logger(__name__)

_ORDER_DETAIL_FIELDS = (
    "buyer_user_id,buyer_username,estimated_shipping_fee,"
    "recipient_address,actual_shipping_fee,item_list,pay_time,"
    "dropshipper,dropshipper_phone,invoice_data,"
    "order_status,update_time"
)


class ShopeeConnector(BaseConnector):
    platform = "shopee"

    def __init__(self, shop_id: str | None = None) -> None:
        self.shop_id = shop_id or settings.SHOPEE_SHOP_ID
        self.partner_id = settings.SHOPEE_PARTNER_ID
        self.partner_key = settings.SHOPEE_PARTNER_KEY
        self.base = settings.shopee_base_url
        self._client = httpx.AsyncClient(timeout=30, http2=True)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> ShopeeConnector:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def _request(
        self, method: str, api_path: str, *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        with_auth: bool = True,
    ) -> dict[str, Any]:
        async def _do() -> dict[str, Any]:
            access = await auth.get_access_token(self.shop_id) if with_auth else ""
            sign, ts = sign_request(
                self.partner_id, api_path, self.partner_key,
                access_token=access, shop_id=self.shop_id if with_auth else "",
            )
            qp: dict[str, Any] = {
                "partner_id": self.partner_id,
                "timestamp": ts,
                "sign": sign,
            }
            if with_auth:
                qp["access_token"] = access
                qp["shop_id"] = self.shop_id
            if params:
                qp.update(params)

            url = f"{self.base[:-7]}{api_path}"
            resp = await self._client.request(method, url, params=qp, json=json_body)
            if resp.status_code == 429:
                retry_after = int(resp.headers.get("Retry-After", 60))
                raise ShopeeRateLimitError(retry_after=retry_after)
            data = resp.json()
            err = data.get("error")
            if err:
                if err in ("error_auth", "error_auth_token_expired"):
                    raise ShopeeAuthError("shopee", data.get("message", err), data=data)
                if err == "error_api_ratelimitation":
                    raise ShopeeRateLimitError(retry_after=60)
                raise ShopeeError("shopee", data.get("message", err), code=err, data=data)
            return data

        try:
            return await shopee_breaker.call(_do)
        except ShopeeAuthError:
            # Force a refresh so the retry signs with a new access token.
            # Drop the cached entry first: if refresh() detects another
            # worker already holds the lock and falls back to reading from
            # Redis, we don't want it to return the same stale token we
            # just got rejected with.
            from src.core.redis import get_redis
            r = await get_redis()
            await r.delete(f"shopee:token:{self.shop_id}:access")
            await auth.refresh(self.shop_id)
            return await shopee_breaker.call(_do)

    async def get_order_detail(self, platform_order_id: str) -> UnifiedOrder:
        data = await self._request(
            "GET", "/api/v2/order/get_order_detail",
            params={
                "order_sn_list": platform_order_id,
                "response_optional_fields": _ORDER_DETAIL_FIELDS,
            },
        )
        orders = data["response"]["order_list"]
        if not orders:
            raise ShopeeError("shopee", f"Order {platform_order_id} not found")
        from src.transformers.shopee import ShopeeTransformer
        return ShopeeTransformer().transform(orders[0])

    async def get_orders_by_date(self, day: date) -> AsyncIterator[UnifiedOrder]:
        from src.transformers.shopee import ShopeeTransformer
        t = ShopeeTransformer()
        start_dt = datetime.combine(day, datetime.min.time(), tzinfo=UTC)
        end_dt = datetime.combine(day, datetime.max.time(), tzinfo=UTC)
        cursor = ""
        while True:
            data = await self._request(
                "GET", "/api/v2/order/get_order_list",
                params={
                    "time_range_field": "create_time",
                    "time_from": int(start_dt.timestamp()),
                    "time_to": int(end_dt.timestamp()),
                    "page_size": 100,
                    "cursor": cursor,
                },
            )
            response = data["response"]
            sn_list = [o["order_sn"] for o in response.get("order_list", [])]
            if sn_list:
                detail = await self._request(
                    "GET", "/api/v2/order/get_order_detail",
                    params={
                        "order_sn_list": ",".join(sn_list),
                        "response_optional_fields": _ORDER_DETAIL_FIELDS,
                    },
                )
                for raw in detail["response"]["order_list"]:
                    yield t.transform(raw)
            if not response.get("more"):
                break
            cursor = response.get("next_cursor", "")

    async def update_stock(self, request: StockUpdateRequest) -> None:
        """
        Update Shopee stock. Looks up item_id/model_id via ProductMapping.
        platform_sku_id format: "item_id" (simple) or "item_id:model_id" (variant).
        """
        from src.services.mapping_service import MappingService
        mapping = await MappingService.get_by_platform_sku("shopee", request.sku)
        if mapping is None:
            raise ShopeeError(
                "shopee", f"No product_mapping for platform_sku={request.sku}",
            )

        pid_raw = mapping["platform_product_id"]
        platform_sku = mapping["platform_sku_id"] or ""
        try:
            item_id = int(pid_raw)
        except (TypeError, ValueError) as e:
            raise ShopeeError(
                "shopee", f"Invalid platform_product_id={pid_raw}",
            ) from e

        model_id: int = 0
        if ":" in platform_sku:
            try:
                model_id = int(platform_sku.split(":", 1)[1])
            except (TypeError, ValueError):
                model_id = 0

        body = {
            "item_id": item_id,
            "stock_list": [{
                "model_id": model_id,
                "normal_stock": max(0, int(request.quantity)),
            }],
        }
        try:
            await self._request(
                "POST", "/api/v2/product/update_stock", json_body=body,
            )
        except ShopeeError as e:
            code = getattr(e, "context", {}).get("code") or ""
            if code == "error_item_is_on_flash_sale":
                logger.warning(
                    "shopee_stock_skip_flash_sale",
                    item_id=item_id, model_id=model_id, qty=request.quantity,
                )
                return
            raise

    async def update_price(self, platform_sku: str, price: float) -> None:
        """Push price to Shopee. `platform_sku` = "item_id" (simple) or
        "item_id:model_id" (variant), matching update_stock format."""
        try:
            item_id_raw, _, model_raw = platform_sku.partition(":")
            item_id = int(item_id_raw)
            model_id = int(model_raw) if model_raw else 0
        except (TypeError, ValueError) as e:
            raise ShopeeError(
                "shopee", f"Invalid platform_sku for price update: {platform_sku!r}",
            ) from e

        body = {
            "item_id": item_id,
            "price_list": [{
                "model_id": model_id,
                "original_price": float(price),
            }],
        }
        try:
            await self._request(
                "POST", "/api/v2/product/update_price", json_body=body,
            )
        except ShopeeError as e:
            code = getattr(e, "context", {}).get("code") or ""
            if code == "error_item_is_on_flash_sale":
                logger.warning(
                    "shopee_price_skip_flash_sale",
                    item_id=item_id, model_id=model_id, price=price,
                )
                return
            raise

    async def confirm_shipment(self, platform_order_id: str, tracking_no: str) -> None:
        await self._request(
            "POST", "/api/v2/logistics/ship_order",
            json_body={
                "order_sn": platform_order_id,
                "pickup": {"address_id": 0, "pickup_time_id": "", "tracking_number": tracking_no},
            },
        )
