"""Stock service — buffer + allocation + bundle expansion."""
from __future__ import annotations

from sqlalchemy import select

from src.core.config import settings
from src.core.database import get_async_db_context
from src.core.logging import get_logger
from src.models.product_mapping import ProductBundleComponent, ProductMapping
from src.models.stock_config import StockAllocationConfig
from src.odoo.client import OdooClient

logger = get_logger(__name__)


class StockService:
    def __init__(self, odoo: OdooClient | None = None) -> None:
        self.odoo = odoo or OdooClient()

    async def calculate_platform_stock(self, odoo_sku: str, platform: str) -> int:
        """Apply buffer + allocation. Never returns negative."""
        odoo_stock = await self.odoo.get_stock_quantity(odoo_sku)
        async with get_async_db_context() as db:
            cfg_result = await db.execute(
                select(StockAllocationConfig).where(
                    StockAllocationConfig.odoo_sku == odoo_sku,
                    StockAllocationConfig.platform == platform,
                    StockAllocationConfig.is_active.is_(True),
                ),
            )
            cfg = cfg_result.scalar_one_or_none()

        buffer_pct = float(cfg.buffer_pct) if cfg else float(settings.DEFAULT_STOCK_BUFFER_PCT)
        allocation_pct = (
            float(cfg.allocation_pct) if cfg
            else float(settings.DEFAULT_SHOPEE_ALLOCATION_PCT)
        )

        buffered = int(odoo_stock * (1 - buffer_pct / 100))
        allocated = int(buffered * (allocation_pct / 100))
        return max(0, allocated)

    async def calculate_bundle_stock(self, platform_sku: str, platform: str) -> int:
        """Bundle stock = min(component_allocated // component_qty)."""
        async with get_async_db_context() as db:
            mapping = (await db.execute(
                select(ProductMapping).where(
                    ProductMapping.platform == platform,
                    ProductMapping.platform_sku_id == platform_sku,
                    ProductMapping.is_active.is_(True),
                ),
            )).scalar_one_or_none()
            if mapping is None:
                return 0
            if mapping.mapping_type == "simple":
                return await self.calculate_platform_stock(mapping.odoo_sku, platform)

            comps = (await db.execute(
                select(ProductBundleComponent).where(
                    ProductBundleComponent.mapping_id == mapping.id,
                ),
            )).scalars().all()

        if not comps:
            return 0

        bundle_qtys = []
        for c in comps:
            allocated = await self.calculate_platform_stock(c.odoo_sku, platform)
            bundle_qtys.append(allocated // max(1, c.quantity))
        return max(0, min(bundle_qtys))
