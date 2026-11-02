import pytest

from mes_scheduling_mcp.backend import ManufacturingBackend
from mes_scheduling_mcp.store import Store


@pytest.fixture()
def store():
    s = Store(":memory:")
    yield s
    s.close()


@pytest.fixture()
def backend(store):
    return ManufacturingBackend(store)


@pytest.fixture()
def erp(backend):
    return backend.erp


@pytest.fixture()
def mes(backend):
    return backend.mes


@pytest.fixture()
def aps(backend):
    return backend.aps


@pytest.fixture()
def sched(backend):
    return backend.aps