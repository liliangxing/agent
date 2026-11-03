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


def test_mrp_nets_shared_stock(erp):
    result = erp.run_mrp()
    by_sku = {p["item_sku"]: p for p in result["proposals"]}
    # RM-1001 has enough on-hand for every open work order -> no proposal
    assert "RM-1001" not in by_sku
    # coolant is shared by four orders but netted exactly once
    coolant = [p for p in result["proposals"] if p["item_sku"] == "RM-1004"]
    assert len(coolant) == 1
    assert coolant[0]["gross_qty"] == 169.0
    assert coolant[0]["quantity"] == 169.0
    assert len(coolant[0]["pegged_orders"].split(",")) == 4


def test_mrp_proposes_production_for_semi_finished(erp):
    result = erp.run_mrp()
    by_sku = {p["item_sku"]: p for p in result["proposals"]}
    semi = by_sku["SF-1001"]
    assert semi["proposal_type"] == "PRODUCE"
    assert semi["quantity"] == 60.0  # 120 demand + 40 safety - 100 on hand
    assert semi["release_date"] is not None


def test_mrp_nets_open_purchase_orders(erp):
    erp.create_purchase_order(
        "PO-MRP", "供应商B", "2026-11-02",
        lines=[{"item_sku": "RM-1004", "quantity": 100}],
    )
    result = erp.run_mrp()
    coolant = next(p for p in result["proposals"] if p["item_sku"] == "RM-1004")
    # on-order supply reduces the net requirement
    assert coolant["on_order_qty"] == 100.0
    assert coolant["quantity"] == 69.0