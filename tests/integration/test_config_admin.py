"""Integration tests for config_admin router."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from src.api.main import app


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture
def admin_token() -> str:
    return "test_admin_token"


@pytest.fixture
def mock_admin_auth():  # noqa: PT004
    """Mock admin authentication."""
    with patch("src.api.dependencies.require_admin_token", return_value="admin"):
        yield


@pytest.fixture
def mock_csrf():  # noqa: PT004
    """Mock CSRF verification."""
    with patch("src.api.dependencies.verify_csrf", return_value=None):
        yield


@pytest.mark.usefixtures("mock_admin_auth", "mock_csrf")
def test_platform_save_creates_new(client: TestClient) -> None:
    """POST /admin/config/platforms should create new platform config."""
    with patch("src.api.routers.config_admin.get_async_db") as mock_db:
        mock_session = AsyncMock()
        mock_result = AsyncMock()
        mock_result.scalar_one_or_none = AsyncMock(return_value=None)
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session.add = AsyncMock()
        mock_session.commit = AsyncMock()
        mock_db.return_value = mock_session

        response = client.post(
            "/admin/config/platforms",
            data={
                "platform": "shopee",
                "shop_id": "123456",
                "is_active": "on",
                "dry_run": "",
                "price_master": "platform",
            },
            cookies={"admin_token": "test"},
        )

    assert response.status_code == 303
    assert "/admin/config/platforms" in response.headers["location"]


@pytest.mark.usefixtures("mock_admin_auth", "mock_csrf")
def test_platform_save_updates_existing(client: TestClient) -> None:
    """POST /admin/config/platforms should update existing platform config."""
    from src.models.platform_config import PlatformConfig

    existing = PlatformConfig(platform="shopee", credentials={}, is_active=False)

    with patch("src.api.routers.config_admin.get_async_db") as mock_db:
        mock_session = AsyncMock()
        mock_result = AsyncMock()
        mock_result.scalar_one_or_none = AsyncMock(return_value=existing)
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session.commit = AsyncMock()
        mock_db.return_value = mock_session

        response = client.post(
            "/admin/config/platforms",
            data={
                "platform": "shopee",
                "shop_id": "789",
                "is_active": "on",
                "dry_run": "on",
                "price_master": "odoo",
            },
            cookies={"admin_token": "test"},
        )

    assert response.status_code == 303
    assert existing.is_active is True
    assert existing.dry_run is True
    assert existing.price_master == "odoo"


@pytest.mark.usefixtures("mock_admin_auth", "mock_csrf")
def test_platform_delete_removes(client: TestClient) -> None:
    """POST /admin/config/platforms/{platform}/delete should remove platform."""
    from src.models.platform_config import PlatformConfig

    existing = PlatformConfig(platform="shopee", credentials={})

    with patch("src.api.routers.config_admin.get_async_db") as mock_db:
        mock_session = AsyncMock()
        mock_result = AsyncMock()
        mock_result.scalar_one_or_none = AsyncMock(return_value=existing)
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session.delete = AsyncMock()
        mock_session.commit = AsyncMock()
        mock_db.return_value = mock_session

        response = client.post(
            "/admin/config/platforms/shopee/delete",
            cookies={"admin_token": "test"},
        )

    assert response.status_code == 303
    mock_session.delete.assert_called_once_with(existing)


@pytest.mark.usefixtures("mock_admin_auth", "mock_csrf")
def test_product_save_with_bundle(client: TestClient) -> None:
    """POST /admin/config/products should save bundle with components."""
    from src.models.product_mapping import ProductMapping

    with (
        patch("src.api.routers.config_admin.get_async_db") as mock_db,
        patch("src.api.routers.config_admin.get_redis") as mock_redis,
    ):
        mock_session = AsyncMock()
        new_mapping = ProductMapping()
        new_mapping.id = 1
        mock_session.add = AsyncMock()
        mock_session.flush = AsyncMock()
        mock_session.execute = AsyncMock(
            return_value=AsyncMock(
                scalars=AsyncMock(return_value=AsyncMock(all=AsyncMock(return_value=[])))
            )
        )
        mock_session.commit = AsyncMock()
        mock_db.return_value = mock_session

        # Mock get to return new_mapping after add
        mock_db.return_value.add.side_effect = lambda obj: setattr(obj, "id", 1)

        mock_redis_instance = AsyncMock()
        mock_redis_instance.delete = AsyncMock()
        mock_redis.return_value = mock_redis_instance

        response = client.post(
            "/admin/config/products",
            data={
                "mapping_id": "",
                "platform": "shopee",
                "platform_product_id": "PROD-001",
                "platform_sku_id": "BUNDLE-001",
                "odoo_product_id": "100",
                "odoo_sku": "BUNDLE-SKU",
                "mapping_type": "bundle",
                "is_active": "on",
                "components_sku": ["COMP-A", "COMP-B"],
                "components_qty": ["2", "1"],
            },
            cookies={"admin_token": "test"},
        )

    assert response.status_code == 303


@pytest.mark.usefixtures("mock_admin_auth", "mock_csrf")
def test_product_delete_invalidates_cache(client: TestClient) -> None:
    """POST /admin/config/products/{id}/delete should invalidate cache."""
    from src.models.product_mapping import ProductMapping

    existing = ProductMapping(
        id=1,
        platform="shopee",
        platform_sku_id="SKU-001",
        odoo_sku="ODOO-001",
        mapping_type="simple",
    )

    with (
        patch("src.api.routers.config_admin.get_async_db") as mock_db,
        patch("src.api.routers.config_admin.get_redis") as mock_redis,
        patch("src.api.routers.config_admin.MappingService") as mock_mapping_svc,
    ):
        mock_session = AsyncMock()
        mock_session.get = AsyncMock(return_value=existing)
        mock_session.delete = AsyncMock()
        mock_session.commit = AsyncMock()
        mock_db.return_value = mock_session

        mock_redis_instance = AsyncMock()
        mock_redis_instance.delete = AsyncMock()
        mock_redis.return_value = mock_redis_instance
        mock_mapping_svc.invalidate = AsyncMock()

        response = client.post(
            "/admin/config/products/1/delete",
            cookies={"admin_token": "test"},
        )

    assert response.status_code == 303
    mock_mapping_svc.invalidate.assert_called_once_with("shopee", "SKU-001")


@pytest.mark.usefixtures("mock_admin_auth", "mock_csrf")
def test_stock_save_updates_allocation(client: TestClient) -> None:
    """POST /admin/config/stock should update stock allocation config."""
    with patch("src.api.routers.config_admin.get_async_db") as mock_db:
        mock_session = AsyncMock()
        mock_session.get = AsyncMock(return_value=None)
        mock_session.add = AsyncMock()
        mock_session.commit = AsyncMock()
        mock_db.return_value = mock_session

        response = client.post(
            "/admin/config/stock",
            data={
                "cfg_id": "",
                "odoo_sku": "SKU-001",
                "platform": "shopee",
                "allocation_pct": "70.0",
                "buffer_pct": "15.0",
                "is_active": "on",
            },
            cookies={"admin_token": "test"},
        )

    assert response.status_code == 303


@pytest.mark.usefixtures("mock_admin_auth", "mock_csrf")
def test_stock_save_validates_percentages(client: TestClient) -> None:
    """POST /admin/config/stock should validate percentage ranges."""
    response = client.post(
        "/admin/config/stock",
        data={
            "cfg_id": "",
            "odoo_sku": "SKU-001",
            "platform": "shopee",
            "allocation_pct": "150.0",  # Invalid
            "buffer_pct": "15.0",
            "is_active": "on",
        },
        cookies={"admin_token": "test"},
    )

    assert response.status_code == 400


@pytest.mark.usefixtures("mock_admin_auth", "mock_csrf")
def test_platform_save_validates_platform_name(client: TestClient) -> None:
    """POST /admin/config/platforms should validate platform name."""
    response = client.post(
        "/admin/config/platforms",
        data={
            "platform": "invalid_platform",
            "shop_id": "123",
            "is_active": "on",
            "price_master": "platform",
        },
        cookies={"admin_token": "test"},
    )

    assert response.status_code == 400
