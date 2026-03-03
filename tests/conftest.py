from datetime import UTC, datetime

import pytest


@pytest.fixture
def fixed_timestamp() -> datetime:
    return datetime(2026, 3, 3, 10, 0, 0, tzinfo=UTC)
