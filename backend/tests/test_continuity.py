"""Part 6: the continuity watcher decides completed / degraded / failed only from measured run states and telemetry."""
from __future__ import annotations

import pytest

from app.research import continuity as cont
from app.research import live_experiments as live


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    cont.reset_for_tests()
    live.reset_for_tests()
    monkeypatch.setattr(cont, "add_audit_event", lambda **kw: None)
    yield
    cont.reset_for_tests()
    live.reset_for_tests()


class World:
    """Fake run table and telemetry the watcher reads."""

    def __init__(self, monkeypatch):
        self.runs = {}
        self.vehicles = {}
        monkeypatch.setattr(cont, "get_mission_execution", lambda run_id: self.runs.get(run_id))
        cont.set_vehicle_source(lambda: list(self.vehicles.values()))
        cont.set_sync(lambda run: run)

    def run(self, run_id, state, percent=0.0, active=True, reason=""):
        self.runs[run_id] = {"id": run_id, "state": state, "active": active, "reason": reason, "progress": {"percent": percent}}

    def vehicle(self, aircraft, armed, altitude):
        self.vehicles[aircraft] = {"id": aircraft, "armed": armed, "position": {"relative_altitude_m": altitude}}


def watch(kind="handover"):
    return cont.watch(kind, "px4-1", "px4-3", "dep", "run-old", "run-new", "tester")


def test_a_handover_that_finishes_with_the_old_aircraft_on_the_ground_is_completed(monkeypatch):
    world = World(monkeypatch)
    item = watch()
    world.run("run-old", "executing", 20); world.run("run-new", "executing", 5); world.vehicle("px4-1", True, 28.0)
    assert cont.evaluate(item, now=item.started_mono + 3) == "verifying" and item.checks["new_primary_flying"]["ok"] is True
    world.run("run-old", "aborted", 22, active=False); world.run("run-new", "executing", 55); world.vehicle("px4-1", False, 0.1)
    assert cont.evaluate(item, now=item.started_mono + 60) == "verifying" and item.checks["previous_aircraft_safe"]["ok"] is True
    world.run("run-new", "completed", 100, active=False)
    assert cont.evaluate(item, now=item.started_mono + 300) == "completed" and item.status == "completed" and item.finished_wall


def test_a_new_primary_that_never_flies_is_failed(monkeypatch):
    world = World(monkeypatch)
    item = watch()
    world.run("run-old", "aborted", 20, active=False); world.run("run-new", "connecting", 0); world.vehicle("px4-1", False, 0.0)
    assert cont.evaluate(item, now=item.started_mono + cont.START_GRACE_S + 5) == "failed"
    assert item.checks["new_primary_flying"]["ok"] is False


def test_a_finished_route_with_the_old_aircraft_still_armed_is_degraded(monkeypatch):
    world = World(monkeypatch)
    item = watch()
    world.run("run-old", "aborted", 20, active=False); world.run("run-new", "executing", 50); world.vehicle("px4-1", True, 12.0)
    cont.evaluate(item, now=item.started_mono + 5)
    world.run("run-new", "completed", 100, active=False)
    assert cont.evaluate(item, now=item.started_mono + cont.SAFE_WAIT_S + 200) == "degraded"
    assert item.checks["previous_aircraft_safe"]["ok"] is False and any("not confirmed safe" in text for text in item.notes)


def test_a_new_primary_ended_early_by_the_operator_is_degraded_with_its_progress(monkeypatch):
    world = World(monkeypatch)
    item = watch()
    world.run("run-old", "aborted", 20, active=False); world.run("run-new", "executing", 35); world.vehicle("px4-1", False, 0.0)
    cont.evaluate(item, now=item.started_mono + 10)
    world.run("run-new", "aborted", 38, active=False, reason="aborted by the operator")
    assert cont.evaluate(item, now=item.started_mono + 40) == "degraded" and any("ended early" in text for text in item.notes)


def test_a_failed_new_run_is_failed(monkeypatch):
    world = World(monkeypatch)
    item = watch()
    world.run("run-old", "aborted", 20, active=False); world.run("run-new", "failed", 0, active=False, reason="px4 refused to arm"); world.vehicle("px4-1", False, 0.0)
    assert cont.evaluate(item, now=item.started_mono + 20) == "failed"


def test_a_switch_keeps_the_old_primary_flying_as_a_decoy_and_completes_when_the_new_primary_finishes(monkeypatch):
    world = World(monkeypatch)
    item = watch("switch_primary")
    world.run("run-old", "executing", 40); world.run("run-new", "executing", 45)
    assert cont.evaluate(item, now=item.started_mono + 5) == "verifying" and "keeps flying" in item.checks["previous_aircraft_safe"]["detail"]
    world.run("run-new", "completed", 100, active=False)
    assert cont.evaluate(item, now=item.started_mono + 200) == "completed"


def test_the_outcome_is_attached_to_the_run_record_even_when_it_was_already_written():
    live._result_ids["run-old"] = ["result-1"]
    seen = {}
    import app.research.live_experiments as module
    original = module.update_experiment_result_payload
    module.update_experiment_result_payload = lambda result_id, updates: seen.update({result_id: updates}) or True
    try:
        live.attach_reconfiguration("run-old", {"id": "r1", "status": "completed"})
    finally:
        module.update_experiment_result_payload = original
    assert seen == {"result-1": {"reconfigurations": [{"id": "r1", "status": "completed"}]}}


def test_the_reconfigurations_endpoint_lists_what_the_watcher_knows(general_client, monkeypatch):
    client, _ = general_client
    world = World(monkeypatch)
    world.run("run-old", "executing", 10); world.run("run-new", "executing", 5); world.vehicle("px4-1", True, 20.0)
    watch()
    body = client.get("/api/research/reconfigurations").json()
    assert body["reconfigurations"][0]["status"] == "verifying" and body["reconfigurations"][0]["previous_primary"] == "px4-1" and body["simulation_only"] is True
