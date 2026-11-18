"""Part 4: operator responses to a GPS threat run the matching Mission Control command, audited with the live score/tier/evidence."""
from __future__ import annotations

import pytest

from app import research_routes
from app.persistence.database import list_research_decisions
from test_mission_execution_api import deployed, sim, start  # noqa: F401  (sim is a fixture)
from test_mission_workflow_e2e import flight  # noqa: F401


@pytest.fixture
def with_assessment(monkeypatch):
    """A live threat row for px4-1 (the monitor itself is tested elsewhere)."""
    row = {"aircraft_id": "px4-1", "state": "live", "armed": True, "assessment": {"tier": "High", "threat_score": 71.5, "evidence": [{"text": "GPS position jumped 150 m"}, {"text": "GPS disagrees with the IMU"}]}}
    monkeypatch.setattr(research_routes, "telemetry_store", object())
    monkeypatch.setattr(research_routes, "live_payload", lambda context: {"aircraft": [row]})
    return row


def respond(client, action, confirm=True, aircraft="px4-1"):
    return client.post("/api/research/responses", json={"aircraft_id": aircraft, "action": action, "confirm": confirm})


def options(client, aircraft="px4-1"):
    body = client.get(f"/api/research/responses?aircraft_id={aircraft}").json()
    return {item["action"]: item for item in body["options"]}


def test_options_without_a_run_say_why_each_is_disabled(general_client, sim, with_assessment):
    client, _ = general_client
    rows = options(client)
    assert rows["acknowledge"]["enabled"] is True and rows["restart"]["enabled"] is True and rows["restart"]["runs_in_ui"] is True
    for name in ("hold", "resume", "return_to_launch"):
        assert rows[name]["enabled"] is False and "No mission run is active on px4-1" in rows[name]["reason"]
    assert rows["handover_to_standby"]["enabled"] is False and rows["handover_to_standby"]["reason"] == "Needs more than one active aircraft."


def test_hold_resume_and_return_to_launch_run_the_real_commands_and_are_recorded(general_client, sim, with_assessment):
    client, user = general_client
    _s, _v, deployment_id = deployed(client)
    execution_id = start(client, deployment_id).json()["id"]
    sim.fly()
    assert respond(client, "hold", confirm=False).status_code == 422                       # a command needs confirmation
    hold = respond(client, "hold")
    assert hold.status_code == 200 and hold.json()["executed"] is True and hold.json()["tier"] == "High" and hold.json()["threat_score"] == 71.5
    assert hold.json()["evidence"][0] == "GPS position jumped 150 m" and hold.json()["decision"]["executed"] is True
    sim.advance(3)
    assert client.get(f"/api/mission-control/executions/{execution_id}").json()["state"] == "paused"
    rows = options(client)
    assert rows["resume"]["enabled"] is True and rows["hold"]["enabled"] is False
    assert respond(client, "resume").json()["executed"] is True
    sim.advance(3)
    assert client.get(f"/api/mission-control/executions/{execution_id}").json()["state"] == "executing"
    rtl = respond(client, "return_to_launch")
    assert rtl.status_code == 200 and "sent back to launch" in rtl.json()["message"]
    sim.advance(3)
    assert client.get(f"/api/mission-control/executions/{execution_id}").json()["state"] in ("returning", "aborted")
    stored = [item for item in list_research_decisions(20) if item["action"].startswith("response_")]
    assert [item["action"] for item in reversed(stored)] == ["response_hold", "response_resume", "response_return_to_launch"]
    assert all(item["tier"] == "High" and item["threat_score"] == 71.5 and item["executed"] for item in stored)


def test_a_refused_response_is_recorded_as_refused_and_nothing_is_commanded(general_client, sim, with_assessment):
    client, _ = general_client
    refused = respond(client, "hold")
    assert refused.status_code == 409 and refused.json()["detail"]["code"] == "no_active_run"
    last = list_research_decisions(1)[0]
    assert last["action"] == "response_hold" and last["source_name"].startswith("refused:") and last["executed"] is False


def test_acknowledge_sends_no_command_and_needs_an_assessment(general_client, sim, with_assessment, monkeypatch):
    client, _ = general_client
    done = respond(client, "acknowledge", confirm=False)
    assert done.status_code == 200 and done.json()["outcome"] == "acknowledged" and done.json()["executed"] is False and "No command was sent" in done.json()["message"]
    monkeypatch.setattr(research_routes, "live_payload", lambda context: {"aircraft": []})
    assert respond(client, "acknowledge").json()["detail"]["code"] == "no_assessment"


def test_land_here_uses_the_land_path(general_client, sim, with_assessment, monkeypatch):
    client, _ = general_client
    seen = {}
    from app import execution_routes

    def fake_land(aircraft_id, payload, request, context, csrf):
        seen.update(aircraft=aircraft_id, confirm=payload.confirm)
        return {"aircraft_id": aircraft_id, "already_landing": False, "mode": "air", "state": "landing", "execution": None}
    monkeypatch.setattr(execution_routes, "land_aircraft", fake_land)
    done = respond(client, "land_here")
    assert done.status_code == 200 and seen == {"aircraft": "px4-1", "confirm": True} and done.json()["executed"] is True


def test_the_response_needs_a_valid_aircraft_and_action_and_is_csrf_protected(general_client, sim, with_assessment):
    client, _ = general_client
    assert client.post("/api/research/responses", json={"aircraft_id": "px4-1", "action": "self_destruct", "confirm": True}).status_code == 422
    assert client.post("/api/research/responses", json={"aircraft_id": "../x", "action": "hold", "confirm": True}).status_code == 422
    assert client.get("/api/research/responses?aircraft_id=bad").status_code == 422


# --- multi-aircraft responses (Part 5) ------------------------------------------------------------------------------

from test_mission_execution_api import deployed_roles, vehicle  # noqa: E402
from app import response_routes  # noqa: E402


def with_two(sim, roles):
    sim.state["vehicles"].append(vehicle(id="px4-2", system_id=2))
    return roles


def test_handover_aborts_the_threatened_aircraft_and_starts_the_standby_on_its_own_route(general_client, sim, with_assessment, monkeypatch):
    client, _ = general_client
    sim.state["vehicles"].append(vehicle(id="px4-2", system_id=2))
    monkeypatch.setattr(response_routes, "check_handover", lambda decision, context: None)         # the battery rule has its own tests
    deployment_id = deployed_roles(client, ["primary", "standby"], "Handover plan")
    first = start(client, deployment_id).json()
    sim.fly(0)
    rows = options(client)
    assert rows["handover_to_standby"]["enabled"] is True and [item["aircraft_id"] for item in rows["handover_to_standby"]["candidates"] if item["eligible"]] == ["px4-2"]
    assert rows["switch_primary"]["enabled"] is False and "No flying decoy" in rows["switch_primary"]["reason"]
    assert client.post("/api/research/responses", json={"aircraft_id": "px4-1", "action": "handover_to_standby", "confirm": True}).status_code == 422     # needs a candidate
    done = client.post("/api/research/responses", json={"aircraft_id": "px4-1", "action": "handover_to_standby", "candidate": "px4-2", "confirm": True})
    assert done.status_code == 200, done.text
    assert "started its own planned route as the new primary" in done.json()["message"] and done.json()["decision"]["handover_candidate"] == "px4-2"
    sim.advance(3, instance=0)
    assert client.get(f"/api/mission-control/executions/{first['id']}").json()["state"] in ("returning", "aborted")
    assert sorted(sim.runs) == [0, 1]                                                                 # the standby really got a run
    from app import research_routes
    assert research_routes.ROLE_OVERRIDES[deployment_id] == {"px4-2": "primary", "px4-1": "standby"}


def test_handover_refuses_a_standby_that_is_not_ready_before_commanding_anything(general_client, sim, with_assessment, monkeypatch):
    client, _ = general_client
    sim.state["vehicles"].append(vehicle(id="px4-2", system_id=2, battery={"remaining_percent": 20.0}))
    monkeypatch.setattr(response_routes, "check_handover", lambda decision, context: None)
    deployment_id = deployed_roles(client, ["primary", "standby"], "Weak standby")
    start(client, deployment_id)
    sim.fly(0)
    rows = options(client)
    assert rows["handover_to_standby"]["enabled"] is False and "battery 20% is below" in rows["handover_to_standby"]["reason"]
    refused = client.post("/api/research/responses", json={"aircraft_id": "px4-1", "action": "handover_to_standby", "candidate": "px4-2", "confirm": True})
    assert refused.status_code == 409 and refused.json()["detail"]["code"] == "candidate_not_eligible"
    assert sorted(sim.runs) == [0]                                                                    # nothing was started and px4-1 was not aborted


def test_switch_primary_swaps_roles_without_any_command(general_client, sim, with_assessment):
    client, _ = general_client
    sim.state["vehicles"].append(vehicle(id="px4-2", system_id=2))
    deployment_id = deployed_roles(client, ["primary", "decoy"], "Switch plan")
    start(client, deployment_id)
    sim.state["vehicles"][1]["armed"] = True                                                          # the decoy is now airborne
    sim.fly(0)
    rows = options(client)
    assert rows["switch_primary"]["enabled"] is True and rows["handover_to_standby"]["enabled"] is False
    before = len(sim.calls)
    done = client.post("/api/research/responses", json={"aircraft_id": "px4-1", "action": "switch_primary", "candidate": "px4-2", "confirm": True})
    assert done.status_code == 200 and "px4-2 is now the primary" in done.json()["message"]
    assert not [call for call in sim.calls[before:] if call[0] not in ("status",)]                    # no command reached any aircraft
    from app import research_routes
    assert research_routes.ROLE_OVERRIDES[deployment_id] == {"px4-2": "primary", "px4-1": "decoy"}


def test_the_multi_aircraft_options_say_why_when_there_is_only_one_aircraft(general_client, sim, with_assessment):
    client, _ = general_client
    sim.state["vehicles"] = [vehicle()]
    rows = options(client)
    assert rows["handover_to_standby"]["reason"] == "Needs more than one active aircraft." and rows["switch_primary"]["reason"] == "Needs more than one active aircraft."
