import pytest

from mes_scheduling_mcp.erp import ERPService
from mes_scheduling_mcp.mes import MESService
from mes_scheduling_mcp.scheduling import SchedulingService
from mes_scheduling_mcp.store import Store


@pytest.fixture()
def store():
    s = Store(":memory:")
    yield s
    s.close()


@pytest.fixture()
def erp(store):
    return ERPService(store)


@pytest.fixture()
def sched(store):
    return SchedulingService(store)


@pytest.fixture()
def mes(store):
    return MESService(store)