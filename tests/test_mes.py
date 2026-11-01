import pytest

from mes_scheduling_mcp.models import OrderStatus, TaskStatus


def test_import_all_released(mes, store):
    result = mes.import_work_orders()
    assert result["imported_count"] == 4
    remaining = store.list_work_orders(status=OrderStatus.RELEASED.value)
    assert remaining == []


def test_import_specific_orders(mes):
    result = mes.import_work_orders(["WO-2026-0001"])
    assert result["imported_count"] == 1


def test_import_missing_raises(mes):
    with pytest.raises(ValueError):
        mes.import_work_orders(["WO-NOPE"])


def test_dispatch_schedule(sched, mes, store):
    plan_id = sched.generate_schedule()["plan_id"]
    result = mes.dispatch_schedule(plan_id)
    assert result["dispatched_count"] == 3
    tasks = store.list_tasks(status=TaskStatus.DISPATCHED.value)
    assert len(tasks) == 3


def test_report_progress_complete(sched, mes):
    plan_id = sched.generate_schedule()["plan_id"]
    mes.dispatch_schedule(plan_id)
    task = mes.list_tasks(status=TaskStatus.DISPATCHED.value)[0]
    updated = mes.report_progress(task["task_id"], task["quantity"], complete=True)
    assert updated["status"] == TaskStatus.COMPLETED.value


def test_report_progress_overreport_raises(sched, mes):
    plan_id = sched.generate_schedule()["plan_id"]
    mes.dispatch_schedule(plan_id)
    task = mes.list_tasks(status=TaskStatus.DISPATCHED.value)[0]
    with pytest.raises(ValueError):
        mes.report_progress(task["task_id"], task["quantity"] + 1)


def test_health(mes):
    h = mes.health()
    assert h["status"] == "ok"
    assert h["work_orders"] == 5
    assert h["lines"] == 4