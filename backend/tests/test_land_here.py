"""Land here: land at the current position and disarm (not a motor kill, not return to launch).

The state machine runs against the fake PX4 peer of test_mission_executor.py; the HTTP route runs with the fake executor of
test_mission_execution_api.py. The live SITL behaviour (landing from 50 m, timings) is recorded in docs/OVERNIGHT_RUN_LOG.md.
"""
from __future__ import annotations

import pytest

import test_mission_executor as peer
from app.mission_execution.ground import ground_state
from app.telemetry.mission_executor import build_executor_command
from test_mission_execution_api import FakeExecutor, deployed, sim, start, vehicle  # noqa: F401  (sim is a fixture)
from test_mission_workflow_e2e import flight  # noqa: F401  (fixture)

ex = peer.ex


def landing_setup(landed=2, rel_alt=40.0, speed=6.0):
    executor, fake, clock = peer.make()
    peer.fly_to_executing(executor, fake, clock)
    fake.landed, fake.rel_alt, fake.speed = landed, rel_alt, speed
    peer.run(executor, fake, clock, 1.0)
    return executor, fake, clock


def touchdown(fake, executor, clock, disarm=True):
    fake.landed, fake.rel_alt, fake.speed = 1, 0.0, 0.0
    peer.run(executor, fake, clock, 1.0)
    if disarm:
        fake.armed = False


# --- the state machine -----------------------------------------------------------------------------------------------

def test_land_in_the_air_sets_px4_land_mode_not_rtl_and_ends_as_aborted_landed_in_place():
    executor, fake, clock = landing_setup()
    before = list(fake.commands())
    answer = executor.control("land")
    assert answer["accepted"] is True and executor.state == "landing"
    peer.run(executor, fake, clock, 1.0)
    sent = fake.sent[len(before) + 0:]
    modes = [entry for entry in sent if entry[0] == "command" and entry[1] == 176]
    assert modes and modes[0][2][2] == 6.0                                         # DO_SET_MODE, AUTO sub-mode 6 = Land
    assert 20 not in fake.commands()[len(before):]                               # never Return to launch
    assert executor.status()["land_result"] == "accepted" and "land_accepted" in peer.types(executor)
    touchdown(fake, executor, clock)
    peer.run(executor, fake, clock, 2.0)
    assert (executor.state, executor.reason) == ("aborted", "landed in place") and executor.terminal
    assert executor.events[-1]["data"]["reason"] == "landed in place"


def test_landing_keeps_the_one_hertz_gcs_heartbeat_going():
    executor, fake, clock = landing_setup()
    executor.control("land")
    start_time = clock.now
    peer.run(executor, fake, clock, 20.0)
    beats = [t for t in fake.heartbeats_received if t >= start_time]
    assert len(beats) >= 19 and max(b - a for a, b in zip(beats, beats[1:])) <= 1.1


def test_px4_is_asked_for_the_landed_state_at_two_hertz_once_connected():
    executor, fake, clock = landing_setup()
    assert fake.stream_requests and fake.stream_requests[0][:2] == (245.0, 500000.0)
    assert 511 in ex.SENDABLE_COMMANDS


def test_armed_on_the_ground_gets_a_normal_disarm_at_once():
    executor, fake, clock = peer.make()
    peer.fly_to_executing(executor, fake, clock)
    fake.landed, fake.rel_alt, fake.speed = 1, 0.0, 0.0
    peer.run(executor, fake, clock, 1.0)
    executor.control("land")
    peer.run(executor, fake, clock, 1.0)
    disarms = [entry for entry in fake.sent if entry[0] == "command" and entry[1] == 400 and entry[2][0] == 0.0]
    assert len(disarms) == 1 and disarms[0][2][1] == 0.0                         # param1 = 0 disarm, param2 = 0: never the force flag
    assert (executor.state, executor.reason) == ("aborted", "landed in place")


def test_still_armed_after_touchdown_gets_one_normal_disarm_after_a_few_seconds_never_in_the_air():
    executor, fake, clock = landing_setup()
    executor.control("land")
    peer.run(executor, fake, clock, 5.0)
    assert not [entry for entry in fake.sent if entry[0] == "command" and entry[1] == 400 and entry[2][0] == 0.0]     # in the air: nothing
    fake.landed, fake.rel_alt, fake.speed = 1, 0.0, 0.0
    peer.run(executor, fake, clock, 2.0)
    assert not [entry for entry in fake.sent if entry[0] == "command" and entry[1] == 400 and entry[2][0] == 0.0]     # not yet
    peer.run(executor, fake, clock, 4.0, until=lambda: executor.terminal)
    disarms = [entry for entry in fake.sent if entry[0] == "command" and entry[1] == 400 and entry[2][0] == 0.0]
    assert len(disarms) == 1 and disarms[0][2][1] == 0.0 and executor.state == "aborted"


def test_pressing_land_twice_sends_one_command():
    executor, fake, clock = landing_setup()
    executor.control("land")
    peer.run(executor, fake, clock, 0.5)
    count = len([entry for entry in fake.sent if entry[0] == "command" and entry[1] == 176 and entry[2][2] == 6.0])
    second = executor.control("land")
    assert second == {"accepted": True, "detail": "already landing"}
    peer.run(executor, fake, clock, 0.5)
    assert len([entry for entry in fake.sent if entry[0] == "command" and entry[1] == 176 and entry[2][2] == 6.0]) == count == 1


def test_a_refusal_keeps_px4s_exact_reason_and_the_values_and_the_run_goes_on():
    executor, fake, clock = landing_setup()
    fake.results[176] = 2
    fake.say("Land denied: no position estimate")
    peer.run(executor, fake, clock, 0.2)
    executor.control("land")
    peer.run(executor, fake, clock, 1.0)
    result = executor.status()["land_result"]
    assert result.startswith("refused: PX4 answered Land with MAV_RESULT_DENIED") and "Land denied: no position estimate" in result and "relative altitude 40.0 m" in result
    assert executor.state == "executing" and "land_rejected" in peer.types(executor)


def test_no_answer_to_the_land_command_is_reported_not_hidden():
    executor, fake, clock = landing_setup()
    fake.answer_commands = False
    executor.control("land")
    peer.run(executor, fake, clock, 6.0)
    assert executor.status()["land_result"].startswith("refused: PX4 answered Land with no answer")
    assert executor.state == "executing"


def test_without_the_landed_state_it_falls_back_to_altitude_and_speed_and_says_so():
    executor, fake, clock = landing_setup(landed=None, rel_alt=40.0, speed=6.0)
    assert executor.on_ground() is False and executor.on_ground_source() == "altitude_speed_fallback"
    executor.control("land")
    peer.run(executor, fake, clock, 1.0)
    fake.rel_alt, fake.speed = 0.1, 0.0
    peer.run(executor, fake, clock, 6.0, until=lambda: executor.terminal)
    assert "landed_state_missing" in peer.types(executor)
    assert executor.state == "aborted" and executor.status()["on_ground_source"] == "altitude_speed_fallback"


def test_px4_landed_state_wins_over_a_wrong_relative_altitude_far_from_home():
    executor, fake, clock = landing_setup(landed=1, rel_alt=70.0, speed=0.0)      # 70 m "up" is the flat-world offset; PX4 says on the ground
    assert executor.on_ground() is True and executor.on_ground_source() == "px4_landed_state"


def test_abort_is_not_available_while_landing_and_land_is_refused_when_disarmed():
    executor, fake, clock = landing_setup()
    executor.control("land")
    assert executor.control("abort")["accepted"] is False and executor.state == "landing"
    idle, idle_fake, idle_clock = peer.make()
    assert idle.control("land") == {"accepted": False, "detail": "the aircraft is already disarmed"}


def test_land_works_from_returning_after_an_abort():
    executor, fake, clock = landing_setup()
    executor.control("abort")
    peer.run(executor, fake, clock, 1.0)
    assert executor.state == "returning"
    executor.control("land")
    peer.run(executor, fake, clock, 1.0)
    assert executor.state == "landing" and fake.mode == peer.custom_mode(6)


# --- the ground rule ----------------------------------------------------------------------------------------------------

def test_ground_state_prefers_the_landed_state_and_only_falls_back_when_it_is_missing_or_stale():
    far = vehicle(armed=True, position={"latitude": 1, "longitude": 1, "relative_altitude_m": 70.0, "valid": True}, landed_state={"value": 1, "name": "on_ground", "age_s": 0.4})
    assert ground_state(far) == {"known": True, "connected": True, "armed": True, "on_ground": True, "landed_state": "on_ground", "source": "px4_landed_state"}
    stale = vehicle(armed=True, position={"latitude": 1, "longitude": 1, "relative_altitude_m": 0.1, "valid": True}, landed_state={"value": 2, "name": "in_air", "age_s": 30.0})
    result = ground_state(stale)
    assert result["source"] == "altitude_speed_fallback" and result["on_ground"] is True
    assert ground_state(vehicle(armed=True, landed_state={"value": 2, "name": "in_air", "age_s": 1.0}))["on_ground"] is False


def test_the_executor_adapter_takes_land_with_or_without_an_execution_id():
    with_id = build_executor_command("land", instance=0, execution_id="a" * 32)
    assert "--execution-id" in with_id and "land" in with_id
    assert "--execution-id" not in build_executor_command("land", instance=1)
    with pytest.raises(ValueError):
        build_executor_command("land", instance=0, execution_id="nope")


# --- the HTTP route -----------------------------------------------------------------------------------------------------

class LandExecutor:
    """Wraps the fake executor: records the actions that the route sends without a run (land / disarm)."""

    def __init__(self, inner):
        self.inner, self.calls, self.answer = inner, [], {"accepted": True, "detail": "landing started"}

    def __call__(self, action, **kwargs):
        if action in ("land", "disarm") and kwargs.get("execution_id") is None:
            self.calls.append((action, kwargs.get("instance")))
            return dict(self.answer)
        return self.inner(action, **kwargs)


@pytest.fixture
def lander(sim, monkeypatch):
    from app import execution_routes

    holder = LandExecutor(sim)
    monkeypatch.setattr(execution_routes, "invoke_mission_executor", holder)
    holder.sim = sim
    return holder


def air(**patch):
    return vehicle(armed=True, position={"latitude": 12.97, "longitude": 77.59, "relative_altitude_m": 30.0, "valid": True}, velocity={"ground_speed_m_s": 7.0}, **patch)


def audit_events(event_type):
    """(outcome, reason, metadata) of every audit event of one type, read straight from the audit table."""
    import json

    from app.persistence.database import AuditLogRecord, SessionLocal

    with SessionLocal() as session:
        return [(row.outcome, row.reason, json.loads(row.metadata_json)) for row in session.query(AuditLogRecord).filter_by(event_type=event_type)]


def land(client, aircraft="px4-1", confirm=True):
    return client.post(f"/api/mission-control/aircraft/{aircraft}/land", json={"confirm": confirm})


def test_land_needs_a_confirmation_and_names_what_it_does(general_client, lander):
    client, _ = general_client
    lander.sim.state["vehicles"] = [air()]
    refused = land(client, confirm=False)
    assert refused.status_code == 422 and refused.json()["detail"]["code"] == "confirmation_required"
    assert "lands the aircraft where it is now and disarms it" in refused.json()["detail"]["message"]
    assert lander.calls == []


def test_land_in_the_air_without_a_run_opens_its_own_onboard_connection_and_logs_an_audit_event(general_client, lander):
    client, _ = general_client
    lander.sim.state["vehicles"] = [air()]
    response = land(client)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["mode"] == "air" and body["state"] == "landing" and body["forced"] is False and body["before"]["relative_altitude_m"] == 30.0
    assert lander.calls == [("land", 0)]
    audit = audit_events("aircraft_land_here")
    assert len(audit) == 1 and audit[0][0] == "success" and audit[0][2]["mode"] == "air" and audit[0][2]["before"]["relative_altitude_m"] == 30.0 and audit[0][2]["forced"] is False


def test_armed_on_the_ground_without_a_run_sends_the_normal_disarm(general_client, lander):
    client, _ = general_client
    lander.sim.state["vehicles"] = [vehicle(armed=True)]
    lander.answer = {"disarm": True, "accepted": True, "ack": "ACCEPTED", "armed": False, "px4_messages": []}
    body = land(client).json()
    assert body["mode"] == "ground" and body["state"] == "disarmed" and lander.calls == [("disarm", 0)]


def test_land_during_a_run_goes_through_the_execution_which_ends_as_aborted_landed_in_place(general_client, lander):
    client, _ = general_client
    _scenario, _version, deployment_id = deployed(client)
    execution = start(client, deployment_id).json()
    run, fake = lander.sim.fly()
    fake.landed, fake.rel_alt, fake.speed = 2, 30.0, 7.0
    lander.sim.advance(1.0)
    lander.sim.state["vehicles"] = [air(landed_state={"value": 2, "name": "in_air", "age_s": 0.3})]
    response = land(client)
    assert response.status_code == 200, response.text
    assert response.json()["execution_id"] == execution["id"] and response.json()["execution"]["state"] == "landing"
    assert lander.calls == []                                                        # the run's own executor link was used
    assert 20 not in fake.commands()                                                 # never Return to launch
    fake.landed, fake.rel_alt, fake.speed = 1, 0.0, 0.0
    lander.sim.advance(1.0)
    fake.armed = False
    lander.sim.advance(2.0)
    final = client.get(f"/api/mission-control/executions/{execution['id']}").json()
    assert final["state"] == "aborted" and final["reason"] == "landed in place" and final["active"] is False
    events = client.get(f"/api/mission-control/executions/{execution['id']}/events").json()["events"]
    assert [item["type"] for item in events if item["source"] == "user" and item["type"] == "land_requested_by_user"]
    mine = next(item for item in events if item["type"] == "land_requested_by_user")
    assert mine["actor_name"] and mine["data"]["before"]["relative_altitude_m"] == 30.0 and mine["data"]["mode"] == "air"
    assert any(item["type"] == "aborted" and item["source"] == "executor" for item in events)


def test_double_press_sends_one_command(general_client, lander):
    client, _ = general_client
    _scenario, _version, deployment_id = deployed(client)
    start(client, deployment_id)
    run, fake = lander.sim.fly()
    lander.sim.state["vehicles"] = [air()]
    first = land(client)
    second = land(client)
    assert first.status_code == 200 and second.status_code == 200 and second.json()["already_landing"] is True
    assert len([entry for entry in fake.sent if entry[0] == "command" and entry[1] == 176 and entry[2][2] == 6.0]) == 1


def test_standalone_double_press_is_idempotent(general_client, lander):
    client, _ = general_client
    lander.sim.state["vehicles"] = [air()]
    lander.answer = {"accepted": True, "already_landing": True}
    assert land(client).json()["already_landing"] is True


def test_px4_refusal_during_a_run_is_returned_with_the_exact_reason_and_logged(general_client, lander):
    client, _ = general_client
    _scenario, _version, deployment_id = deployed(client)
    execution = start(client, deployment_id).json()
    run, fake = lander.sim.fly()
    lander.sim.state["vehicles"] = [air()]
    fake.results[176] = 2
    fake.say("Land denied: no position estimate")
    lander.sim.advance(0.2)
    lander.sim.auto_advance = 1.0
    refused = land(client)
    assert refused.status_code == 409 and refused.json()["detail"]["code"] == "land_refused"
    assert "MAV_RESULT_DENIED" in refused.json()["detail"]["message"] and "Land denied: no position estimate" in refused.json()["detail"]["message"]
    assert refused.json()["detail"]["values"]["relative_altitude_m"] == 30.0
    events = client.get(f"/api/mission-control/executions/{execution['id']}/events").json()["events"]
    assert any(item["type"] == "land_refused" for item in events)


def test_standalone_refusal_and_link_failures_are_reported(general_client, lander):
    client, _ = general_client
    lander.sim.state["vehicles"] = [air()]
    lander.answer = {"accepted": False, "refused": True, "reason": "PX4 answered Land with MAV_RESULT_DENIED; values used: AUTO LAND at relative altitude 30.0 m", "px4_messages": ["x"]}
    refused = land(client)
    assert refused.status_code == 409 and refused.json()["detail"]["code"] == "land_refused" and "MAV_RESULT_DENIED" in refused.json()["detail"]["message"]
    assert [item[0] for item in audit_events("aircraft_land_here")] == ["refused"]
    lander.answer = {"failure": "WSL mission executor could not complete: TimeoutExpired", "failure_code": "executor_unreachable"}
    assert land(client).status_code == 503
    lander.answer = {"failure": "PX4 instance 0 was not started by the TRINETRA fleet manager.", "failure_code": "instance_not_managed"}
    assert land(client).status_code == 409


@pytest.mark.parametrize("vehicles,code", [([vehicle()], "already_disarmed"), ([], "no_telemetry"), ([vehicle(status="stale", armed=True)], "no_telemetry"), ([vehicle(armed=None)], "armed_state_unknown")])
def test_land_is_disabled_when_disarmed_or_unknown_and_nothing_is_sent(general_client, lander, vehicles, code):
    client, _ = general_client
    lander.sim.state["vehicles"] = vehicles
    refused = land(client)
    assert refused.status_code == 409 and refused.json()["detail"]["code"] == code
    if code == "already_disarmed":
        assert refused.json()["detail"]["message"] == "Already disarmed"
    assert lander.calls == []


def test_both_roles_may_land_without_approval_and_csrf_and_login_are_required(general_client, operator_client, lander):
    from fastapi.testclient import TestClient
    from app.main import app

    client, _ = general_client
    operator, _user = operator_client
    lander.sim.state["vehicles"] = [air()]
    assert TestClient(app).post("/api/mission-control/aircraft/px4-1/land", json={"confirm": True}).status_code in (401, 403)
    csrf = operator.headers.pop("X-CSRF-Token")
    assert land(operator).status_code == 403
    operator.headers["X-CSRF-Token"] = csrf
    assert land(operator).status_code == 200                       # an Operator needs no approval, exactly like Abort
    assert land(client).status_code == 200
    assert land(client, "px4-1000").status_code == 422


def test_abort_is_refused_while_the_run_is_landing_with_a_clear_reason(general_client, lander):
    client, _ = general_client
    _scenario, _version, deployment_id = deployed(client)
    execution = start(client, deployment_id).json()
    lander.sim.fly()
    lander.sim.state["vehicles"] = [air()]
    assert land(client).status_code == 200
    refused = client.post(f"/api/mission-control/executions/{execution['id']}/abort")
    assert refused.status_code == 409 and "landing at its current position" in refused.json()["detail"]["message"]


# --- orphan cleanup (the executor tool, against a fake /proc) -----------------------------------------------------------------

def _proc_entry(root, pid, *argv):
    folder = root / str(pid)
    folder.mkdir(parents=True)
    (folder / "cmdline").write_bytes(b"\0".join(part.encode() for part in argv) + b"\0")


def test_cleanup_stops_only_this_checkouts_executor_and_landing_processes_and_reports_what_it_killed(tmp_path, monkeypatch):
    path = "/mnt/d/TRINETRA/backend/tools/wsl_mission_executor.py"
    _proc_entry(tmp_path, 301, "python3", path, "run", "--instance", "0", "--execution-id", "a" * 32)
    _proc_entry(tmp_path, 302, "python3", path, "land-run", "--instance", "1")
    _proc_entry(tmp_path, 303, "python3", path, "status", "--instance", "0")                 # a status read is not a long-running process
    _proc_entry(tmp_path, 304, "python3", "/home/someone/other_script.py", "run")             # another program that happens to say "run"
    _proc_entry(tmp_path, 305, "/home/apt8055/uav-sim/PX4-Autopilot/build/px4_sitl_default/bin/px4", "-i", "0")
    (tmp_path / "self").mkdir()
    sent = []
    monkeypatch.setattr(ex.os, "kill", lambda pid, sig: sent.append((pid, sig)))
    monkeypatch.setattr(ex, "runtime_dir", lambda instance: tmp_path / f"rt{instance}")
    for instance in range(5):
        (tmp_path / f"rt{instance}").mkdir()
    monkeypatch.setattr(ex, "__file__", path)
    result = ex.cleanup_processes(tmp_path)
    assert sorted(item["pid"] for item in result["killed"]) == [301, 302]
    assert {item["kind"] for item in result["killed"]} == {"executor", "land"} and {pid for pid, _ in sent} == {301, 302}
    assert all(sig == getattr(ex.signal, "SIGKILL", 9) for _, sig in sent)
