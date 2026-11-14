"""End-to-end Scenario Lab -> Mission Control backend workflow through the real HTTP stack.

Uses the temporary test database. Only the PX4 telemetry lookup and the WSL fleet manager are mocked.
"""
from __future__ import annotations

import pytest

from app.mission_planning.generators import generate_route
from app.mission_planning.schemas import MissionPlan
from test_mission_planning import LIMITS, make_plan, offset

CONFIG = {"uav_count": 4, "task_count": 10, "seed": 21, "duration": 180, "spoofing_type": "none", "target_uav": "UAV-02",
          "spoof_start": 15, "spoof_duration": 35, "spoof_magnitude": 3, "response_policy": "adaptive", "detector_threshold": None}


def complete_plan(count: int = 1, stage: str = "review", **extra) -> MissionPlan:
    base = make_plan("waypoint_route", plan_extra={"workflow_stage": stage})
    aircraft = []
    for index in range(count):
        item = base.aircraft[0].model_copy(update={"uav_id": f"px4-{index + 1}", "role": "primary" if index == 0 else "standby"})
        item = item.model_copy(update={"route": generate_route(item, base, LIMITS), "confirmed": True})
        aircraft.append(item)
    ids = [item.uav_id for item in aircraft]
    return base.model_copy(update={"aircraft": aircraft, "selected_uav_ids": ids, "activated_uav_ids": ids, "required_uav_count": count, **extra})


def body(plan: MissionPlan, title: str = "E2E plan", faults: list | None = None) -> dict:
    return {"title": title, "config": CONFIG, "intent": {
        "schema_version": 2, "mission_plan": plan.model_dump(mode="json"), "uav_fault_assignments": faults or [],
    }}


@pytest.fixture
def flight(monkeypatch):
    """Mocked fleet/PX4: two connected, verified aircraft."""
    ids = ["px4-1", "px4-2"]
    monkeypatch.setattr("app.scenario_routes._planning_aircraft_ids", lambda: set(ids))
    monkeypatch.setattr("app.scenario_routes.invoke_fleet_manager", lambda *a, **k: {
        "instances": [{"system_id": 1}, {"system_id": 2}], "manager": "px4_wsl_fleet_v1"})
    monkeypatch.setattr("app.fleet_routes._verified_fleet", lambda count: (
        [{"aircraft_id": ids[i], "system_id": i + 1, "gazebo_model": f"x500_{i}", "verified": True} for i in range(count)], []))


def deploy(client, scenario_id, version_id):
    return client.post(f"/api/scenarios/{scenario_id}/versions/{version_id}/deploy")


def test_general_creates_saves_and_deploys_one_and_two_aircraft(general_client, flight):
    client, _ = general_client
    for count in (1, 2):
        saved = client.post("/api/scenarios", json=body(complete_plan(count), f"General {count}"))
        assert saved.status_code == 201, saved.text
        scenario = saved.json()
        version = scenario["versions"][0]
        assert version["intent"]["schema_version"] == 2
        response = deploy(client, scenario["id"], version["id"])
        assert response.status_code == 200, response.text
        payload = response.json()["payload"] if "payload" in response.json() else response.json()
        plan = payload["intent"]["mission_plan"]
        assert [item["uav_id"] for item in plan["aircraft"]] == [f"px4-{i + 1}" for i in range(count)]
        assert all(item["route"]["waypoints"] for item in plan["aircraft"])
        assert payload["execution_status"] == "DEPLOYED_TO_MISSION_CONTROL_NOT_UPLOADED_TO_PX4"
        assert deploy(client, scenario["id"], version["id"]).json()["id"] == response.json()["id"]  # idempotent
        latest = client.get("/api/mission-control/deployments/latest")
        assert latest.status_code == 200
        deployed = latest.json()["deployment"]
        assert deployed["scenario_version_id"] == version["id"]
        assert deployed["intent"]["mission_plan"]["aircraft"][0]["route"]["events"] == []


def test_stale_version_is_rejected_and_the_latest_version_deploys(general_client, flight):
    client, _ = general_client
    created = client.post("/api/scenarios", json=body(complete_plan())).json()
    first = created["versions"][0]["id"]
    edited = complete_plan().model_copy(update={"description": "edited after the first save"})
    updated = client.put(f"/api/scenarios/{created['id']}", json=body(edited)).json()
    second = updated["versions"][0]["id"]
    assert second != first and updated["versions"][0]["version_number"] == 2
    stale = deploy(client, created["id"], first)
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "version_superseded"
    assert stale.json()["detail"]["message"] == "Only the latest immutable scenario version can be deployed."
    assert stale.json()["detail"]["latest_version_id"] == second and stale.json()["detail"]["latest_version_number"] == 2
    assert deploy(client, created["id"], second).status_code == 200


def test_incomplete_far_and_tampered_plans_are_rejected_with_structured_issues(general_client, flight):
    client, _ = general_client
    draft = complete_plan(stage="aircraft")
    saved = client.post("/api/scenarios", json=body(draft)).json()
    incomplete = body(draft.model_copy(update={"workflow_stage": "review", "constraints": {"max_distance_from_start_m": None, "max_altitude_m": 100}}))
    rejected = client.put(f"/api/scenarios/{saved['id']}", json=incomplete)
    assert rejected.status_code == 422 and rejected.json()["detail"]["code"] == "mission_plan_invalid"
    assert {item["code"] for item in rejected.json()["detail"]["issues"]} >= {"missing_field"}
    far = complete_plan().model_copy(update={"aircraft": [complete_plan().aircraft[0].model_copy(update={"start_location": {"latitude": 15.7, "longitude": 77.5946}})]})
    far_response = client.post("/api/scenarios", json=body(far))
    assert far_response.status_code == 422
    assert "start_too_far_from_area" in {item["code"] for item in far_response.json()["detail"]["issues"]}
    plan = complete_plan()
    tampered_route = plan.aircraft[0].route.model_copy(update={"length_m": 5.0})
    tampered = plan.model_copy(update={"aircraft": [plan.aircraft[0].model_copy(update={"route": tampered_route})]})
    response = client.post("/api/scenarios", json=body(tampered))
    assert response.status_code == 422
    assert "route_mismatch" in {item["code"] for item in response.json()["detail"]["issues"]}


def test_operator_deploys_only_the_exact_approved_version(general_client, operator_client, flight):
    general, _ = general_client
    operator, _ = operator_client
    created = operator.post("/api/scenarios", json=body(complete_plan(), "Operator plan")).json()
    first = created["versions"][0]["id"]
    blocked = deploy(operator, created["id"], first)
    assert blocked.status_code == 409 and blocked.json()["detail"]["code"] == "approval_required"
    assert "requires approval" in blocked.json()["detail"]["message"]
    submitted = operator.post(f"/api/scenarios/{created['id']}/versions/{first}/submit")
    assert submitted.status_code == 200, submitted.text
    request_id = submitted.json()["id"]
    assert operator.post(f"/api/general/scenario-approvals/{request_id}/decision", json={"decision": "approve", "reason": "ok"}).status_code == 403
    assert general.get("/api/general/scenario-approvals").status_code == 200
    approved = general.post(f"/api/general/scenario-approvals/{request_id}/decision", json={"decision": "approve", "reason": "Reviewed the exact version."})
    assert approved.status_code == 200, approved.text
    # A later change creates a new immutable version with no approval: the old approval does not carry over.
    edited = operator.put(f"/api/scenarios/{created['id']}", json=body(complete_plan().model_copy(update={"notes": "changed"}))).json()
    second = edited["versions"][0]["id"]
    assert edited["versions"][0]["approval"] is None
    assert deploy(operator, created["id"], first).status_code == 409  # approved but no longer the latest version
    still_blocked = deploy(operator, created["id"], second)
    assert still_blocked.status_code == 409 and still_blocked.json()["detail"]["code"] == "approval_required"
    resubmitted = operator.post(f"/api/scenarios/{created['id']}/versions/{second}/submit").json()
    assert general.post(f"/api/general/scenario-approvals/{resubmitted['id']}/decision", json={"decision": "approve", "reason": "Second version reviewed."}).status_code == 200
    assert deploy(operator, created["id"], second).status_code == 200


def test_unauthenticated_and_incomplete_deploys_are_refused(general_client, flight):
    from fastapi.testclient import TestClient
    from app.main import app

    client, _ = general_client
    created = client.post("/api/scenarios", json=body(complete_plan(stage="aircraft"))).json()
    version = created["versions"][0]["id"]
    assert TestClient(app).post(f"/api/scenarios/{created['id']}/versions/{version}/deploy").status_code == 401
    incomplete = deploy(client, created["id"], version)
    assert incomplete.status_code == 422 and incomplete.json()["detail"]["code"] == "mission_plan_invalid"
    assert any(item["message"].startswith("The A–C workflow") for item in incomplete.json()["detail"]["issues"])


def test_stored_fault_profiles_may_target_the_plans_real_aircraft(general_client, flight):
    client, _ = general_client
    faults = [{"uav_id": "px4-1", "fault_type": "gradual", "spoof_start": 5, "spoof_duration": 10, "spoof_magnitude": 2.0}]
    saved = client.post("/api/scenarios", json=body(complete_plan(), faults=faults))
    assert saved.status_code == 201, saved.text
    assert saved.json()["versions"][0]["intent"]["uav_fault_assignments"][0]["uav_id"] == "px4-1"
    unknown = client.post("/api/scenarios", json=body(complete_plan(), faults=[{**faults[0], "uav_id": "px4-4"}]))
    assert unknown.status_code == 422
    legacy_ids = client.post("/api/scenarios", json=body(complete_plan(), faults=[{**faults[0], "uav_id": "UAV-02"}]))
    assert legacy_ids.status_code == 201, legacy_ids.text
