"""Web dashboard state must expose the production cockpit fields."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from web_demo import server  # noqa: E402


def test_state_exposes_shift_andon_and_oee_loss():
    server.run_all()
    state = server.build_state()

    shift = state["shift"]
    assert shift["window"] == "08:00-16:00"
    assert shift["planned_qty"] > 0
    assert 0 < shift["attainment"] <= 1
    assert shift["downtime_min"] >= shift["unplanned_downtime_min"] >= 0

    andon = state["andon"]
    assert andon["items"]
    assert andon["critical"] >= 1
    assert len(andon["items"]) <= 10
    assert andon["info"] >= 0
    assert andon["total"] >= len(andon["items"])
    assert all(x["severity"] in ("critical", "warning", "info")
               for x in andon["items"])

    loss = state["oee_loss"]
    assert 0 < loss["oee"] <= 1
    assert abs(loss["oee"] - (loss["availability"] * loss["performance"]
                              * loss["quality"])) < 0.01
    assert loss["performance_loss_min"] >= 0

    execution = state["execution"]
    assert execution
    for order in execution:
        assert order["operations"]
        assert 0 <= order["progress"] <= 1
        assert all("instructions" in op for op in order["operations"])


def test_state_exposes_manifest_and_spc_points():
    server.run_all()
    state = server.build_state()

    manifest = state["manifest"]
    assert manifest["total"] == 73
    assert manifest["by_risk_class"]["destructive"] == 2
    assert len(manifest["approval_required"]) == 7

    characteristics = state["quality"]["spc"]["characteristics"]
    assert characteristics
    assert all(c["points"] for c in characteristics)
