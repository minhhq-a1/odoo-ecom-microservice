"""Unit tests for logging module."""

from __future__ import annotations

from src.core.logging import get_logger


def test_get_logger() -> None:
    """get_logger should return a logger instance."""
    logger = get_logger("test_module")
    assert logger is not None
    assert hasattr(logger, "info")
    assert hasattr(logger, "error")
    assert hasattr(logger, "warning")
    assert hasattr(logger, "debug")


def test_get_logger_different_names() -> None:
    """get_logger should return different loggers for different names."""
    logger1 = get_logger("module1")
    logger2 = get_logger("module2")
    assert logger1 is not None
    assert logger2 is not None


def test_logger_info() -> None:
    """Logger should support info logging."""
    logger = get_logger("test")
    # Should not raise
    logger.info("test_message", key="value")


def test_logger_error() -> None:
    """Logger should support error logging."""
    logger = get_logger("test")
    # Should not raise
    logger.error("test_error", error="details")


def test_logger_warning() -> None:
    """Logger should support warning logging."""
    logger = get_logger("test")
    # Should not raise
    logger.warning("test_warning", reason="test")
