"""Disarm: a normal MAVLink disarm for an aircraft that is armed, on the ground and not being flown by an execution.

The WSL executor is replaced by a fake that records the call; the executor's own one-shot `disarm_once` is exercised against the
fake PX4 peer used by the other executor tests. Relocation while armed on the ground is tested through the real route.
"""
from __future__ import annotations

import pytest

import test_mission_executor as peer
from app.mission_execution.ground import IN_THE_AIR, ground_state
from app.telemetry.mission_executor import build_executor_command
from test_mission_execution_api import FakeExecutor, deployed, sim, start, vehicle  # noqa: F401  (sim is a fixture)
from test_mission_workflow_e2e import flight  # noqa: F401  (fixture)
from test_simulation_home import _manager_ok, _receivers, _saved
from test_simulation_home import _vehicle as home_vehicle

ex = peer.ex


def airborne(**patch):
    return vehicle(armed=True, position={"latitude": 12.97, "longitude": 77.59, "relative_altitude_m": 30.0, "valid": True}, velocity={"ground_speed_m_s": 7.9}, **patch)


def standing(**patch):
    return vehicle(armed=True, **patch)       # relative altitude 0.02 m, speed 0


class DisarmExecutor:
    """Replaces invoke_mission_executor for the disarm action; every other action goes to the real fake."""

    def __init__(self, inner, answer):
        self.inner, self.answer, self.disarms = inner, answer, []

    def __call__(self, action, **kwargs):
        if action == "disarm":
            self.disarms.append(kwargs.get("instance"))
            return dict(self.answer)
        return self.inner(action, **kwargs)


@pytest.fixture
def disarmer(sim, monkeypatch):
    from app import execution_routes

    holder = DisarmExecutor(sim, {"disarm": True, "was_armed": True, "accepted": True, "ack": "ACCEPTED", "armed": False, "px4_messages": []})
    monkeypatch.setattr(execution_routes, "invoke_mission_executor", holder)
    holder.sim = sim
    return holder


def disarm(client, aircraft="px4-1"):
    return client.post(f"/api/mission-control/aircraft/{aircraft}/disarm")


def test_executor_adapter_allows_disarm_without_an_execution_id():
    command = build_executor_command("disarm", instance=0)
    assert command[-6:-3] == ["disarm", "--instance", "0"] or "disarm" in command
    with pytest.raises(ValueError):
        build_executor_command("disarm", instance=0, execution_id="a" * 32)
    with pytest.raises(ValueError):
        build_executor_command("force_disarm", instance=0)


def test_ground_state_uses_altitude_and_speed_and_never_guesses():
    assert ground_state(None) == {"known": False, "connected": False, "armed": None, "on_ground": None}
    assert ground_state(vehicle(status="stale"))["connected"] is False
    assert ground_state(standing())["on_ground"] is True
    assert ground_state(airborne())["on_ground"] is False
    moving = standing(velocity={"ground_speed_m_s": 3.0})
    assert ground_state(moving)["on_ground"] is False                       # low altitude but moving: not on the ground
    assert ground_state(standing(position={"latitude": 1, "longitude": 1, "valid": True}))["known"] is False        # no altitude: unknown


def test_disarm_is_allowed_on_the_ground_and_sends_a_normal_disarm(general_client, disarmer):
    client, _ = general_client
    disarmer.sim.state["vehicles"] = [standing()]
    response = disarm(client)
    assert response.status_code == 200, response.text
    assert response.json() == {"aircraft_id": "px4-1", "disarmed": True, "armed": False, "forced": False, "px4_messages": [], "simulation_only": True}
    assert disarmer.disarms == [0]
    audit = [item for item in client.get("/api/audit/events").json().get("events", [])] if client.get("/api/audit/events").status_code == 200 else []
    assert not audit or any(item.get("action") == "aircraft_disarm" for item in audit)


def test_disarm_is_refused_in_the_air_with_the_abort_hint_and_nothing_is_sent(general_client, disarmer):
    client, _ = general_client
    disarmer.sim.state["vehicles"] = [airborne()]
    refused = disarm(client)
    assert refused.status_code == 409 and refused.json()["detail"]["code"] == "in_the_air"
    assert refused.json()["detail"]["message"] == IN_THE_AIR == "In the air: abort (return to launch) first; it disarms itself after landing."
    assert disarmer.disarms == []


@pytest.mark.parametrize("vehicles,code", [
    ([vehicle()], "already_disarmed"),
    ([], "no_telemetry"),
    ([vehicle(status="stale", armed=True)], "no_telemetry"),
    ([vehicle(armed=None)], "armed_state_unknown"),
    ([standing(position={"latitude": 1.0, "longitude": 1.0, "valid": True})], "altitude_unknown"),
])
def test_disarm_refuses_when_the_state_is_not_a_confirmed_armed_on_the_ground(general_client, disarmer, vehicles, code):
    client, _ = general_client
    disarmer.sim.state["vehicles"] = vehicles
    refused = disarm(client)
    assert refused.status_code == 409 and refused.json()["detail"]["code"] == code
    assert disarmer.disarms == []


def test_disarm_is_refused_while_an_execution_is_flying_the_aircraft(general_client, disarmer):
    client, _ = general_client
    _scenario, _version, deployment_id = deployed(client)
    started = start(client, deployment_id)
    assert started.status_code == 201, started.text
    disarmer.sim.fly()
    disarmer.sim.state["vehicles"] = [standing()]              # even a telemetry glitch showing "on the ground" cannot disarm a flying run
    refused = disarm(client)
    assert refused.status_code == 409 and refused.json()["detail"]["code"] == "in_the_air"
    assert disarmer.disarms == []


def test_after_a_final_state_an_armed_aircraft_on_the_ground_can_be_disarmed_and_the_run_log_records_it(general_client, disarmer):
    client, _ = general_client
    _scenario, _version, deployment_id = deployed(client)
    execution = start(client, deployment_id).json()
    _run, fake = disarmer.sim.fly()
    assert client.post(f"/api/mission-control/executions/{execution['id']}/abort").status_code == 200
    disarmer.sim.advance(3)
    fake.armed = False                                        # (the fake PX4 lands and disarms; the real one is checked live)
    disarmer.sim.advance(3)
    assert client.get(f"/api/mission-control/executions/{execution['id']}").json()["active"] is False
    disarmer.sim.state["vehicles"] = [standing()]              # PX4 did not disarm by itself
    response = disarm(client)
    assert response.status_code == 200 and response.json()["disarmed"] is True
    events = client.get(f"/api/mission-control/executions/{execution['id']}/events").json()["events"]
    mine = [item for item in events if item["source"] == "user" and item["type"] == "disarmed"]      # (the executor also logs its own "disarmed")
    assert len(mine) == 1 and mine[0]["actor_name"]
    assert "disarmed px4-1 on the ground (normal disarm, no force)" in mine[0]["message"]


def test_px4_refusal_is_returned_with_its_reason_and_logged(general_client, disarmer):
    client, _ = general_client
    disarmer.sim.state["vehicles"] = [standing()]
    disarmer.answer = {"disarm": True, "was_armed": True, "accepted": False, "ack": "DENIED", "armed": True, "px4_messages": ["Disarming denied: not landed"]}
    refused = disarm(client)
    assert refused.status_code == 409 and refused.json()["detail"]["code"] == "disarm_refused"
    assert refused.json()["detail"]["message"] == "PX4 refused to disarm (DENIED). PX4 said: Disarming denied: not landed"
    assert refused.json()["detail"]["px4_messages"] == ["Disarming denied: not landed"]


def test_executor_failures_are_reported_not_hidden(general_client, disarmer):
    client, _ = general_client
    disarmer.sim.state["vehicles"] = [standing()]
    disarmer.answer = {"failure": "WSL mission executor could not complete: TimeoutExpired", "failure_code": "executor_unreachable"}
    assert disarm(client).status_code == 503
    disarmer.answer = {"failure": "PX4 instance 0 was not started by the TRINETRA fleet manager.", "failure_code": "instance_not_managed"}
    assert disarm(client).status_code == 409
    assert disarm(client, "px4-9").status_code == 422 and disarm(client, "px4-1;rm").status_code in (404, 422)


def test_both_roles_may_disarm_without_approval_and_csrf_and_login_are_required(general_client, operator_client, disarmer):
    from fastapi.testclient import TestClient
    from app.main import app

    client, _ = general_client
    operator, _user = operator_client
    disarmer.sim.state["vehicles"] = [standing()]
    assert TestClient(app).post("/api/mission-control/aircraft/px4-1/disarm").status_code in (401, 403)
    csrf = operator.headers.pop("X-CSRF-Token")
    assert disarm(operator).status_code == 403
    operator.headers["X-CSRF-Token"] = csrf
    assert disarm(operator).status_code == 200                 # an Operator needs no approval, exactly like Abort
    assert disarm(client).status_code == 200
    assert disarmer.disarms == [0, 0]


# --- relocation while armed -------------------------------------------------------------------------------------------

def _relocation_setup(client, monkeypatch):
    scenario_id, version_id = _saved(client)
    _receivers(monkeypatch)
    row = {"aircraft_id": "px4-1", "instance_number": 0, "system_id": 1, "gazebo_model": "x500_0", "px4_status": "connected", "gazebo_status": "connected", "verified": True}
    monkeypatch.setattr("app.fleet_routes._verified_fleet", lambda count: ([row], [row]))
    sent = []
    monkeypatch.setattr("app.fleet_routes.invoke_fleet_manager", lambda action, **k: sent.append(action) or _manager_ok())
    return {"scenario_id": scenario_id, "scenario_version_id": version_id, "confirm": True}, sent


def test_relocation_is_not_blocked_by_an_aircraft_that_is_armed_on_the_ground(general_client, flight, monkeypatch):
    from test_mission_planning import offset

    client, _ = general_client
    body, sent = _relocation_setup(client, monkeypatch)
    near = offset(2, 1)
    on_ground = {**home_vehicle(**near, armed=True), "position": {**near, "valid": True, "relative_altitude_m": 0.05}, "velocity": {"ground_speed_m_s": 0.0}}
    monkeypatch.setattr("app.fleet_routes._fleet_snapshot", lambda: ([on_ground], []))
    response = client.post("/api/fleet/relocate", json=body)
    assert response.status_code == 200, response.text            # PX4 is restarted, so the armed flag is lost with it
    assert sent == ["relocate"] and response.json()["relocated"] is True


def test_relocation_still_refuses_an_armed_aircraft_that_is_in_the_air_or_unconfirmed(general_client, flight, monkeypatch):
    from test_mission_planning import offset

    client, _ = general_client
    body, sent = _relocation_setup(client, monkeypatch)
    near = offset(2, 1)
    flying = {**home_vehicle(**near, armed=True), "position": {**near, "valid": True, "relative_altitude_m": 30.0}, "velocity": {"ground_speed_m_s": 8.0}}
    monkeypatch.setattr("app.fleet_routes._fleet_snapshot", lambda: ([flying], []))
    refused = client.post("/api/fleet/relocate", json=body)
    assert refused.status_code == 409 and refused.json()["detail"]["code"] == "aircraft_armed"
    assert "Abort (return to launch) first" in refused.json()["detail"]["message"]
    unknown = home_vehicle(**near, armed=True)                    # no altitude reported: not confirmed on the ground
    monkeypatch.setattr("app.fleet_routes._fleet_snapshot", lambda: ([unknown], []))
    assert client.post("/api/fleet/relocate", json=body).status_code == 409
    assert sent == []


# --- the one-shot executor against the fake PX4 peer ------------------------------------------------------------------

class DisarmPeer:
    """A fake PX4 onboard link for disarm_once: replies to the disarm command the way PX4 does."""

    def __init__(self, armed=True, deny=None):
        self.armed, self.deny, self.sent, self.queue, self.texts = armed, deny, [], [], []

    def send_heartbeat(self):
        self.queue.append(("HEARTBEAT", {"autopilot": ex.MAV_AUTOPILOT_PX4, "base_mode": ex.MAV_MODE_FLAG_SAFETY_ARMED if self.armed else 0}))

    def send_command(self, command, params):
        assert command == ex.MAV_CMD_COMPONENT_ARM_DISARM
        self.sent.append((command, params))
        if self.deny:
            self.queue.append(("STATUSTEXT", {"text": self.deny}))
            self.queue.append(("COMMAND_ACK", {"command": command, "result": 2}))
        else:
            self.armed = False
            self.queue.append(("COMMAND_ACK", {"command": command, "result": 0}))
            self.send_heartbeat()

    def recv(self):
        if not self.queue:
            return None
        kind, fields = self.queue.pop(0)
        return type("Message", (), {"get_type": lambda self: kind, **fields})()


def run_disarm(monkeypatch, fake):
    monkeypatch.setattr(ex, "PymavlinkLink", lambda instance, target: fake)
    return ex.disarm_once(0, seconds=2.0)


def test_executor_disarm_sends_param1_zero_and_param2_zero_only_when_armed(monkeypatch):
    fake = DisarmPeer(armed=True)
    result = run_disarm(monkeypatch, fake)
    assert result["accepted"] is True and result["armed"] is False and result["ack"] == "ACCEPTED"
    assert fake.sent == [(ex.MAV_CMD_COMPONENT_ARM_DISARM, (0.0,) * 7)]            # param1 0 = disarm, param2 0 = not forced (never 21196)
    already = DisarmPeer(armed=False)
    assert run_disarm(monkeypatch, already)["was_armed"] is False and already.sent == []        # nothing is sent to a disarmed aircraft


def test_executor_disarm_reports_px4_refusal_text(monkeypatch):
    fake = DisarmPeer(armed=True, deny="Disarming denied: not landed")
    result = run_disarm(monkeypatch, fake)
    assert result["accepted"] is False and result["ack"] == "DENIED" and result["armed"] is True
    assert result["px4_messages"] == ["Disarming denied: not landed"]
    silent = type("Silent", (), {"send_heartbeat": lambda self: None, "recv": lambda self: None, "send_command": lambda *a: None})()
    assert run_disarm(monkeypatch, silent)["failure_code"] == "no_heartbeat"
