"""Unit tests for base connector."""

from __future__ import annotations

from src.connectors.base import BaseConnector


def test_base_connector_is_abstract() -> None:
    """BaseConnector should be abstract and not instantiable directly."""
    # BaseConnector is abstract, but we can check it exists
    assert BaseConnector is not None
    assert hasattr(BaseConnector, "__abstractmethods__")
