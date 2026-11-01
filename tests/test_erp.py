import pytest

from mes_scheduling_mcp.models import OrderStatus


def test_list_all_work_orders(erp):
    orders = erp.list_work_orders()
    assert len(orders) == 5


def test_filter_by_status(erp):
    released = erp.list_work_orders(status=OrderStatus.RELEASED.value)
    assert len(released) == 4
    assert all(o["status"] == OrderStatus.RELEASED.value for o in released)


def test_get_work_order(erp):
    order = erp.get_work_order("WO-2026-0001")
    assert order["product_name"] == "气动阀体"
    assert order["quantity"] == 120


def test_get_missing_order_raises(erp):
    with pytest.raises(ValueError):
        erp.get_work_order("WO-NOPE")


def test_release_draft_order(erp):
    released = erp.release_work_order("WO-2026-0005")
    assert released["status"] == OrderStatus.RELEASED.value


def test_release_non_draft_raises(erp):
    with pytest.raises(ValueError):
        erp.release_work_order("WO-2026-0001")


def test_material_availability_fulfilled(erp):
    result = erp.check_material_availability("WO-2026-0001")
    assert result["fulfilled"] is True
    assert result["shortages"] == []


def test_material_availability_shortage(erp):
    result = erp.check_material_availability("WO-2026-0002")
    assert result["fulfilled"] is False
    assert result["shortages"][0]["material_sku"] == "M-COOLANT"
    assert result["shortages"][0]["shortage_qty"] == 40.0