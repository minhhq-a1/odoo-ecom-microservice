"""Unit tests for workers app module."""

from __future__ import annotations

from src.workers.app import celery_app


def test_celery_app_exists() -> None:
    """Celery app should be defined."""
    assert celery_app is not None
    assert hasattr(celery_app, "task")
    assert hasattr(celery_app, "conf")


def test_celery_app_name() -> None:
    """Celery app should have correct name."""
    assert celery_app.main == "middleware"


def test_celery_app_has_beat_schedule() -> None:
    """Celery app should have beat schedule configured."""
    assert celery_app.conf.beat_schedule is not None
    assert len(celery_app.conf.beat_schedule) > 0
