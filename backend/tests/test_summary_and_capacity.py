"""Compact per-aircraft summary (verified and the reason it is not) and the capacity guard."""
from __future__ import annotations

from app import config
from app.capacity import assess
from app.summary_routes import build_summary, not_verified_reason


def _vehicle(system_id, status="connected", valid=True, **extra):
    return {"system_id": system_id, "id": f"px4-{system_id}", "status": status, "telemetry_age_seconds": 0.4, "armed": False, "heartbeat": {"flight_mode": "Hold"}, "landed_state": {"name": "on_ground"},
            "battery": {"remaining_percent": 91}, "position": {"latitude": 12.9, "longitude": 77.5, "relative_altitude_m": 0.1, "valid": valid}, **extra}


def _model(index, status="connected"):
    return {"model_name": f"x500_{index}", "status": status, "telemetry_age_seconds": 0.2, "position_m": {"x": 1.0, "y": 2.0 * index, "z": 0.2}}


def test_every_planned_aircraft_has_a_row_and_verified_matches_the_reason():
    plan = [{"uav_id": f"px4-{n}", "role": role, "start": None} for n, role in ((1, "primary"), (2, "decoy"), (3, "decoy"), (4, "standby"), (5, "standby"))]
    vehicles = [_vehicle(1), _vehicle(2), _vehicle(3, status="stale"), _vehicle(4, valid=False)]          # px4-5 never seen
    models = [_model(0), _model(1), _model(2), _model(3, status="disconnected")]
    summary = build_summary(plan, vehicles, models, {"px4-1": {"state": "flying", "route_reached": 2, "route_total": 9}}, running_count=5)
    rows = {row["aircraft_id"]: row for row in summary["aircraft"]}
    assert len(rows) == 5 and summary["counts"]["with_mission"] == 5
    assert rows["px4-1"]["verified"] and rows["px4-1"]["not_verified_reason"] is None and rows["px4-1"]["route_reached"] == 2
    assert rows["px4-3"]["not_verified_reason"].startswith("PX4 telemetry is stale")
    assert rows["px4-4"]["not_verified_reason"] == "PX4 has no valid GPS position yet."
    assert rows["px4-5"]["not_verified_reason"].startswith("No PX4 telemetry")
    # the count of verified aircraft and the list of not-verified ones always add up to the aircraft that have a mission
    assert summary["counts"]["verified"] + len(summary["counts"]["not_verified"]) == summary["counts"]["with_mission"] == 5
    assert summary["counts"]["verified"] == 2 and all(item["reason"] for item in summary["counts"]["not_verified"])


def test_the_reason_names_a_missing_gazebo_pose_and_a_stopped_simulation():
    assert not_verified_reason(_vehicle(1), None, in_simulation=True) == "No Gazebo pose has arrived for this aircraft."
    assert "Gazebo pose is stale" in not_verified_reason(_vehicle(1), _model(0, "stale"), in_simulation=True)
    assert not_verified_reason(None, None, in_simulation=False).startswith("Not part of the running simulation")
    assert not_verified_reason(_vehicle(1), _model(0), in_simulation=True) is None


def test_capacity_guard_warns_before_a_half_start_and_never_blocks_planning(monkeypatch):
    ok = assess(3, memory=(15800, 9000))
    assert ok["level"] == "ok" and ok["reasons"] == [] and ok["safe_maximum"] == config.SIM_MAX_AIRCRAFT
    tight = assess(5, memory=(15800, 3000))
    assert tight["level"] == "warn" and "3000 MiB" in tight["reasons"][0]
    short = assess(5, memory=(15800, 1000))
    assert short["level"] == "overload" and "only 1000 MiB" in short["reasons"][0] and short["advice"]
    too_many = assess(config.SIM_MAX_AIRCRAFT + 1, memory=(15800, 9000))
    assert too_many["level"] == "overload" and "measured to run" in too_many["reasons"][0]
    running = assess(5, running=5, memory=(15800, 500))               # aircraft that already run are not counted again
    assert running["level"] == "ok" and running["estimated_needed_mb"] == 0
    monkeypatch.setattr("app.capacity.host_memory_mb", lambda: None)
    unknown = assess(2)                                               # memory unreadable: only the maximum is judged
    assert unknown["level"] == "ok" and unknown["host_free_mb"] is None


def test_the_summary_shows_the_role_after_a_reassign_and_keeps_the_plan_role():
    plan = [{"uav_id": "px4-1", "role": "standby", "plan_role": "primary", "start": None}, {"uav_id": "px4-2", "role": "primary", "plan_role": "standby", "start": None}]
    rows = {r["aircraft_id"]: r for r in build_summary(plan, [_vehicle(1), _vehicle(2)], [_model(0), _model(1)], {}, running_count=2)["aircraft"]}
    assert rows["px4-1"]["role"] == "standby" and rows["px4-1"]["plan_role"] == "primary" and rows["px4-2"]["role"] == "primary"
