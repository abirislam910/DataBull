

import os

from app.core.config import get_settings
import pytest
from pydantic import ValidationError


async def test_cannot_set_reading_limit_greater_than_max(
) -> None:
    os.environ["DEFAULT_READING_LIMIT"] = "10001"
    get_settings.cache_clear()
    try:
        with pytest.raises(ValidationError):
            get_settings()
    finally:
        os.environ["DEFAULT_READING_LIMIT"] = "1000"
        get_settings.cache_clear()
        settings = get_settings()
        assert settings.default_reading_limit == 1000