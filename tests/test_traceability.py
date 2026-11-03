"""Lot traceability: issue by lot, build genealogy on completion, walk both ways."""
import pytest

from mes_scheduling_mcp.errors import ValidationError


def _complete_first_order(backend):
    backend.synchronize_orders()
    po = sorted(backend.mes.list_production_orders(),
                key=lambda x: x.due_date or "")[0]
    for req in backend.erp.bom_explode(po.product_sku, po.quantity):
        backend.mes.issue_material(po.production_order_no, req["sku"], req["required"])
    backend.mes.start_production_order(po.production_order_no)
    for op in sorted(backend.mes.list_operations(po.production_order_no), key=lambda o: o.operation_no):
        backend.mes.report_progress(po.production_order_no, op.operation_no,
                                    qty_good=po.quantity, qty_bad=0, complete=True)
    return po


def test_issue_material_records_lot_allocations(backend):
    backend.synchronize_orders()
    po = sorted(backend.mes.list_production_orders(),
                key=lambda x: x.due_date or "")[0]
    req = backend.erp.bom_explode(po.product_sku, po.quantity)[0]
    result = backend.mes.issue_material(po.production_order_no, req["sku"], 10)
    assert result["lots"], "reservation should report the lots it drew from"
    assert sum(lot["quantity"] for lot in result["lots"]) == pytest.approx(10)
    consumptions = backend.mes.list_consumptions(po.production_order_no)
    assert [(c["item_sku"], c["lot_no"]) for c in consumptions] == [
        (req["sku"], result["lots"][0]["lot_no"])]


def test_completion_builds_genealogy(backend):
    po = _complete_first_order(backend)
    lot_no = f"LOT-{po.production_order_no}"
    trace = backend.mes.trace_lot(lot_no, direction="up")
    assert trace["lot_no"] == lot_no
    assert trace["parents"], "finished lot should trace back to input lots"
    assert all(p["child_lot"] == lot_no for p in trace["parents"])
    assert all(p["parent_lot"] != lot_no for p in trace["parents"])
    assert trace["upstream_lots"]


def test_trace_downstream_from_raw_lot(backend):
    po = _complete_first_order(backend)
    up = backend.mes.trace_lot(f"LOT-{po.production_order_no}", direction="up")
    raw_lot = up["upstream_lots"][0]
    down = backend.mes.trace_lot(raw_lot, direction="down")
    assert f"LOT-{po.production_order_no}" in down["downstream_lots"]


def test_trace_rejects_bad_direction(backend):
    with pytest.raises(ValidationError):
        backend.mes.trace_lot("LOT-ANY", direction="sideways")