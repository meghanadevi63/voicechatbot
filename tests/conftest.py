import pytest


@pytest.fixture
def anyio_backend():
    # Run async tests (marked @pytest.mark.anyio) on asyncio only
    return "asyncio"
