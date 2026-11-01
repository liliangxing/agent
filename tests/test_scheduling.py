def test_capacity_query(sched):
    caps = {c["line_id"]: c for c in sched.capacity_query()}
    assert caps["MILL-01"]["available_hours"] == 0.0
    assert caps["CNC-01"]["available_hours"] == 12.0


def test_generate_schedule_counts(sched):
    result = sched.generate_schedule()
    assert result["summary"]["scheduled_count"] == 3
    assert result["summary"]["shortage_count"] == 1
    assert result["summary"]["unscheduled_count"] == 0


def test_generate_schedule_shortage_is_coolant(sched):
    result = sched.generate_schedule()
    assert [s["order_no"] for s in result["shortages"]] == ["WO-2026-0002"]


def test_generate_schedule_orders_preserved(sched):
    result = sched.generate_schedule()
    scheduled_nos = [x["order_no"] for x in result["scheduled"]]
    assert set(scheduled_nos) == {"WO-2026-0001", "WO-2026-0003", "WO-2026-0004"}


def test_schedule_items_have_correct_plan_id(sched):
    result = sched.generate_schedule()
    plan = sched.get_schedule(result["plan_id"])
    assert all(i["plan_id"] == result["plan_id"] for i in plan["items"])


def test_get_missing_schedule_raises(sched):
    import pytest

    with pytest.raises(ValueError):
        sched.get_schedule("does-not-exist")