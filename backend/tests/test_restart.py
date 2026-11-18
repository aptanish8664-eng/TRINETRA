"""Restart at mission start (always available) and the one-click mission switch, through the real HTTP stack.

The WSL fleet manager and executor are fakes (and tests/conftest.py blocks the real ones). The live behaviour (timings, the aircraft at the
mission start afterwards) is recorded in docs/OVERNIGHT_RUN_LOG.md.
"""
from __future__ import annotations

import threading
import time

import pytest

import test_simulation_home as home
from app.mission_execution.restart import REASONS
from test_mission_execution_api import _operator_deployment_without_approval, deployed, sim, start, vehicle  # noqa: F401  (sim is a fixture)
from test_mission_workflow_e2e import body as plan_body, complete_plan, flight  # noqa: F401  (flight is a fixture)

CONFIRMATION = "This restarts PX4 and Gazebo at the mission start (about 20 s). A flight in progress ends."


class Manager:
    """A fake fleet manager: records every call, answers status/relocate, and can be told to fail or to wait."""

    def __init__(self):
        self.calls = []
        self.relocate_answer = None
        self.status_answer = {"instances": [{"instance": 0, "system_id": 1, "managed": True, "status": "process_present"}]}
        self.gate = None
        self.on_relocate = None          # what PX4 looks like after the restart (set by the stack fixture)

    def __call__(self, action, **kwargs):
        self.calls.append((action, kwargs))
        if action == "status":
            return dict(self.status_answer)
        if self.gate is not None:
            self.gate.wait(20)
        if self.on_relocate is not None and self.relocate_answer is None:
            self.on_relocate()
        return dict(self.relocate_answer) if self.relocate_answer is not None else home._manager_ok()


@pytest.fixture(autouse=True)
def fresh_jobs(monkeypatch):
    from app import restart_routes

    monkeypatch.setattr(restart_routes, "_jobs", {})
    monkeypatch.setattr(restart_routes, "_state", {"active": None, "latest": None})


@pytest.fixture
def stack(sim, monkeypatch):
    """A deployed mission, a verified managed aircraft at the mission start, bound receivers, a fake manager; the reset hook is observed."""
    manager = Manager()
    home._receivers(monkeypatch)
    row = {"aircraft_id": "px4-1", "instance_number": 0, "system_id": 1, "gazebo_model": "x500_0", "px4_status": "connected", "gazebo_status": "connected", "verified": True}
    monkeypatch.setattr("app.fleet_routes._verified_fleet", lambda count: ([row], [row]))
    monkeypatch.setattr("app.fleet_routes.invoke_fleet_manager", home.staged(manager))        # the staged start is forwarded to this fake as `relocate`
    resets = []
    monkeypatch.setattr("app.research_routes.reset_live_threat", lambda: resets.append(1))
    manager.on_relocate = lambda: sim.state.update(vehicles=[vehicle()])      # the restarted PX4: disarmed, at the mission start
    sim.manager, sim.resets = manager, resets
    return sim


def audit_events(event_type):
    """(outcome, reason, metadata) of every audit event of one type, read straight from the audit table."""
    import json

    from app.persistence.database import AuditLogRecord, SessionLocal

    with SessionLocal() as session:
        return [(row.outcome, row.reason, json.loads(row.metadata_json)) for row in session.query(AuditLogRecord).filter_by(event_type=event_type)]


def restart(client, deployment_id, purpose="restart", confirm=True):
    return client.post("/api/mission-control/restart", json={"deployment_id": deployment_id, "confirm": confirm, "purpose": purpose})


def finished(client, response, timeout=20.0):
    assert response.status_code == 202, response.text
    job_id = response.json()["id"]
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = client.get(f"/api/mission-control/restart/{job_id}").json()
        if job["status"] != "running":
            return job
        time.sleep(0.05)
    raise AssertionError(f"the restart job did not finish: {job}")


def steps(job):
    return {item["key"]: item for item in job["steps"]}


def test_restart_needs_a_confirmation_that_states_the_effect(general_client, stack):
    client, _ = general_client
    _scenario, _version, deployment_id = deployed(client)
    refused = restart(client, deployment_id, confirm=False)
    assert refused.status_code == 422 and refused.json()["detail"]["code"] == "confirmation_required"
    assert CONFIRMATION in refused.json()["detail"]["message"] and stack.manager.calls == []
    assert client.post("/api/mission-control/restart", json={"deployment_id": "nope", "confirm": True}).status_code == 404


@pytest.mark.parametrize("armed", [False, True])
def test_restart_while_idle_or_armed_on_the_ground_or_already_at_the_start_runs_every_step(general_client, stack, armed):
    client, _ = general_client
    _scenario, _version, deployment_id = deployed(client)
    stack.state["vehicles"] = [vehicle(armed=armed)]                          # standing at the mission start (0 m), disarmed or armed
    job = finished(client, restart(client, deployment_id))
    assert job["status"] == "succeeded" and job["failure"] is None and job["can_retry"] is False, (job["failure"], job["steps"])
    assert [item["key"] for item in job["steps"]] == ["managed", "executor", "stop_sim", "start_sim", "bridges", "telemetry", "gazebo_pose", "position", "orphans"]
    assert all(item["status"] == "ok" for item in job["steps"]), job["steps"]
    relocate = [kwargs for action, kwargs in stack.manager.calls if action == "relocate"]
    assert len(relocate) == 1 and relocate[0]["count"] == 1
    start_point = home.offset()
    assert abs(relocate[0]["home"]["latitude"] - start_point["latitude"]) < 1e-9 and abs(relocate[0]["home"]["longitude"] - start_point["longitude"]) < 1e-9
    assert job["result"]["max_distance_to_start_m"] < 5 and job["result"]["ended_executions"] == []
    assert stack.resets == [1]                                               # the live Threat Monitor window starts again
    audit = audit_events("simulation_restart")
    assert len(audit) == 1 and audit[0][0] == "success" and audit[0][2]["purpose"] == "restart" and audit[0][2]["aircraft_before"]["armed"] is armed


def test_restart_mid_flight_ends_the_run_with_the_reason_and_stops_the_executor_first(general_client, stack):
    client, _ = general_client
    _scenario, _version, deployment_id = deployed(client)
    execution = start(client, deployment_id).json()
    stack.fly()
    stack.state["vehicles"] = [vehicle(armed=True, position={"latitude": 12.97, "longitude": 77.59, "relative_altitude_m": 40.0, "valid": True}, velocity={"ground_speed_m_s": 8.0})]
    job = finished(client, restart(client, deployment_id))
    assert job["status"] == "succeeded" and job["result"]["ended_executions"] == ["px4-1"]
    assert ("cleanup", None) in stack.calls                                  # the executor was stopped through the fixed `cleanup` action
    final = client.get(f"/api/mission-control/executions/{execution['id']}").json()
    assert final["active"] is False and final["state"] == "aborted" and final["reason"] == "simulation restarted" == REASONS["restart"]
    events = client.get(f"/api/mission-control/executions/{execution['id']}/events").json()["events"]
    mine = [item for item in events if item["type"] == "simulation_restarted"]
    assert len(mine) == 1 and mine[0]["actor_name"] and mine[0]["data"]["state_before"] in ("executing", "arming", "uploading", "starting")
    # the aircraft can be started again with no further step
    stack.state["vehicles"] = [vehicle()]
    again = start(client, deployment_id)
    assert again.status_code == 201, again.text


def test_a_foreign_px4_instance_is_refused_before_anything_is_stopped_with_what_to_do(general_client, stack):
    client, _ = general_client
    _scenario, _version, deployment_id = deployed(client)
    execution = start(client, deployment_id).json()
    stack.fly()
    stack.manager.status_answer = {"instances": [{"instance": 0, "system_id": 1, "managed": False, "status": "process_present"}]}
    job = finished(client, restart(client, deployment_id))
    assert job["status"] == "failed" and job["can_retry"] is True and job["failure"]["step"] == "managed" and job["failure"]["code"] == "instance_not_managed"
    assert "not started by the TRINETRA fleet manager" in job["failure"]["message"] and "Stop that PX4 process in WSL" in job["failure"]["message"] and "nothing was changed" in job["failure"]["message"]
    assert [action for action, _ in stack.manager.calls] == ["status"]       # no relocate, nothing stopped
    assert ("cleanup", None) not in stack.calls
    assert client.get(f"/api/mission-control/executions/{execution['id']}").json()["active"] is True      # the run was not touched


def test_a_failed_step_names_the_step_and_the_reason_and_a_retry_succeeds(general_client, stack):
    client, _ = general_client
    _scenario, _version, deployment_id = deployed(client)
    stack.manager.relocate_answer = home._manager_ok(failure="PX4 SITL instance 0 exited at once.", failure_code="sitl_start_failed", instances=[])
    failed = finished(client, restart(client, deployment_id))
    assert failed["status"] == "failed" and failed["can_retry"] is True
    assert failed["failure"]["step"] == "start_sim" and failed["failure"]["code"] == "sitl_start_failed" and "PX4 SITL instance 0 exited at once." in failed["failure"]["message"]
    assert audit_events("simulation_restart")[0][0] == "failed" and "PX4 SITL instance 0 exited at once." in audit_events("simulation_restart")[0][1]
    assert steps(failed)["start_sim"]["status"] == "failed" and steps(failed)["stop_sim"]["status"] == "ok" and steps(failed)["position"]["status"] == "skipped"
    assert all(steps(failed)[key]["status"] == "skipped" and "Not run" in steps(failed)[key]["detail"] for key in ("bridges", "telemetry", "gazebo_pose", "position", "orphans"))   # never left RUNNING or pending after a failure
    stack.manager.relocate_answer = None                                     # Retry: a new job, the lock was released
    retry = finished(client, restart(client, deployment_id))
    assert retry["status"] == "succeeded" and retry["id"] != failed["id"]


def test_position_not_at_the_start_fails_the_position_step_after_the_time_box(general_client, stack, monkeypatch):
    client, _ = general_client
    _scenario, _version, deployment_id = deployed(client)
    monkeypatch.setattr("app.fleet_routes.RELOCATION_VERIFY_TIMEOUT_S", 0.0)
    stack.manager.on_relocate = None                                         # PX4 comes back at the wrong place
    stack.state["vehicles"] = [vehicle(position={"latitude": 47.3769, "longitude": 8.5417, "relative_altitude_m": 0.0, "valid": True})]    # the Zurich default home, 8000 km away
    job = finished(client, restart(client, deployment_id))
    assert job["status"] == "failed" and job["failure"]["step"] == "position" and "mission start" in job["failure"]["message"]
    assert steps(job)["position"]["status"] == "failed" and job["can_retry"] is True


def test_only_one_restart_at_a_time_and_a_double_click_is_refused_then_allowed_again(general_client, stack):
    client, _ = general_client
    _scenario, _version, deployment_id = deployed(client)
    stack.manager.gate = threading.Event()
    first = restart(client, deployment_id)
    assert first.status_code == 202
    second = restart(client, deployment_id)
    assert second.status_code == 409 and second.json()["detail"]["code"] == "restart_in_progress" and second.json()["detail"]["job_id"] == first.json()["id"]
    assert client.get("/api/mission-control/restart").json()["job"]["status"] == "running"       # a reloaded page finds it
    stack.manager.gate.set()
    done = finished(client, first)
    assert done["status"] == "succeeded"
    assert finished(client, restart(client, deployment_id))["status"] == "succeeded"
    assert len([action for action, _ in stack.manager.calls if action == "relocate"]) == 2


def test_a_job_that_never_finishes_is_reported_failed_so_the_ui_is_never_stuck(general_client, stack, monkeypatch):
    client, _ = general_client
    _scenario, _version, deployment_id = deployed(client)
    monkeypatch.setattr("app.restart_routes.JOB_STUCK_S", 0.3)
    stack.manager.gate = threading.Event()
    first = restart(client, deployment_id)
    time.sleep(0.5)
    job = client.get(f"/api/mission-control/restart/{first.json()['id']}").json()
    assert job["status"] == "failed" and job["failure"]["code"] == "restart_timeout" and job["can_retry"] is True
    stack.manager.gate.set()
    stack.manager.gate = None
    time.sleep(0.3)
    assert finished(client, restart(client, deployment_id))["status"] == "succeeded"


def test_both_roles_may_restart_login_and_csrf_are_required_and_other_users_missions_stay_hidden(general_client, operator_client, stack):
    from fastapi.testclient import TestClient
    from app.main import app

    general, _ = general_client
    operator, _user = operator_client
    _scenario, _version, general_deployment = deployed(general)
    assert TestClient(app).post("/api/mission-control/restart", json={"deployment_id": general_deployment, "confirm": True}).status_code in (401, 403)
    csrf = operator.headers.pop("X-CSRF-Token")
    assert restart(operator, general_deployment).status_code in (403, 404)
    operator.headers["X-CSRF-Token"] = csrf
    assert restart(operator, general_deployment).status_code == 404            # an Operator cannot see a General's mission
    _scenario_id, _version_id, operator_deployment = _operator_deployment_without_approval(operator_client)
    assert finished(operator, restart(operator, operator_deployment))["status"] == "succeeded"     # restart needs no approval (it flies nothing)


# --- one-click mission switch -----------------------------------------------------------------------------------------------

@pytest.mark.parametrize("armed,fly", [(True, True), (True, False), (False, False)])
def test_switch_ends_the_current_run_restarts_at_the_new_start_and_leaves_the_aircraft_ready(general_client, stack, armed, fly):
    client, _ = general_client
    _a_scenario, _a_version, deployment_a = deployed(client, title="Mission A")
    execution = start(client, deployment_a).json() if fly else None
    if fly:
        stack.fly()
    stack.state["vehicles"] = [vehicle(armed=armed)]
    _b_scenario, _b_version, deployment_b = deployed(client, title="Mission B")
    job = finished(client, restart(client, deployment_b, purpose="switch"))
    assert job["status"] == "succeeded" and job["purpose"] == "switch" and job["deployment_id"] == deployment_b
    assert job["reason"] == "simulation restarted for a new mission"
    relocate = [kwargs for action, kwargs in stack.manager.calls if action == "relocate"][-1]
    assert relocate["home"]["latitude"] is not None
    if fly:
        final = client.get(f"/api/mission-control/executions/{execution['id']}").json()
        assert final["state"] == "aborted" and final["reason"] == "simulation restarted for a new mission" and final["active"] is False
        assert job["result"]["ended_executions"] == ["px4-1"]
    stack.state["vehicles"] = [vehicle()]
    assert start(client, deployment_b).status_code == 201                    # B is startable at once: aircraft at its start, disarmed, ready
    audit = audit_events("simulation_switch_restart")
    assert len(audit) == 1 and audit[0][0] == "success" and audit[0][2]["purpose"] == "switch"


def test_switch_follows_the_role_rules_an_operator_needs_an_approved_version(general_client, operator_client, stack):
    general, _ = general_client
    operator, _user = operator_client
    scenario_id, version_id, deployment_id = _operator_deployment_without_approval(operator_client)
    refused = restart(operator, deployment_id, purpose="switch")
    assert refused.status_code == 403 and refused.json()["detail"]["code"] == "approval_required"
    assert "Submit version 1 for review" in refused.json()["detail"]["message"]
    assert [action for action, _ in stack.manager.calls] == []
    request_id = operator.post(f"/api/scenarios/{scenario_id}/versions/{version_id}/submit").json()["id"]
    pending = restart(operator, deployment_id, purpose="switch")
    assert pending.status_code == 403 and pending.json()["detail"]["code"] == "approval_pending"
    assert general.post(f"/api/general/scenario-approvals/{request_id}/decision", json={"decision": "approve", "reason": "Reviewed."}).status_code == 200
    assert finished(operator, restart(operator, deployment_id, purpose="switch"))["status"] == "succeeded"
    # a plain restart of the shown mission never needs the approval
    assert finished(operator, restart(operator, deployment_id))["status"] == "succeeded"


def test_the_switch_confirmation_states_that_the_flight_ends_and_the_new_start_is_used(general_client, stack):
    client, _ = general_client
    _scenario, _version, deployment_id = deployed(client)
    refused = restart(client, deployment_id, purpose="switch", confirm=False)
    message = refused.json()["detail"]["message"]
    assert refused.status_code == 422 and "current flight ends" in message and "NEW mission's start" in message and "about 20 s" in message


def test_the_role_rule_of_a_deployment_is_served_cheaply_for_the_switch_button(general_client, operator_client, stack):
    general, _ = general_client
    operator, _user = operator_client
    _scenario, _version, general_deployment = deployed(general)
    ok = general.get(f"/api/mission-control/deployments/{general_deployment}/role-rule")
    assert ok.status_code == 200 and ok.json()["role_rule"]["allowed"] is True and ok.json()["role_rule"]["code"] == "general"
    _s, _v, operator_deployment = _operator_deployment_without_approval(operator_client)
    blocked = operator.get(f"/api/mission-control/deployments/{operator_deployment}/role-rule").json()["role_rule"]
    assert blocked["allowed"] is False and blocked["code"] == "approval_required" and "Submit version 1 for review" in blocked["message"]
    assert operator.get(f"/api/mission-control/deployments/{general_deployment}/role-rule").status_code == 404


def deployed_plan_only(client, count, title):
    """A saved plan with `count` aircraft handed to Mission Control as a PLAN ONLY (no aircraft has to run for that)."""
    saved = client.post("/api/scenarios", json=plan_body(complete_plan(count), title))
    assert saved.status_code == 201, saved.text
    scenario_id, version_id = saved.json()["id"], saved.json()["versions"][0]["id"]
    deployment = client.post(f"/api/scenarios/{scenario_id}/versions/{version_id}/deploy", json={"mode": "plan_only"})
    assert deployment.status_code == 200, deployment.text
    return scenario_id, version_id, deployment.json()["id"]


# --- the staged start of several aircraft: live per-aircraft state, partial results, retry of the missing ones -------------------------------

def snapshot(states, *, finished=False, partial=False, phase="aircraft", stop="ok", gazebo="ready"):
    """One progress-file snapshot of the manager's staged start; `states` is one state per aircraft (pending|booting|sensors|ready|failed)."""
    count = len(states)
    ready = sum(state == "ready" for state in states)
    failure = None
    if finished and partial:
        failure = {"code": "partial", "message": f"{ready} of {count} aircraft are ready"}
    return {"present": True, "finished": finished, "alive": not finished, "phase": phase, "count": count, "targets": list(range(count)), "stalled_s": 0.0,
            "stop": {"state": stop, "detail": "PX4 and Gazebo are gone (0.4 s)." if stop == "ok" else ""}, "gazebo": {"state": gazebo, "detail": "The world answers (2 s)."}, "bridges": {"state": "ok", "detail": ""},
            "aircraft": {str(i): {"aircraft_id": f"px4-{i + 1}", "state": state, "detail": "PX4 exited: broke" if state == "failed" else ""} for i, state in enumerate(states)},
            "failure": failure, "timings": {}, "result": {"failure": None, "failure_code": None, "partial": partial, "bridges": {"telemetry": {"status": "running"}, "pose": {"status": "running"}}} if finished else None}


class Scripted:
    """A fake manager whose progress file changes between reads: every `progress` returns the next snapshot (the last one repeats); a snapshot may wait on a gate."""

    def __init__(self, snapshots, gates=None):
        self.snapshots, self.gates, self.index, self.calls = list(snapshots), gates or {}, 0, []

    def __call__(self, action, **kwargs):
        self.calls.append((action, kwargs))
        if action == "status":
            return {"instances": []}
        if action == "start":
            return {"present": True, "started": True, "finished": False}
        if action == "progress":
            gate = self.gates.get(self.index)
            if gate is not None:
                gate.wait(20)
            snap = self.snapshots[min(self.index, len(self.snapshots) - 1)]
            self.index += 1
            return snap
        return {}


@pytest.fixture
def five(sim, monkeypatch):
    """A 5-aircraft deployment, five vehicles standing at the mission start, and a verified-fleet fake that counts only the aircraft in `ready_ids`."""
    home._receivers(monkeypatch)
    monkeypatch.setattr("app.mission_execution.restart.POLL_S", 0.02)
    sim.state["vehicles"] = [vehicle(id=f"px4-{n}", system_id=n) for n in range(1, 6)]
    sim.ready_ids = {1, 2, 3, 4, 5}

    def verified_fleet(count):
        rows = [{"aircraft_id": f"px4-{n}", "instance_number": n - 1, "system_id": n, "gazebo_model": f"x500_{n - 1}",
                 "px4_status": "connected" if n in sim.ready_ids else "not_seen", "gazebo_status": "connected" if n in sim.ready_ids else "not_seen", "verified": n in sim.ready_ids} for n in range(1, count + 1)]
        return [row for row in rows if row["verified"]], rows

    monkeypatch.setattr("app.fleet_routes._verified_fleet", verified_fleet)
    monkeypatch.setattr("app.research_routes.reset_live_threat", lambda: None)
    monkeypatch.setattr("app.fleet_routes.RELOCATION_VERIFY_TIMEOUT_S", 0.3)
    return sim


def test_the_job_reports_every_aircraft_and_a_live_count_while_it_starts(general_client, five, monkeypatch):
    client, _ = general_client
    _scenario, _version, deployment_id = deployed_plan_only(client, 5, "Five aircraft")
    gate = threading.Event()
    manager = Scripted([snapshot(["pending"] * 5, phase="stopping", stop="running"), snapshot(["booting", "booting", "pending", "pending", "pending"]),
                        snapshot(["ready", "ready", "ready", "booting", "sensors"]), snapshot(["ready"] * 5, finished=True, phase="done")], gates={3: gate})
    monkeypatch.setattr("app.fleet_routes.invoke_fleet_manager", manager)
    five.ready_ids = set()                                                      # nothing is verified yet: telemetry has not arrived
    response = restart(client, deployment_id)
    assert response.status_code == 202
    job_id = response.json()["id"]
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and manager.index < 3:
        time.sleep(0.02)
    job = client.get(f"/api/mission-control/restart/{job_id}").json()
    assert job["status"] == "running" and job["requested_count"] == 5
    assert [row["state"] for row in job["aircraft"]] == ["starting"] * 5                      # the manager says ready for 3, but their telemetry has not arrived yet
    assert job["aircraft"][3]["detail"] == "PX4 is starting." and job["aircraft"][4]["detail"] == "PX4 started; waiting for a valid position."
    assert steps(job)["stop_sim"]["status"] == "ok" and steps(job)["start_sim"]["status"] == "running"
    assert "3 of 5 aircraft ready" in steps(job)["start_sim"]["detail"]
    five.ready_ids = {1, 2, 3, 4, 5}
    gate.set()
    done = finished(client, response)
    assert done["status"] == "succeeded" and done["ready_count"] == 5 and done["summary"] == "5 of 5 ready"
    assert [row["state"] for row in done["aircraft"]] == ["ready"] * 5 and all(item["status"] == "ok" for item in done["steps"])
    start = [kwargs for action, kwargs in manager.calls if action == "start"][0]
    assert start["count"] == 5 and start["stop_first"] is True and start["instances"] is None and start["home"]["latitude"] is not None


def test_a_partial_result_names_the_ready_and_the_missing_aircraft_and_offers_retry_and_continue(general_client, five, monkeypatch):
    client, _ = general_client
    _scenario, _version, deployment_id = deployed_plan_only(client, 5, "Partial")
    five.ready_ids = {1, 2, 3}
    manager = Scripted([snapshot(["ready", "ready", "ready", "failed", "failed"], finished=True, partial=True, phase="partial")])
    monkeypatch.setattr("app.fleet_routes.invoke_fleet_manager", manager)
    job = finished(client, restart(client, deployment_id))
    assert job["status"] == "partial" and job["partial"] is True and job["can_retry"] is False
    assert job["ready_count"] == 3 and job["missing"] == ["px4-4", "px4-5"] and job["summary"] == "3 of 5 ready; failed: px4-4, px4-5"
    assert job["failure"]["code"] == "partial_fleet" or job["failure"]["code"] == "partial"
    assert "px4-4" in job["failure"]["message"] and "px4-5" in job["failure"]["message"]
    assert job["can_retry_missing"] is True and job["can_continue"] is True
    assert not [item for item in job["steps"] if item["status"] in ("pending", "running")]        # nothing is left pending or running after the end
    audit = audit_events("simulation_restart")
    assert audit[0][0] == "partial" and [row["state"] for row in audit[0][2]["aircraft"]] == ["ready", "ready", "ready", "failed", "failed"]
    # Retry the missing ones: only px4-4 and px4-5 are started, nothing is stopped, the three that run are left alone
    five.ready_ids = {1, 2, 3, 4, 5}
    manager.snapshots = [snapshot(["ready"] * 5, finished=True, phase="done")]
    manager.index = 0
    retry = client.post(f"/api/mission-control/restart/{job['id']}/retry-missing")
    assert retry.status_code == 202 and retry.json()["retry_of"] == job["id"]
    again = finished(client, retry)
    assert again["status"] == "succeeded" and again["ready_count"] == 5
    start = [kwargs for action, kwargs in manager.calls if action == "start"][-1]
    assert start["instances"] == [3, 4] and start["stop_first"] is False and start["count"] == 5
    assert client.post(f"/api/mission-control/restart/{again['id']}/retry-missing").status_code == 409        # nothing to retry any more


def test_continue_with_the_ready_aircraft_is_audited_and_ends_the_offer(general_client, five, monkeypatch):
    client, _ = general_client
    _scenario, _version, deployment_id = deployed_plan_only(client, 5, "Continue")
    five.ready_ids = {1, 2}
    monkeypatch.setattr("app.fleet_routes.invoke_fleet_manager", Scripted([snapshot(["ready", "ready", "failed", "failed", "failed"], finished=True, partial=True, phase="partial")]))
    job = finished(client, restart(client, deployment_id))
    assert job["status"] == "partial" and job["missing"] == ["px4-3", "px4-4", "px4-5"]
    accepted = client.post(f"/api/mission-control/restart/{job['id']}/continue")
    assert accepted.status_code == 200 and accepted.json()["accepted_partial"] is True and accepted.json()["can_continue"] is False and accepted.json()["can_retry_missing"] is False
    events = audit_events("simulation_restart_partial_accepted")
    assert len(events) == 1 and events[0][2]["ready"] == ["px4-1", "px4-2"] and events[0][2]["missing"] == ["px4-3", "px4-4", "px4-5"]
    assert client.post(f"/api/mission-control/restart/{job['id']}/retry-missing").status_code == 409
    assert client.post("/api/mission-control/restart/nope/continue").status_code == 404


def test_no_aircraft_ready_is_a_failure_not_a_partial_result_and_later_steps_say_not_run(general_client, five, monkeypatch):
    client, _ = general_client
    _scenario, _version, deployment_id = deployed_plan_only(client, 5, "None ready")
    five.ready_ids = set()
    snap = snapshot(["failed"] * 5, finished=True, phase="failed")
    snap["failure"] = {"code": "no_aircraft_ready", "message": "No aircraft became ready"}
    snap["result"] = {"failure": "No aircraft became ready; waiting for px4-1 failed or stalled.", "failure_code": "no_aircraft_ready", "partial": False, "bridges": {}}
    monkeypatch.setattr("app.fleet_routes.invoke_fleet_manager", Scripted([snap]))
    job = finished(client, restart(client, deployment_id))
    assert job["status"] == "failed" and job["failure"]["step"] == "start_sim" and job["can_retry"] is True and job["can_retry_missing"] is False
    assert steps(job)["start_sim"]["status"] == "failed" and all(steps(job)[key]["status"] == "skipped" for key in ("bridges", "telemetry", "gazebo_pose", "position", "orphans"))
    assert [row["state"] for row in job["aircraft"]] == ["failed"] * 5


def test_the_job_keeps_waiting_while_aircraft_keep_coming_up_and_gives_up_only_when_nothing_changes(general_client, five, monkeypatch):
    client, _ = general_client
    _scenario, _version, deployment_id = deployed_plan_only(client, 5, "Progress")
    five.ready_ids = set()
    monkeypatch.setattr("app.fleet_routes.invoke_fleet_manager", Scripted([snapshot(["ready"] * 5, finished=True, phase="done")]))
    monkeypatch.setattr("app.fleet_routes.RELOCATION_VERIFY_TIMEOUT_S", 0.9)         # the stall window

    def bring_up():
        for number in (1, 2, 3, 4, 5):
            time.sleep(0.4)
            five.ready_ids.add(number)
    thread = threading.Thread(target=bring_up)
    thread.start()
    job = finished(client, restart(client, deployment_id))
    thread.join()
    assert job["status"] == "succeeded" and job["ready_count"] == 5                  # 2 s in total, more than the 0.9 s window, yet one more aircraft every 0.4 s kept it going
    five.ready_ids = set()                                                           # and with nothing coming up the same window gives up
    failed = finished(client, restart(client, deployment_id))
    assert failed["status"] == "failed" and failed["failure"]["code"] == "verification_timeout"


def test_a_restart_that_needs_more_aircraft_than_the_machine_runs_says_so_and_starts_nothing(general_client, five, monkeypatch):
    client, _ = general_client
    _scenario, _version, deployment_id = deployed_plan_only(client, 6, "Six aircraft")
    manager = Scripted([])
    monkeypatch.setattr("app.fleet_routes.invoke_fleet_manager", manager)
    refused = restart(client, deployment_id)
    assert refused.status_code == 409 and refused.json()["detail"]["code"] == "capacity_exceeded"
    assert refused.json()["detail"]["message"] == "This mission has 6 aircraft, this machine runs up to 5 reliably. You can start the first 5 aircraft, activate fewer, or still deploy as a plan only."
    assert manager.calls == []
