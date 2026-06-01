"""Integration tests for config_admin router."""

from __future__ import annotations

from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastapi.testclient import TestClient

from src.api.dependencies import require_admin_token, verify_csrf
from src.api.main import app


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture
def admin_token() -> str:
    return "test_admin_token"


@pytest.fixture
def mock_db_session():
    """Create a mock database session."""
    mock_session = AsyncMock()

    # Setup default mock result for execute
    # The chain is: await db.execute() -> result.scalars() -> scalars.all()
    # Only execute() is async, scalars() and all() are sync
    mock_scalars = Mock()  # Not async
    mock_scalars.all = Mock(return_value=[])

    mock_result = Mock()  # Not async
    mock_result.scalars = Mock(return_value=mock_scalars)
    mock_result.scalar_one_or_none = Mock(return_value=None)

    mock_session.execute = AsyncMock(return_value=mock_result)
    mock_session.add = Mock()  # Not async
    mock_session.delete = AsyncMock()  # This IS async
    mock_session.commit = AsyncMock()
    mock_session.flush = AsyncMock()
    mock_session.get = AsyncMock(return_value=None)

    return mock_session


@pytest.fixture
def mock_admin_auth(mock_db_session):
    """Mock admin authentication and database using dependency_overrides."""
    from src.api.dependencies import get_async_db

    async def mock_get_db():
        yield mock_db_session

    app.dependency_overrides[require_admin_token] = lambda: "admin"
    app.dependency_overrides[verify_csrf] = lambda: None
    app.dependency_overrides[get_async_db] = mock_get_db
    yield mock_db_session
    app.dependency_overrides.clear()


def test_platform_save_creates_new(client: TestClient, mock_admin_auth: AsyncMock) -> None:
    """POST /admin/config/platforms should create new platform config."""
    # Setup mock to return None (no existing platform)
    mock_scalars = Mock()
    mock_scalars.all = Mock(return_value=[])
    mock_result = Mock()
    mock_result.scalars = Mock(return_value=mock_scalars)
    mock_result.scalar_one_or_none = Mock(return_value=None)
    mock_admin_auth.execute = AsyncMock(return_value=mock_result)

    response = client.post(
        "/admin/config/platforms",
        data={
            "platform": "shopee",
            "shop_id": "123456",
            "is_active": "on",
            "dry_run": "",
            "price_master": "platform",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert "/admin/config/platforms" in response.headers["location"]


def test_platform_save_updates_existing(client: TestClient, mock_admin_auth: AsyncMock) -> None:
    """POST /admin/config/platforms should update existing platform config."""
    from src.models.platform_config import PlatformConfig

    existing = PlatformConfig(platform="shopee", credentials={}, is_active=False)

    # Setup mock to return existing platform
    mock_scalars = Mock()
    mock_scalars.all = Mock(return_value=[])
    mock_result = Mock()
    mock_result.scalars = Mock(return_value=mock_scalars)
    mock_result.scalar_one_or_none = Mock(return_value=existing)
    mock_admin_auth.execute = AsyncMock(return_value=mock_result)

    response = client.post(
        "/admin/config/platforms",
        data={
            "platform": "shopee",
            "shop_id": "789",
            "is_active": "on",
            "dry_run": "on",
            "price_master": "odoo",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert existing.is_active is True
    assert existing.dry_run is True
    assert existing.price_master == "odoo"


def test_platform_delete_removes(client: TestClient, mock_admin_auth: AsyncMock) -> None:
    """POST /admin/config/platforms/{platform}/delete should remove platform."""
    from src.models.platform_config import PlatformConfig

    existing = PlatformConfig(platform="shopee", credentials={})

    # Setup mock to return existing platform
    mock_scalars = Mock()
    mock_scalars.all = Mock(return_value=[])
    mock_result = Mock()
    mock_result.scalars = Mock(return_value=mock_scalars)
    mock_result.scalar_one_or_none = Mock(return_value=existing)
    mock_admin_auth.execute = AsyncMock(return_value=mock_result)

    response = client.post(
        "/admin/config/platforms/shopee/delete",
        follow_redirects=False,
    )

    assert response.status_code == 303
    mock_admin_auth.delete.assert_called_once_with(existing)


def test_product_save_with_bundle(client: TestClient, mock_admin_auth: AsyncMock) -> None:
    """POST /admin/config/products should save bundle with components."""
    from src.models.product_mapping import ProductMapping

    new_mapping = ProductMapping()
    new_mapping.id = 1

    # Setup mock for execute (returns empty list for component query)
    mock_scalars = Mock()
    mock_scalars.all = Mock(return_value=[])
    mock_result = Mock()
    mock_result.scalars = Mock(return_value=mock_scalars)
    mock_admin_auth.execute = AsyncMock(return_value=mock_result)

    # Setup mock for add (sets id on object)
    mock_admin_auth.add.side_effect = lambda obj: setattr(obj, "id", 1)

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
        follow_redirects=False,
    )

    assert response.status_code == 303


def test_product_delete_invalidates_cache(client: TestClient, mock_admin_auth: AsyncMock) -> None:
    """POST /admin/config/products/{id}/delete should invalidate cache."""
    from src.models.product_mapping import ProductMapping

    existing = ProductMapping(
        id=1,
        platform="shopee",
        platform_sku_id="SKU-001",
        odoo_sku="ODOO-001",
        mapping_type="simple",
    )

    mock_admin_auth.get = AsyncMock(return_value=existing)

    with patch("src.services.mapping_service.MappingService.invalidate") as mock_invalidate:
        response = client.post(
            "/admin/config/products/1/delete",
            follow_redirects=False,
        )

    assert response.status_code == 303
    mock_invalidate.assert_called_once_with("shopee", "SKU-001")


def test_stock_save_updates_allocation(client: TestClient, mock_admin_auth: AsyncMock) -> None:
    """POST /admin/config/stock should update stock allocation config."""
    # Setup mock for get (returns None - new config)
    mock_admin_auth.get = AsyncMock(return_value=None)

    # Setup mock for execute (returns empty list for stock list query)
    mock_scalars = Mock()
    mock_scalars.all = Mock(return_value=[])
    mock_result = Mock()
    mock_result.scalars = Mock(return_value=mock_scalars)
    mock_admin_auth.execute = AsyncMock(return_value=mock_result)

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
        follow_redirects=False,
    )

    assert response.status_code == 303


def test_stock_save_validates_percentages(client: TestClient, mock_admin_auth: AsyncMock) -> None:
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
    )

    assert response.status_code == 400


def test_platform_save_validates_platform_name(
    client: TestClient, mock_admin_auth: AsyncMock
) -> None:
    """POST /admin/config/platforms should validate platform name."""
    response = client.post(
        "/admin/config/platforms",
        data={
            "platform": "invalid_platform",
            "shop_id": "123",
            "is_active": "on",
            "price_master": "platform",
        },
    )

    assert response.status_code == 400
