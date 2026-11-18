"""Start in simulation: role rules, pre-start checks, controls and the event record, through the real HTTP stack.

The WSL executor is replaced by `FakeExecutor`, which runs the REAL executor state machine and mission validation
(backend/tools/wsl_mission_executor.py) against the fake PX4 peer from test_mission_executor.py. No WSL, PX4 or
Gazebo process is involved. The live run is recorded in docs/OVERNIGHT_RUN_LOG.md.
"""
from __future__ import annotations

import json

import pytest

import test_mission_executor as peer
from app.mission_execution.items import MissionItemsError, build_mission
from app.research.attacks import forbidden_fields
from app.telemetry.mission_executor import build_executor_command
from test_attack_lab import _all_keys
from test_mission_planning import make_plan, offset
from test_mission_workflow_e2e import body as plan_body, complete_plan, flight  # noqa: F401  (flight is a fixture)

ex = peer.ex


class FakeExecutor:
    """Stands in for `invoke_mission_executor`: same actions, same response shapes, real state machine inside."""

    def __init__(self):
        self.runs = {}
        self.calls = []
        self.start_failure = None
        self.unreachable = False
        self.daemon_alive = True
        self.now_offset = 0.0
        self.auto_advance = 0.0

    def __call__(self, action, *, instance=0, execution_id=None, events_after=None, mission=None, timeout_seconds=20):
        build_executor_command(action, instance=instance, execution_id=execution_id, events_after=events_after)  # argument rules still apply
        self.calls.append((action, execution_id or (mission or {}).get("execution_id")))
        if self.unreachable:
            return {"failure": "WSL mission executor could not complete: TimeoutExpired", "failure_code": "executor_unreachable"}
        if action == "cleanup":     # kills every executor / landing process of the checkout and reports them
            killed = [{"kind": "executor", "pid": 4000 + number, "instance": str(number)} for number, run in self.runs.items() if not run["executor"].terminal]
            self.runs.clear()
            return {"cleanup": True, "killed": killed}
        if action == "start":
            if self.start_failure:
                return dict(self.start_failure)
            valid = ex.validate_mission(json.loads(json.dumps(mission)))
            clock = peer.Clock()
            fake = peer.FakePx4(clock)
            self.runs[instance] = {"executor": ex.MissionExecutor(fake, valid, clock=clock, wall_clock=clock), "fake": fake, "clock": clock, "mission": valid}
            return {"started": True, **self._status(instance, -1)}
        if action == "status":
            if self.auto_advance and instance in self.runs:
                self.advance(self.auto_advance, instance=instance)     # time passes on the fake PX4 while the route waits for an answer
            return self._status(instance, events_after if events_after is not None else -1)
        run = self.runs.get(instance)
        if run is None or not self.daemon_alive:
            return {"failure": "No executor is running for this aircraft.", "failure_code": "executor_not_running"}
        if run["mission"]["execution_id"] != execution_id:
            return {"failure": "A different execution is running on this aircraft.", "failure_code": "execution_mismatch"}
        answer = run["executor"].control(action)
        return {"accepted": answer["accepted"], "detail": answer["detail"], **self._status(instance, -1)}

    def _status(self, instance, after):
        run = self.runs.get(instance)
        if run is None:
            return {"status": None, "daemon_alive": False, "events": [], "now": 0.0}
        executor = run["executor"]
        return {"status": executor.status(), "daemon_alive": self.daemon_alive and not executor.terminal,
                "events": [event for event in executor.events if event["index"] > after], "now": run["clock"].now + self.now_offset}

    def advance(self, seconds, until=None, instance=0):
        run = self.runs[instance]
        return peer.run(run["executor"], run["fake"], run["clock"], seconds, until=until)

    def fly(self, instance=0):
        run = self.runs[instance]
        assert self.advance(30, until=lambda: run["executor"].state == "executing"), run["executor"].state
        return run["executor"], run["fake"]


def vehicle(**patch):
    start = offset()
    base = {"id": "px4-1", "system_id": 1, "status": "connected", "armed": False, "telemetry_age_seconds": 0.4,
            "position": {"latitude": start["latitude"], "longitude": start["longitude"], "relative_altitude_m": 0.02, "valid": True},
            "velocity": {"ground_speed_m_s": 0.0}, "gps_raw": {"valid": True, "fix_type": 3, "satellites_visible": 12},
            "battery": {"remaining_percent": 97.0}}
    base.update(patch)
    return base


@pytest.fixture
def sim(monkeypatch, flight):
    """A verified, managed, disarmed aircraft standing at the mission start, and the fake executor."""
    from app import execution_routes

    executor = FakeExecutor()
    state = {"vehicles": [vehicle()], "manager": {"instances": [{"system_id": 1, "managed": True}, {"system_id": 2, "managed": True}]}}
    monkeypatch.setattr(execution_routes, "invoke_mission_executor", executor)
    monkeypatch.setattr(execution_routes, "SYNC_MIN_INTERVAL_S", 0.0)
    monkeypatch.setattr("app.fleet_routes._fleet_snapshot", lambda: (state["vehicles"], []))
    monkeypatch.setattr("app.telemetry.fleet_manager.invoke_fleet_manager", lambda *a, **k: state["manager"])
    executor.state = state
    return executor


def deployed(client, count=1, title="Start plan", **plan_extra):
    saved = client.post("/api/scenarios", json=plan_body(complete_plan(count, **plan_extra), title))
    assert saved.status_code == 201, saved.text
    scenario_id, version_id = saved.json()["id"], saved.json()["versions"][0]["id"]
    deployment = client.post(f"/api/scenarios/{scenario_id}/versions/{version_id}/deploy")
    assert deployment.status_code == 200, deployment.text
    return scenario_id, version_id, deployment.json()["id"]


def start(client, deployment_id, confirm=True):
    return client.post("/api/mission-control/executions", json={"deployment_id": deployment_id, "confirm": confirm})


def checks(payload):
    return {item["key"]: item for item in payload["checks"]}


# --- mission items from the stored route ------------------------------------------------------------------------------

def test_stored_route_maps_to_takeoff_speed_waypoints_and_landing_and_validates_in_the_executor():
    aircraft = complete_plan(1).aircraft[0]
    mission = build_mission(aircraft, "a" * 32)
    commands = [item["command"] for item in mission["items"]]
    assert commands[0] == 22 and commands[1] == 178 and commands[-1] == 21 and set(commands[2:-1]) == {16}
    assert mission["route_total"] == len(aircraft.route.waypoints) and mission["instance"] == 0 and mission["system_id"] == 1
    assert [item["route_seq"] for item in mission["items"] if "route_seq" in item] == [w.seq for w in aircraft.route.waypoints]
    assert mission["items"][1]["speed_mps"] == aircraft.route.waypoints[1].speed_mps
    valid = ex.validate_mission(json.loads(json.dumps(mission)))   # the executor accepts exactly what the backend builds
    assert all(ex.item_fields(item)["frame"] in (6, 2) for item in valid["items"])
    with pytest.raises(MissionItemsError, match="no generated route"):
        build_mission(aircraft.model_copy(update={"route": None}), "a" * 32)


def test_a_payload_drop_route_becomes_a_hold_with_a_simulated_event_and_no_actuator_item():
    plan = make_plan("payload_dropoff", {"drop_point": offset(150, 100), "drop_altitude_m": 8, "hover_duration_s": 20, "payload_category": "medical_kit"},
                     objectives=["relief_supply_delivery"])
    from test_mission_planning import route_of
    aircraft = plan.aircraft[0].model_copy(update={"route": route_of(plan)})
    mission = build_mission(aircraft, "b" * 32)
    drops = [item for item in mission["items"] if item.get("simulated_event")]
    assert len(drops) == 1 and drops[0]["command"] == 16 and drops[0]["simulated_event"] == "payload_released" and drops[0]["hold_s"] == 20
    assert {item["command"] for item in mission["items"]} <= {16, 21, 22, 178}   # nothing that moves a servo or releases anything
    ex.validate_mission(json.loads(json.dumps(mission)))


def test_executor_commands_are_fixed_and_argument_validated():
    command = build_executor_command("status", instance=0, events_after=12)
    assert command[:6] == ["wsl.exe", "--distribution", "Ubuntu", "--exec", "python3", "/mnt/d/TRINETRA/backend/tools/wsl_mission_executor.py"]
    assert command[6:] == ["status", "--instance", "0", "--px4-root", "/home/apt8055/uav-sim/PX4-Autopilot", "--events-after", "12"]
    assert build_executor_command("abort", execution_id="c" * 32)[-2:] == ["--execution-id", "c" * 32]
    for bad in (dict(action="shell"), dict(action="abort"), dict(action="abort", execution_id="x; rm -rf /"), dict(action="status", instance=9),
                dict(action="status", execution_id="c" * 32), dict(action="pause", execution_id="c" * 32, events_after=1), dict(action="run")):
        with pytest.raises(ValueError):
            build_executor_command(**bad)


# --- GENERAL: start, progress, controls, record --------------------------------------------------------------------------

def test_general_starts_directly_and_the_run_is_recorded_from_upload_to_landing(general_client, sim):
    client, user = general_client
    _scenario, _version, deployment_id = deployed(client)
    preflight = client.get(f"/api/mission-control/executions/preflight?deployment_id={deployment_id}").json()
    assert preflight["can_start"] is True and preflight["role_rule"]["code"] == "general" and preflight["blocking"] == []
    assert list(checks(preflight)) == ["plan_valid", "role", "aircraft_count", "fleet_verified", "managed_instance", "telemetry_fresh", "gps_fix",
                                       "disarmed_landed", "at_mission_start", "battery", "no_execution_running", "endurance"]
    assert all(item["ok"] for item in preflight["checks"])
    assert preflight["confirmation"] == "This runs your route in the PX4/Gazebo simulation. The aircraft will arm, take off and fly the plan."
    assert client.get("/api/mission-control/executions/latest").json() == {"execution": None}

    unconfirmed = start(client, deployment_id, confirm=False)
    assert unconfirmed.status_code == 422 and unconfirmed.json()["detail"]["code"] == "confirmation_required"
    assert sim.calls == []
    started = start(client, deployment_id)
    assert started.status_code == 201, started.text
    execution = started.json()
    assert execution["state"] == "connecting" and execution["stage"] == "starting" and execution["active"] is True
    assert execution["started_by_name"] == user.full_name and execution["started_by_role"] == "GENERAL" and execution["simulation_only"] is True
    assert execution["controls"]["abort"] == {"allowed": True, "reason": None}
    assert execution["controls"]["pause"]["allowed"] is False and "the run is connecting" in execution["controls"]["pause"]["reason"]
    execution_id = execution["id"]

    executor, fake = sim.fly()
    flying = client.get(f"/api/mission-control/executions/{execution_id}").json()
    assert flying["state"] == "executing" and flying["stage"] == "executing" and flying["uploaded"] is True and flying["armed"] is True
    assert flying["controls"]["pause"]["allowed"] is True and flying["controls"]["abort"]["allowed"] is True
    assert flying["estimated_s"] > 0 and flying["px4_mode"] == "Mission"
    items = sim.runs[0]["mission"]["items"]
    last = len(items) - 1
    fake.reach(0); fake.current(2)
    sim.advance(2)
    progress = client.get("/api/mission-control/executions/latest").json()["execution"]
    assert progress["route_reached"] == 1 and progress["route_total"] == len([i for i in items if i["route_seq"] is not None])
    assert progress["percent"] > 0 and progress["id"] == execution_id
    for seq in range(2, last + 1):
        fake.reach(seq)
    fake.current(last, mission_state=5)
    sim.advance(2)
    fake.armed = False
    sim.advance(3)
    done = client.get(f"/api/mission-control/executions/{execution_id}").json()
    assert done["state"] == "completed" and done["stage"] == "completed" and done["active"] is False and done["percent"] == 100.0
    assert done["finished_at"] and done["controls"]["abort"] == {"allowed": False, "reason": "The run is already completed."}

    events = client.get(f"/api/mission-control/executions/{execution_id}/events").json()["events"]
    kinds = [event["type"] for event in events]
    assert kinds[0] == "start_requested" and events[0]["actor_name"] == user.full_name and events[0]["source"] == "user"
    for expected in ("upload_started", "uploaded", "mission_mode", "armed", "mission_started", "takeoff_complete", "waypoint_reached", "landed", "disarmed", "completed"):
        assert expected in kinds, expected
    assert kinds.index("uploaded") < kinds.index("armed") < kinds.index("takeoff_complete") < kinds.index("completed")
    assert len(kinds) == len(set((event["type"], event["id"]) for event in events))
    more = client.get(f"/api/mission-control/executions/{execution_id}/events?after={events[-1]['id']}").json()["events"]
    assert more == []   # nothing is stored twice, however often it is polled
    assert all(event["occurred_at"] for event in events)
    # a finished run frees the aircraft
    assert checks(client.get(f"/api/mission-control/executions/preflight?deployment_id={deployment_id}").json())["no_execution_running"]["ok"] is True


def test_pause_resume_abort_are_recorded_with_who_and_refused_in_the_wrong_state(general_client, sim):
    client, user = general_client
    _s, _v, deployment_id = deployed(client)
    execution_id = start(client, deployment_id).json()["id"]
    early = client.post(f"/api/mission-control/executions/{execution_id}/pause")
    assert early.status_code == 409 and early.json()["detail"]["code"] == "not_executing" and "the run is connecting" in early.json()["detail"]["message"]
    executor, fake = sim.fly()
    assert client.post(f"/api/mission-control/executions/{execution_id}/resume").json()["detail"]["code"] == "not_paused"
    paused = client.post(f"/api/mission-control/executions/{execution_id}/pause")
    assert paused.status_code == 200 and paused.json()["request"] == {"command": "pause", "detail": "pause accepted"}
    sim.advance(3)
    view = client.get(f"/api/mission-control/executions/{execution_id}").json()
    assert view["state"] == "paused" and view["px4_mode"] == "Hold" and view["stage"] == "executing"
    assert view["controls"]["resume"]["allowed"] is True and view["controls"]["pause"]["allowed"] is False and view["controls"]["abort"]["allowed"] is True
    assert client.post(f"/api/mission-control/executions/{execution_id}/resume").status_code == 200
    sim.advance(3)
    assert client.get(f"/api/mission-control/executions/{execution_id}").json()["state"] == "executing"
    aborted = client.post(f"/api/mission-control/executions/{execution_id}/abort")
    assert aborted.status_code == 200
    sim.advance(3)
    returning = client.get(f"/api/mission-control/executions/{execution_id}").json()
    assert returning["state"] == "returning" and returning["px4_mode"] == "Return" and returning["active"] is True
    assert 20 in fake.commands()
    fake.armed = False
    sim.advance(3)
    final = client.get(f"/api/mission-control/executions/{execution_id}").json()
    assert final["state"] == "aborted" and final["stage"] == "aborted" and final["active"] is False
    again = client.post(f"/api/mission-control/executions/{execution_id}/abort")
    assert again.status_code == 409 and again.json()["detail"]["code"] == "already_finished"
    events = client.get(f"/api/mission-control/executions/{execution_id}/events").json()["events"]
    by_user = [(event["type"], event["actor_name"]) for event in events if event["source"] == "user"]
    assert by_user == [("start_requested", user.full_name), ("pause_requested_by_user", user.full_name),
                       ("resume_requested_by_user", user.full_name), ("abort_requested_by_user", user.full_name)]
    kinds = [event["type"] for event in events]
    for expected in ("paused", "resumed", "return_to_launch", "aborted"):
        assert expected in kinds
    assert not forbidden_fields(_all_keys({"events": events, "final": final})) and "attack" not in json.dumps([events, final]).lower()


def test_one_execution_at_a_time_per_aircraft(general_client, sim):
    client, _ = general_client
    _s, _v, deployment_id = deployed(client)
    first = start(client, deployment_id)
    assert first.status_code == 201
    second = start(client, deployment_id)
    assert second.status_code == 409 and second.json()["detail"]["code"] == "preflight_failed"
    assert "already connecting on px4-1" in second.json()["detail"]["message"]
    assert checks(second.json()["detail"])["no_execution_running"]["ok"] is False
    assert [call[0] for call in sim.calls].count("start") == 1


# --- pre-start checks -------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("key, change, text", [
    ("at_mission_start", lambda s: s["vehicles"][0]["position"].update(latitude=47.397971, longitude=8.546164), "Use Restart at mission start"),
    ("disarmed_landed", lambda s: s["vehicles"][0].update(armed=True), "The aircraft is armed."),
    ("disarmed_landed", lambda s: s["vehicles"][0]["position"].update(relative_altitude_m=12.0), "not on the ground"),
    ("battery", lambda s: s["vehicles"][0]["battery"].update(remaining_percent=39.0), "Battery 39%"),
    ("telemetry_fresh", lambda s: s["vehicles"][0].update(telemetry_age_seconds=6.0), "needs 3 s or newer"),
    ("telemetry_fresh", lambda s: s["vehicles"][0].update(status="stale"), "Telemetry is stale"),
    ("gps_fix", lambda s: s["vehicles"][0]["gps_raw"].update(fix_type=1), "No valid 3D GPS fix"),
    ("managed_instance", lambda s: s["manager"]["instances"][0].update(managed=False), "was not started by the TRINETRA fleet manager"),
    ("fleet_verified", lambda s: s["manager"].update(instances=[]), "is not fully verified"),
    ("fleet_verified", lambda s: s["manager"].update(failure="WSL fleet manager could not complete: OSError"), "WSL fleet manager"),
])
def test_each_failed_pre_start_check_blocks_the_start_with_its_reason(general_client, sim, key, change, text):
    client, _ = general_client
    _s, _v, deployment_id = deployed(client)
    change(sim.state)
    preflight = client.get(f"/api/mission-control/executions/preflight?deployment_id={deployment_id}").json()
    assert preflight["can_start"] is False and checks(preflight)[key]["ok"] is False and text in checks(preflight)[key]["detail"]
    assert any(text in line for line in preflight["blocking"])
    if key == "at_mission_start":
        assert checks(preflight)[key]["offer_relocation"] is True
    refused = start(client, deployment_id)
    assert refused.status_code == 409 and refused.json()["detail"]["code"] == "preflight_failed"
    assert checks(refused.json()["detail"])[key]["ok"] is False
    assert sim.calls == [] and client.get("/api/mission-control/executions/latest").json() == {"execution": None}


def deployed_roles(client, roles, title="Roles plan"):
    """A saved and deployed plan whose aircraft have the given roles (the first must be the primary)."""
    plan = complete_plan(len(roles))
    plan = plan.model_copy(update={"aircraft": [item.model_copy(update={"role": role}) for item, role in zip(plan.aircraft, roles)]})
    saved = client.post("/api/scenarios", json=plan_body(plan, title))
    assert saved.status_code == 201, saved.text
    deployment = client.post(f"/api/scenarios/{saved.json()['id']}/versions/{saved.json()['versions'][0]['id']}/deploy")
    assert deployment.status_code == 200, deployment.text
    return deployment.json()["id"]


def test_a_standby_aircraft_stays_on_the_ground_and_a_plan_over_the_endurance_limit_is_not_started(general_client, sim, monkeypatch):
    client, _ = general_client
    sim.state["vehicles"].append(vehicle(id="px4-2", system_id=2))
    _s, _v, two = deployed(client, count=2, title="Two aircraft")                     # primary + standby
    preflight = client.get(f"/api/mission-control/executions/preflight?deployment_id={two}").json()
    assert checks(preflight)["aircraft_count"]["ok"] is True and "stay on the ground (standby)" in checks(preflight)["aircraft_count"]["detail"]
    assert [(item["aircraft_id"], item["role"], item["flies"]) for item in preflight["fleet"]] == [("px4-1", "primary", True), ("px4-2", "standby", False)]
    started = start(client, two)
    assert started.status_code == 201 and "executions" not in started.json() and [call for call in sim.calls if call[0] == "start"] and len(sim.runs) == 1
    _s, _v, one = deployed(client, title="Tight limit")
    from app import config
    monkeypatch.setattr(config, "MP_MAX_FLIGHT_MINUTES", 1)
    tight = client.get(f"/api/mission-control/executions/preflight?deployment_id={one}").json()
    assert checks(tight)["endurance"]["ok"] is False and checks(tight)["plan_valid"]["ok"] is False
    assert "the aircraft limit is 1 min" in checks(tight)["plan_valid"]["detail"]
    starts = lambda: [call for call in sim.calls if call[0] == "start"]      # noqa: E731  (status polls of the running aircraft do not count)
    before = len(starts())
    assert start(client, one).status_code == 409 and len(starts()) == before


def test_a_primary_and_a_decoy_fly_together_each_with_its_own_run(general_client, sim):
    client, _ = general_client
    sim.state["vehicles"].append(vehicle(id="px4-2", system_id=2))
    deployment_id = deployed_roles(client, ["primary", "decoy"], "Decoy plan")
    preflight = client.get(f"/api/mission-control/executions/preflight?deployment_id={deployment_id}").json()
    assert preflight["can_start"] is True and [item["flies"] for item in preflight["fleet"]] == [True, True]
    response = start(client, deployment_id)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["aircraft_id"] == "px4-1" and [item["aircraft_id"] for item in body["executions"]] == ["px4-1", "px4-2"] and body["not_started"] == []
    assert len({item["id"] for item in body["executions"]}) == 2 and sorted(sim.runs) == [0, 1]
    sim.fly(0)                                                           # (the fake PX4 peer only models system 1: the second run is checked as started, not flown)
    first = client.get(f"/api/mission-control/executions/{body['executions'][0]['id']}").json()
    second = client.get(f"/api/mission-control/executions/{body['executions'][1]['id']}").json()
    assert first["state"] == "executing" and second["state"] not in ("failed", "aborted") and first["aircraft_id"] != second["aircraft_id"]
    assert client.post(f"/api/mission-control/executions/{body['executions'][0]['id']}/pause").status_code == 200       # each run has its own controls
    sim.advance(3, instance=0)
    assert client.get(f"/api/mission-control/executions/{body['executions'][0]['id']}").json()["state"] == "paused"
    assert client.get(f"/api/mission-control/executions/{body['executions'][1]['id']}").json()["state"] != "paused"
    fleet = client.get(f"/api/mission-control/executions/fleet?deployment_id={deployment_id}").json()
    assert [(row["aircraft_id"], row["role"]) for row in fleet["executions"]] == [("px4-1", "primary"), ("px4-2", "decoy")] and fleet["on_the_ground"] == []
    assert fleet["executions"][0]["controls"]["resume"]["allowed"] is True and fleet["executions"][1]["controls"]["resume"]["allowed"] is False   # separate controls


def test_a_decoy_that_cannot_start_does_not_stop_the_primary_and_is_reported(general_client, sim):
    client, _ = general_client
    sim.state["vehicles"].append(vehicle(id="px4-2", system_id=2))
    deployment_id = deployed_roles(client, ["primary", "decoy"], "Decoy fail plan")
    real = sim.__call__

    def flaky(action, **kwargs):
        if action == "start" and kwargs.get("instance") == 1:
            return {"failure": "px4-2 would not arm", "failure_code": "executor_error"}
        return real(action, **kwargs)
    from app import execution_routes
    import pytest as _pytest
    with _pytest.MonkeyPatch.context() as patch:
        patch.setattr(execution_routes, "invoke_mission_executor", flaky)
        body = start(client, deployment_id).json()
    assert [item["aircraft_id"] for item in body["executions"]] == ["px4-1"] if "executions" in body else body["aircraft_id"] == "px4-1"
    assert body["not_started"][0]["aircraft_id"] == "px4-2" and "would not arm" in body["not_started"][0]["reason"]


def test_a_missing_decoy_blocks_the_whole_start_and_commands_nothing(general_client, sim):
    client, _ = general_client                                           # px4-2 has no telemetry
    deployment_id = deployed_roles(client, ["primary", "decoy"], "Missing decoy")
    refused = start(client, deployment_id)
    assert refused.status_code == 409 and any(text.startswith("px4-2:") for text in refused.json()["detail"]["blocking"]) and not [call for call in sim.calls if call[0] == "start"]


# --- OPERATOR: approval per version ----------------------------------------------------------------------------------------

def _operator_deployment_without_approval(operator_client):
    """A deployment record for an operator version that no General approved (the deploy route itself refuses this)."""
    from app.persistence.database import save_mission_deployment

    operator, user = operator_client
    saved = operator.post("/api/scenarios", json=plan_body(complete_plan(), "Operator start")).json()
    version = saved["versions"][0]
    record = save_mission_deployment(saved["id"], version["id"], user.id, "GENERAL", {
        "mission_id": saved["id"], "scenario_title": "Operator start", "scenario_version_hash": version["config_hash"],
        "scenario_version_number": 1, "intent": version["intent"], "execution_status": "DEPLOYED_TO_MISSION_CONTROL_NOT_UPLOADED_TO_PX4"})
    return saved["id"], version["id"], record["id"]


def test_operator_cannot_start_before_approval_and_can_after_approval_of_that_version(general_client, operator_client, sim):
    general, _ = general_client
    operator, user = operator_client
    scenario_id, version_id, deployment_id = _operator_deployment_without_approval(operator_client)

    never = start(operator, deployment_id)
    assert never.status_code == 403 and never.json()["detail"]["code"] == "approval_required"
    assert "Submit version 1 for review" in never.json()["detail"]["message"]
    preflight = operator.get(f"/api/mission-control/executions/preflight?deployment_id={deployment_id}").json()
    assert preflight["can_start"] is False and preflight["role_rule"]["allowed"] is False and checks(preflight)["role"]["ok"] is False
    assert preflight["blocking"][0] == never.json()["detail"]["message"]   # the UI shows the same sentence the API returns

    request_id = operator.post(f"/api/scenarios/{scenario_id}/versions/{version_id}/submit").json()["id"]
    pending = start(operator, deployment_id)
    assert pending.status_code == 403 and pending.json()["detail"]["code"] == "approval_pending"
    assert sim.calls == []

    assert general.post(f"/api/general/scenario-approvals/{request_id}/decision", json={"decision": "approve", "reason": "Reviewed."}).status_code == 200
    allowed = operator.get(f"/api/mission-control/executions/preflight?deployment_id={deployment_id}").json()
    assert allowed["can_start"] is True and allowed["role_rule"]["code"] == "approved"
    started = start(operator, deployment_id)
    assert started.status_code == 201, started.text
    assert started.json()["started_by_role"] == "OPERATOR" and started.json()["started_by_name"] == user.full_name
    sim.fly()
    execution_id = started.json()["id"]
    assert operator.post(f"/api/mission-control/executions/{execution_id}/pause").status_code == 200
    # a General sees and can abort an operator's run
    assert general.get("/api/mission-control/executions/latest").json()["execution"]["id"] == execution_id
    assert general.post(f"/api/mission-control/executions/{execution_id}/abort").status_code == 200


def test_operator_cannot_start_a_rejected_or_a_superseded_version(general_client, operator_client, sim):
    general, _ = general_client
    operator, _user = operator_client
    scenario_id, version_id, deployment_id = _operator_deployment_without_approval(operator_client)
    request_id = operator.post(f"/api/scenarios/{scenario_id}/versions/{version_id}/submit").json()["id"]
    assert general.post(f"/api/general/scenario-approvals/{request_id}/decision", json={"decision": "reject", "reason": "Too close to the boundary."}).status_code == 200
    rejected = start(operator, deployment_id)
    assert rejected.status_code == 403 and rejected.json()["detail"]["code"] == "approval_rejected"
    assert "was rejected by a General" in rejected.json()["detail"]["message"]

    # an approved, deployed version that has since been superseded: the approval does not carry over
    saved = operator.post("/api/scenarios", json=plan_body(complete_plan(), "Operator second")).json()
    first = saved["versions"][0]["id"]
    approval = operator.post(f"/api/scenarios/{saved['id']}/versions/{first}/submit").json()["id"]
    assert general.post(f"/api/general/scenario-approvals/{approval}/decision", json={"decision": "approve", "reason": "ok"}).status_code == 200
    deployment = operator.post(f"/api/scenarios/{saved['id']}/versions/{first}/deploy").json()["id"]
    newer = operator.put(f"/api/scenarios/{saved['id']}", json=plan_body(complete_plan().model_copy(update={"notes": "v2"}), "Operator second")).json()
    assert newer["versions"][0]["approval"] is None
    superseded = start(operator, deployment)
    assert superseded.status_code == 409 and superseded.json()["detail"]["code"] == "version_superseded"
    assert "superseded by version 2" in superseded.json()["detail"]["message"]
    assert start(general, deployment).status_code == 409   # superseded is refused for a General too
    # the new version cannot even be deployed until it is approved, so there is nothing to start
    assert operator.post(f"/api/scenarios/{saved['id']}/versions/{newer['versions'][0]['id']}/deploy").json()["detail"]["code"] == "approval_required"
    assert sim.calls == []


def test_a_general_starts_an_operators_deployment_directly_and_other_operators_see_nothing(general_client, operator_client, sim):
    from conftest import OPERATOR_PASSWORD, register_and_approve
    from fastapi.testclient import TestClient
    from app.main import app

    general, _ = general_client
    _scenario, _version, deployment_id = _operator_deployment_without_approval(operator_client)
    assert start(general, deployment_id).status_code == 201   # a General needs no approval
    other = register_and_approve(general_client)
    stranger = TestClient(app)
    login = stranger.post("/api/auth/login", json={"username": other.username, "password": OPERATOR_PASSWORD})
    stranger.headers.update({"X-CSRF-Token": login.json()["csrf_token"]})
    execution_id = general.get("/api/mission-control/executions/latest").json()["execution"]["id"]
    assert start(stranger, deployment_id).status_code == 404
    assert stranger.get(f"/api/mission-control/executions/{execution_id}").status_code == 404
    assert stranger.post(f"/api/mission-control/executions/{execution_id}/abort").status_code == 404
    assert stranger.get("/api/mission-control/executions/latest").json() == {"execution": None}
    assert TestClient(app).post("/api/mission-control/executions", json={"deployment_id": deployment_id, "confirm": True}).status_code == 401
    csrf = general.headers.pop("X-CSRF-Token")
    assert general.post(f"/api/mission-control/executions/{execution_id}/abort").status_code == 403
    general.headers["X-CSRF-Token"] = csrf


# --- executor refusals and lost links ---------------------------------------------------------------------------------------

def test_an_executor_refusal_fails_the_run_with_the_structured_code(general_client, sim):
    client, _ = general_client
    _s, _v, deployment_id = deployed(client)
    sim.start_failure = {"failure_code": "instance_not_managed", "failure": "PX4 instance 0 was not started by the TRINETRA fleet manager, so it is not commanded."}
    refused = start(client, deployment_id)
    assert refused.status_code == 409 and refused.json()["detail"]["code"] == "instance_not_managed"
    execution = client.get("/api/mission-control/executions/latest").json()["execution"]
    assert execution["state"] == "failed" and execution["active"] is False and "not started by the TRINETRA fleet manager" in execution["reason"]
    sim.start_failure = {"failure_code": "pymavlink_missing", "failure": "pymavlink is not installed in WSL."}
    assert start(client, deployment_id).status_code == 503


def test_link_lost_is_shown_when_the_executor_stops_answering_or_dies(general_client, sim, monkeypatch):
    from app import execution_routes

    client, _ = general_client
    _s, _v, deployment_id = deployed(client)
    execution_id = start(client, deployment_id).json()["id"]
    sim.fly()
    assert client.get(f"/api/mission-control/executions/{execution_id}").json()["state"] == "executing"
    sim.unreachable = True
    assert client.get(f"/api/mission-control/executions/{execution_id}").json()["state"] == "executing"   # one missed poll is not a lost link
    monkeypatch.setattr(execution_routes, "EXECUTOR_STALE_S", -1.0)
    lost = client.get(f"/api/mission-control/executions/{execution_id}").json()
    assert lost["state"] == "link_lost" and lost["stage"] == "link_lost" and lost["active"] is True
    assert "cannot be reached" in lost["reason"] and lost["controls"]["abort"]["allowed"] is True   # abort stays available
    # the executor comes back: the run is shown as it really is again
    sim.unreachable = False
    monkeypatch.setattr(execution_routes, "EXECUTOR_STALE_S", 10.0)
    assert client.get(f"/api/mission-control/executions/{execution_id}").json()["state"] == "executing"
    # the executor process dies mid-run: final, and never reported as executing
    sim.daemon_alive = False
    dead = client.get(f"/api/mission-control/executions/{execution_id}").json()
    assert dead["state"] == "link_lost" and dead["active"] is False and "process stopped before the run finished" in dead["reason"]
    kinds = [event["type"] for event in client.get(f"/api/mission-control/executions/{execution_id}/events").json()["events"]]
    assert kinds.count("link_lost") == 2


def test_px4_heartbeat_loss_reported_by_the_executor_is_shown_as_link_lost(general_client, sim):
    client, _ = general_client
    _s, _v, deployment_id = deployed(client)
    execution_id = start(client, deployment_id).json()["id"]
    executor, fake = sim.fly()
    fake.alive = False
    sim.advance(12)
    view = client.get(f"/api/mission-control/executions/{execution_id}").json()
    assert view["state"] == "link_lost" and view["active"] is True and "no PX4 heartbeat for 10 s" in view["reason"]
    assert view["px4_heartbeat_age_s"] > 10


def test_legacy_deployments_and_unknown_ids_are_refused(general_client, sim):
    from app.persistence.database import create_scenario, save_mission_deployment
    from test_mission_workflow_e2e import CONFIG

    client, user = general_client
    legacy = create_scenario(user.id, "Old", CONFIG, {
        "mission_objectives": ["x"], "mission_description": "legacy", "proposed_tasks": [], "uav_role_suggestions": [], "operating_area_notes": "",
        "environment_notes": "", "constraints": [], "geographic_mission": {"objective": "inspection", "uav_assignments": [], "survey_zones": [], "constraints": [], "ai_recommendations": []}})
    version = legacy["versions"][0]
    record = save_mission_deployment(legacy["id"], version["id"], user.id, "GENERAL", {"mission_id": legacy["id"], "scenario_title": "Old",
                                     "scenario_version_hash": version["config_hash"], "intent": version["intent"], "execution_status": "x"})
    preflight = client.get(f"/api/mission-control/executions/preflight?deployment_id={record['id']}").json()
    assert preflight["can_start"] is False and "legacy (schema 1)" in preflight["blocking"][0]
    assert start(client, record["id"]).status_code == 409
    assert start(client, "missing").status_code == 404
    assert client.get("/api/mission-control/executions/preflight?deployment_id=missing").status_code == 404
    assert client.get("/api/mission-control/executions/" + "0" * 32).status_code == 404


# --- mission switching: the active execution's deployment is served beside the latest one ---------------------------------

def test_latest_deployment_also_serves_the_deployment_of_the_newest_execution(general_client, sim):
    client, _ = general_client
    empty = client.get("/api/mission-control/deployments/latest").json()
    assert empty == {"deployment": None, "execution": None, "execution_deployment": None}
    _s1, _v1, first_id = deployed(client, title="First flight")
    idle = client.get("/api/mission-control/deployments/latest").json()
    assert idle["deployment"]["id"] == first_id and idle["execution"] is None and idle["execution_deployment"] is None
    execution_id = start(client, first_id).json()["id"]
    sim.fly()
    # a second deployment while the flight is running is always allowed and does not touch the flight
    _s2, _v2, second_id = deployed(client, title="Second mission")
    assert second_id != first_id
    during = client.get("/api/mission-control/deployments/latest").json()
    assert during["deployment"]["id"] == second_id                                    # the latest deployment is the new one ...
    assert during["execution"]["id"] == execution_id and during["execution"]["active"] is True
    assert during["execution_deployment"]["id"] == first_id                           # ... and the flight's own deployment is served with it
    assert during["execution_deployment"]["scenario_version_number"] == 1 and during["execution_deployment"]["legacy"] is False
    assert client.get(f"/api/mission-control/executions/{execution_id}").json()["active"] is True    # the flight was not disturbed
    # starting the new mission while the first flies stays refused, with the reason
    refused = start(client, second_id)
    assert refused.status_code == 409 and "already" in refused.json()["detail"]["message"]
    # after an abort and landing the execution is final: the client may offer "Switch to new mission"
    assert client.post(f"/api/mission-control/executions/{execution_id}/abort").status_code == 200
    sim.advance(3)
    sim.runs[0]["fake"].armed = False
    sim.advance(3)
    assert client.get(f"/api/mission-control/executions/{execution_id}").json()["active"] is False
    after = client.get("/api/mission-control/deployments/latest").json()
    assert after["execution"]["active"] is False and after["execution"]["state"] == "aborted" and after["execution_deployment"]["id"] == first_id
    assert after["deployment"]["id"] == second_id


def test_the_deployment_overview_follows_ownership(general_client, operator_client, sim):
    client, _ = general_client
    _s, _v, deployment_id = deployed(client, title="General flight")
    start(client, deployment_id)
    other = operator_client[0] if isinstance(operator_client, tuple) else operator_client
    body = other.get("/api/mission-control/deployments/latest").json()
    assert body["deployment"] is None and body["execution"] is None and body["execution_deployment"] is None        # an operator does not see the General's data
    assert client.get("/api/mission-control/deployments/latest").json()["execution_deployment"]["id"] == deployment_id
