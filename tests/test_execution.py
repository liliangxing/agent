"""Shop-floor execution: runs, downtime, OEE, yield, SPC and recall."""
import pytest

from mes_scheduling_mcp.errors import ValidationError


def _issue_all(backend, po):
    for req in backend.erp.bom_explode(po.product_sku, po.quantity):
        backend.mes.issue_material(po.production_order_no, req["sku"], req["required"])


def _first_order(backend):
    backend.synchronize_orders()
    po = sorted(backend.mes.list_production_orders(), key=lambda x: x.due_date or "")[0]
    _issue_all(backend, po)
    return po


def _execute(backend, po, downtime_min=0.0):
    backend.mes.start_production_order(po.production_order_no)
    runs = []
    ops = sorted(backend.mes.list_operations(po.production_order_no), key=lambda o: o.operation_no)
    for index, op in enumerate(ops):
        run = backend.mes.start_run(po.production_order_no, op.operation_no)
        if index == 0 and downtime_min:
            backend.mes.log_run_downtime(run["run_no"], "BREAKDOWN", downtime_min)
        backend.mes.log_run_output(run["run_no"], qty_good=po.quantity, qty_scrap=0)
        backend.mes.end_run(run["run_no"])
        backend.mes.report_progress(po.production_order_no, op.operation_no,
                                    qty_good=po.quantity, qty_bad=0, complete=True)
        runs.append(run)
    return runs


def test_start_run_occupies_work_center_and_blocks_second_run(backend):
    po = _first_order(backend)
    backend.mes.start_production_order(po.production_order_no)
    first = backend.mes.start_run(po.production_order_no, 10)
    assert first["status"] == "RUNNING"
    assert first["planned_minutes"] > 0
    assert first["ideal_cycle_sec"] > 0
    with pytest.raises(ValidationError):
        backend.mes.start_run(po.production_order_no, 10)


def test_end_run_releases_work_center(backend):
    po = _first_order(backend)
    backend.mes.start_production_order(po.production_order_no)
    run = backend.mes.start_run(po.production_order_no, 10)
    ended = backend.mes.end_run(run["run_no"])
    assert ended["status"] == "ENDED"
    assert ended["ended_at"]
    second = backend.mes.start_run(po.production_order_no, 10)
    assert second["run_no"] != run["run_no"]


def test_end_run_is_idempotent(backend):
    po = _first_order(backend)
    backend.mes.start_production_order(po.production_order_no)
    run = backend.mes.start_run(po.production_order_no, 10)
    backend.mes.end_run(run["run_no"])
    assert backend.mes.end_run(run["run_no"])["status"] == "ENDED"


def test_log_output_rolls_up_into_operation(backend):
    po = _first_order(backend)
    backend.mes.start_production_order(po.production_order_no)
    run = backend.mes.start_run(po.production_order_no, 10)
    backend.mes.log_run_output(run["run_no"], qty_good=8, qty_scrap=2)
    updated = backend.mes.get_run(run["run_no"])
    assert (updated.qty_good, updated.qty_scrap) == (8, 2)
    op = [o for o in backend.mes.list_operations(po.production_order_no)
          if o.operation_no == 10][0]
    assert (op.qty_good, op.qty_bad) == (8, 2)


def test_oee_penalises_unplanned_downtime(backend):
    po = _first_order(backend)
    backend.mes.start_production_order(po.production_order_no)
    run = backend.mes.start_run(po.production_order_no, 10)
    backend.mes.log_run_output(run["run_no"], qty_good=po.quantity, qty_scrap=0)
    backend.mes.end_run(run["run_no"])
    baseline = backend.mes.oee_report(run_no=run["run_no"])
    assert baseline["unplanned_downtime_min"] == 0
    assert 0 < baseline["oee"] <= 1

    backend.mes.log_run_downtime(run["run_no"], "BREAKDOWN", 45)
    degraded = backend.mes.oee_report(run_no=run["run_no"])
    assert degraded["unplanned_downtime_min"] == 45
    assert degraded["availability"] < baseline["availability"]


def test_oee_overall_aggregates_all_runs(backend):
    po = _first_order(backend)
    _execute(backend, po)
    overall = backend.mes.oee_report()
    assert overall["runs"] >= 1
    assert overall["work_center"] is None
    assert 0 < overall["oee"] <= 1


def test_yield_and_defect_pareto(backend):
    po = _first_order(backend)
    backend.mes.start_production_order(po.production_order_no)
    backend.mes.record_test(po.production_order_no, 10, "U-1", "PASS",
                            characteristic="d1", nominal=10.0, tolerance=0.05, measured=10.01)
    backend.mes.record_test(po.production_order_no, 10, "U-2", "FAIL",
                            defect_code="DIM-OOT", defect_desc="外径超差",
                            characteristic="d1", nominal=10.0, tolerance=0.05, measured=10.20)
    backend.mes.record_test(po.production_order_no, 10, "U-3", "FAIL",
                            defect_code="DIM-OOT", defect_desc="外径超差",
                            characteristic="d1", nominal=10.0, tolerance=0.05, measured=9.70)
    report = backend.mes.yield_report(po.production_order_no)
    assert report["tested"] == 3
    assert report["first_pass_yield"] == pytest.approx(0.333)
    assert report["defect_pareto"][0]["defect_code"] == "DIM-OOT"
    assert report["defect_pareto"][0]["count"] == 2


def test_spc_reports_cpk_and_out_of_spec(backend):
    po = _first_order(backend)
    backend.mes.start_production_order(po.production_order_no)
    values = [10.000, 10.005, 9.995, 10.004, 9.996, 10.003, 9.997, 10.001]
    for index, measured in enumerate(values):
        backend.mes.record_test(po.production_order_no, 10, f"U-{index}", "PASS",
                                characteristic="外径", nominal=10.0, tolerance=0.05,
                                measured=measured)
    backend.mes.record_test(po.production_order_no, 10, "U-9", "FAIL",
                            defect_code="DIM-OOT", characteristic="同轴度",
                            nominal=0.02, tolerance=0.005, measured=0.040)
    report = backend.mes.spc_report()
    by_name = {row["characteristic"]: row for row in report["characteristics"]}
    diameter = by_name["外径"]
    assert diameter["n"] == 8
    assert diameter["out_of_spec"] == 0
    assert diameter["cp"] > 1
    assert diameter["cpk"] is not None
    assert by_name["同轴度"]["out_of_spec"] == 1


def test_recall_traces_raw_lot_to_customer(backend):
    po = _first_order(backend)
    _execute(backend, po)
    consumption = backend.mes.list_consumptions(po.production_order_no)[0]
    report = backend.mes.recall_report(consumption["lot_no"])
    assert report["lot_no"] == consumption["lot_no"]
    assert report["affected_order_count"] == 1
    order = report["affected_orders"][0]
    assert order["production_order_no"] == po.production_order_no
    assert order["output_lot"] == f"LOT-{po.production_order_no}"
    assert order["customer"]
    assert report["affected_quantity"] == po.quantity


def test_work_instructions_come_from_routing(backend):
    po = _first_order(backend)
    result = backend.mes.get_work_instructions(po.production_order_no)
    assert result["operations"]
    assert all(row["instructions"] for row in result["operations"])
    assert all(row["status"] for row in result["operations"])
