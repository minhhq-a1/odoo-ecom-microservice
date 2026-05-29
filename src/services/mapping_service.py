"""Mapping service — cache product_mapping in Redis."""
from __future__ import annotations

import json

from sqlalchemy import select

from src.core.database import get_async_db_context
from src.core.logging import get_logger
from src.core.redis import get_redis
from src.models.product_mapping import ProductBundleComponent, ProductMapping

logger = get_logger(__name__)

CACHE_TTL = 3600  # 1h


def _k_mapping(platform: str, platform_sku_id: str) -> str:
    return f"mapping:platform_sku:{platform}:{platform_sku_id}"


def _k_reverse(platform: str, odoo_sku: str) -> str:
    return f"mapping:odoo_sku:{platform}:{odoo_sku}"


def _k_bundle_components(mapping_id: int) -> str:
    return f"mapping:bundle:{mapping_id}"


class MappingService:
    @classmethod
    async def get_by_platform_sku(
        cls, platform: str, platform_sku_id: str,
    ) -> dict | None:
        r = await get_redis()
        cached = await r.get(_k_mapping(platform, platform_sku_id))
        if cached:
            return json.loads(cached)

        async with get_async_db_context() as db:
            m = (await db.execute(
                select(ProductMapping).where(
                    ProductMapping.platform == platform,
                    ProductMapping.platform_sku_id == platform_sku_id,
                    ProductMapping.is_active.is_(True),
                ),
            )).scalar_one_or_none()
        if m is None:
            return None
        data = {
            "id": m.id,
            "platform_product_id": m.platform_product_id,
            "platform_sku_id": m.platform_sku_id,
            "odoo_product_id": m.odoo_product_id,
            "odoo_sku": m.odoo_sku,
            "mapping_type": m.mapping_type,
        }
        await r.setex(_k_mapping(platform, platform_sku_id), CACHE_TTL, json.dumps(data))
        return data

    @classmethod
    async def list_active_for_platform(cls, platform: str) -> list[dict]:
        async with get_async_db_context() as db:
            rows = (await db.execute(
                select(ProductMapping).where(
                    ProductMapping.platform == platform,
                    ProductMapping.is_active.is_(True),
                ),
            )).scalars().all()
        return [
            {
                "id": r.id,
                "platform_product_id": r.platform_product_id,
                "platform_sku_id": r.platform_sku_id,
                "odoo_product_id": r.odoo_product_id,
                "odoo_sku": r.odoo_sku,
                "mapping_type": r.mapping_type,
            }
            for r in rows
        ]

    @classmethod
    async def get_bundle_components(cls, mapping_id: int) -> list[dict]:
        r = await get_redis()
        cached = await r.get(_k_bundle_components(mapping_id))
        if cached:
            return json.loads(cached)

        async with get_async_db_context() as db:
            comps = (await db.execute(
                select(ProductBundleComponent).where(
                    ProductBundleComponent.mapping_id == mapping_id,
                ),
            )).scalars().all()
        data = [{"odoo_sku": c.odoo_sku, "quantity": c.quantity} for c in comps]
        await r.setex(_k_bundle_components(mapping_id), CACHE_TTL, json.dumps(data))
        return data

    @classmethod
    async def find_mappings_using_component(
        cls, platform: str, component_sku: str,
    ) -> list[dict]:
        """Reverse lookup — return mappings where odoo_sku == component_sku OR
        any bundle component uses it. Used for stock recalc invalidation."""
        async with get_async_db_context() as db:
            direct = (await db.execute(
                select(ProductMapping).where(
                    ProductMapping.platform == platform,
                    ProductMapping.odoo_sku == component_sku,
                    ProductMapping.is_active.is_(True),
                ),
            )).scalars().all()

            comp_rows = (await db.execute(
                select(ProductMapping)
                .join(ProductBundleComponent,
                      ProductBundleComponent.mapping_id == ProductMapping.id)
                .where(
                    ProductMapping.platform == platform,
                    ProductMapping.is_active.is_(True),
                    ProductBundleComponent.odoo_sku == component_sku,
                ),
            )).scalars().all()

        seen: set[int] = set()
        result: list[dict] = []
        for m in [*direct, *comp_rows]:
            if m.id in seen:
                continue
            seen.add(m.id)
            result.append({
                "id": m.id,
                "platform_product_id": m.platform_product_id,
                "platform_sku_id": m.platform_sku_id,
                "odoo_sku": m.odoo_sku,
                "mapping_type": m.mapping_type,
            })
        return result

    @classmethod
    async def invalidate(cls, platform: str, platform_sku_id: str) -> None:
        r = await get_redis()
        await r.delete(_k_mapping(platform, platform_sku_id))
