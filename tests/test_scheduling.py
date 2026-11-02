import pytest


def test_capacity_query(aps):
    caps = {c["code"]: c for c in aps.capacity_query()}
    assert len(caps) == 8
    assert "CNC-01" in caps
    assert caps["CNC-01"]["available_hours"] == 14.0


def test_generate_schedule_counts(backend):
    backend.synchronize_orders()
    result = backend.aps.generate_schedule()
    assert result["summary"]["scheduled_orders"] == 3
    assert result["summary"]["shortage_orders"] == 1
    assert result["summary"]["candidate_orders"] == 4


def test_generate_schedule_shortage_is_coolant(backend):
    backend.synchronize_orders()
    result = backend.aps.generate_schedule()
    assert [s["order_no"] for s in result["shortages"]] == ["WO-2026-0002"]
    assert result["shortages"][0]["shortages"][0]["sku"] == "RM-1004"
    assert result["shortages"][0]["shortages"][0]["shortage"] == 10.0


def test_generate_schedule_orders_preserved(backend):
    backend.synchronize_orders()
    result = backend.aps.generate_schedule()
    scheduled_nos = {s["erp_work_order_no"] for s in result["scheduled"]}
    assert scheduled_nos == {"WO-2026-0001", "WO-2026-0003", "WO-2026-0004"}


def test_generate_schedule_needs_production_orders(aps):
    result = aps.generate_schedule()
    assert result["summary"]["candidate_orders"] == 0


def test_schedule_operations_have_plan_id(backend):
    backend.synchronize_orders()
    result = backend.aps.generate_schedule()
    assert result["scheduled"]
    assert all(s["plan_id"] == result["plan_id"] for s in result["scheduled"])


def test_schedule_detail_and_dispatch(backend):
    backend.synchronize_orders()
    result = backend.aps.generate_schedule()
    plan_id = result["plan_id"]

    detail = backend.aps.schedule_detail(plan_id)
    assert detail["plan"]["plan_id"] == plan_id
    assert len(detail["operations"]) == 12  # 3 orders x 4 operations

    dispatched = backend.aps.dispatch_schedule(plan_id)
    assert dispatched["dispatched_count"] == 3
    activated = backend.mes.list_production_orders(status="RELEASED")
    assert len(activated) == 3


def test_get_missing_schedule_raises(aps):
    with pytest.raises(KeyError):
        aps.schedule_detail("does-not-exist")