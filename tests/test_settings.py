import pytest
from core.settings import settings


def test_settings_loads():
    """Settings object is created without errors."""
    assert settings is not None


def test_database_url_is_string():
    """Database URL is a non-empty string."""
    assert isinstance(settings.database_url, str)
    assert len(settings.database_url) > 0


def test_database_url_has_correct_driver():
    """Database URL uses the asyncpg driver."""
    assert "asyncpg" in settings.database_url


def test_redis_url_is_string():
    """Redis URL is a non-empty string."""
    assert isinstance(settings.redis_url, str)
    assert settings.redis_url.startswith("redis://")


def test_token_limit_is_integer():
    """Daily token limit is an integer, not a string."""
    assert isinstance(settings.default_daily_token_limit, int)


def test_budget_is_float():
    """Monthly budget is a float."""
    assert isinstance(settings.default_monthly_budget_usd, float)


def test_app_env_is_development():
    """App environment is development in our local setup."""
    assert settings.app_env == "development"


def test_log_level_is_valid():
    """Log level is one of the valid options."""
    valid_levels = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
    assert settings.log_level.upper() in valid_levels