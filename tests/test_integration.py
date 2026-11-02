"""End-to-end integration test across ERP -> MES -> APS and back.

Exercises the full closed loop of the case:
release -> sync to MES -> finite-capacity scheduling -> dispatch -> report
progress -> sync completion back to ERP -> reconcile (clean).
"""
from mes_scheduling_mcp.models import OrderStatus


def _complete_order(backend, production_order_no):
    ops = sorted(backend.mes.list_operations(production_order_no), key=lambda o: o.operation_no)
    backend.mes.start_production_order(production_order_no)
    qty = backend.store.get_production_order(production_order_no).quantity
    for op in ops:
        backend.mes.report_progress(
            production_order_no, op.operation_no,
            qty_good=qty, qty_bad=0, complete=True,
        )


def test_outbox_starts_with_pending_releases(backend):
    pending = [e for e in backend.integration.outbox.list()
               if str(e.event_type) == "ORDER_RELEASE" and str(e.status) == "PENDING"]
    assert len(pending) == 4


def test_full_closed_loop(backend):
    # release an additional draft order so the loop also covers the release path
    backend.erp.release_work_order("WO-2026-0005")

    sync = backend.synchronize_orders()
    assert len(sync["created"]) == 5

    plan = backend.aps.generate_schedule()

    # 1 released order is short on coolant and stays unscheduled
    assert plan["summary"]["scheduled_orders"] == 4
    assert plan["summary"]["shortage_orders"] == 1

    dispatched = backend.aps.dispatch_schedule(plan["plan_id"])
    assert dispatched["dispatched_count"] == 4

    scheduled = {s["production_order_no"] for s in plan["scheduled"]}
    for po_no in scheduled:
        _complete_order(backend, po_no)

    progress = backend.synchronize_progress()
    assert len(progress["updated"]) == 4

    for wo_no in ("WO-2026-0001", "WO-2026-0003", "WO-2026-0004", "WO-2026-0005"):
        assert str(backend.store.get_work_order(wo_no).status) == "COMPLETED"

    reconcile = backend.reconcile()
    assert reconcile["healthy"] is True
    assert reconcile["summary"]["matched"] == 5
    assert reconcile["summary"]["drift"] == 0


def test_mapping_and_audit_recorded(backend):
    backend.synchronize_orders()
    assert len(backend.mappings()["mappings"]) == 4
    assert backend.audit_log()["entries"]  # audit trail non-empty after sync