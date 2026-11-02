import pytest

from mes_scheduling_mcp.models import OrderStatus


def test_list_all_work_orders(erp):
    orders = erp.list_work_orders()
    assert len(orders) == 5


def test_filter_by_status(erp):
    released = erp.list_work_orders(status="RELEASED")
    assert len(released) == 4
    assert all(str(o.status) == "RELEASED" for o in released)


def test_get_work_order(erp):
    order = erp.get_work_order("WO-2026-0001")
    assert order.product_name == "气动阀体"
    assert order.quantity == 120


def test_get_missing_order_raises(erp):
    with pytest.raises(KeyError):
        erp.get_work_order("WO-NOPE")


def test_release_draft_order(erp):
    released = erp.release_work_order("WO-2026-0005")
    assert released["status"] == OrderStatus.RELEASED


def test_release_non_draft_raises(erp):
    with pytest.raises(ValueError):
        erp.release_work_order("WO-2026-0001")


def test_material_availability_fulfilled(erp):
    result = erp.check_material_availability("WO-2026-0001")
    assert result["fulfilled"] is True
    assert result["shortages"] == []


def test_material_availability_shortage_is_coolant(erp):
    result = erp.check_material_availability("WO-2026-0002")
    assert result["fulfilled"] is False
    assert result["shortages"][0]["sku"] == "RM-1004"
    assert result["shortages"][0]["shortage"] == 10.0


def test_bom_explode_multilevel(erp):
    rows = erp.bom_explode("FG-1001", 10)
    skus = {r["sku"] for r in rows}
    assert "RM-1001" in skus  # direct raw material
    assert "RM-1002" in skus  # reached through semi-finished SF-1001


def test_inventory_adjust(erp):
    before = erp.inventory_summary(sku="RM-1001")[0]["available"]
    erp.inventory_adjust("RM-1001", "AL-260001", delta_on_hand=100)
    after = erp.inventory_summary(sku="RM-1001")[0]["available"]
    assert after == before + 100


def test_receive_purchase_order_increases_stock(erp):
    erp.create_purchase_order(
        "PO-TEST", "供应商A", "2026-12-01",
        lines=[{"item_sku": "RM-1001", "quantity": 50}],
    )
    before = erp.inventory_summary(sku="RM-1001")[0]["available"]
    erp.receive_purchase_order("PO-TEST")
    after = erp.inventory_summary(sku="RM-1001")[0]["available"]
    assert after == before + 50