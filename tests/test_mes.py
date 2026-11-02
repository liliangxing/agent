import pytest

from mes_scheduling_mcp.models import EventType


def test_health(mes):
    h = mes.health()
    assert h["status"] == "ok"
    assert h["work_centers"] == 8
    assert h["production_orders"] == 0


def test_create_production_order_precreates_operations(mes, store):
    wo = store.get_work_order("WO-2026-0001")
    po = mes.create_production_order(wo)
    ops = mes.list_operations(po.production_order_no)
    assert len(ops) == 4  # FG-1001 has a 4-step routing
    assert str(po.status) == "DRAFT"
    assert all(str(o.status) == "PENDING" for o in ops)


def test_start_production_order(mes, store):
    wo = store.get_work_order("WO-2026-0001")
    po = mes.create_production_order(wo)
    mes.start_production_order(po.production_order_no)
    assert str(mes.get_production_order(po.production_order_no).status) == "IN_PROCESS"


def test_report_progress_completes_order(backend, store):
    backend.synchronize_orders()
    po = store.list_production_orders()[0]
    ops = sorted(backend.mes.list_operations(po.production_order_no), key=lambda o: o.operation_no)
    backend.mes.start_production_order(po.production_order_no)
    for op in ops:
        backend.mes.report_progress(
            po.production_order_no, op.operation_no,
            qty_good=po.quantity, qty_bad=0, complete=True,
        )
    assert str(backend.mes.get_production_order(po.production_order_no).status) == "COMPLETED"


def test_completion_publishes_event(backend, store):
    backend.synchronize_orders()
    po = store.list_production_orders()[0]
    ops = sorted(backend.mes.list_operations(po.production_order_no), key=lambda o: o.operation_no)
    backend.mes.start_production_order(po.production_order_no)
    for op in ops:
        backend.mes.report_progress(
            po.production_order_no, op.operation_no,
            qty_good=po.quantity, qty_bad=0, complete=True,
        )
    completions = [e for e in backend.integration.outbox.list()
                   if e.event_type == EventType.COMPLETION]
    assert len(completions) == 1
    assert completions[0].aggregate_id == po.production_order_no


def test_oee_report(mes):
    report = mes.oee_report("CNC-01")
    assert 0.0 <= report["oee"] <= 1.0
    assert report["availability"] >= 0.0


def test_quality_inspect(mes, store):
    wo = store.get_work_order("WO-2026-0001")
    po = mes.create_production_order(wo)
    rec = mes.quality_inspect(po.production_order_no, 10, sample_qty=10, defect_qty=0, disposition="PASS")
    assert rec["disposition"] == "PASS"


def test_report_downtime(mes):
    evt = mes.report_downtime("CNC-01", "BREAKDOWN", "2026-11-02T10:00:00", "2026-11-02T10:30:00")
    assert evt["duration_min"] == 30.0