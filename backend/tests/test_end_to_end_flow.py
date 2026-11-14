"""Acceptance walk through the real HTTP stack: Scenario Lab -> save -> deploy -> Mission Control -> Attack Lab -> back.

Uses the temporary test database. Only the PX4 telemetry lookup and the WSL fleet manager are mocked (the `flight`
fixture); every route, validator, role check and database write is the real one.
"""
from __future__ import annotations

import json

from app.research.attacks import forbidden_fields
from test_attack_lab import _all_keys
from test_mission_planning import make_plan, offset
from test_mission_workflow_e2e import CONFIG, flight  # noqa: F401  (flight is a fixture)


def _draft(count: int = 1, **extra) -> dict:
    """What Scenario Lab holds after Sections A and B are filled in but before any route is generated."""
    base = make_plan("waypoint_route", plan_extra={"workflow_stage": "aircraft"}).model_dump(mode="json")
    aircraft = []
    for index in range(count):
        item = json.loads(json.dumps(base["aircraft"][0]))
        item.update(uav_id=f"px4-{index + 1}", role="primary" if index == 0 else "standby", route=None, confirmed=False)
        aircraft.append(item)
    ids = [item["uav_id"] for item in aircraft]
    return {**base, "aircraft": aircraft, "selected_uav_ids": ids, "activated_uav_ids": ids, "required_uav_count": count, **extra}


def _generate_routes_and_confirm(client, plan: dict) -> dict:
    """Section B: the backend generates each route; the client stores exactly what it returned, then confirms."""
    for aircraft in plan["aircraft"]:
        preview = client.post("/api/mission-plans/preview-route", json={"plan": plan, "uav_id": aircraft["uav_id"]})
        assert preview.status_code == 200, preview.text
        assert preview.json()["summary"]["waypoints"] == len(preview.json()["route"]["waypoints"]) >= 3
        aircraft.update(route=preview.json()["route"], confirmed=True)
    return {**plan, "workflow_stage": "review"}


def _body(plan: dict, title: str) -> dict:
    return {"title": title, "config": CONFIG, "intent": {"schema_version": 2, "mission_plan": plan, "uav_fault_assignments": []}}


def _mission_control(client) -> dict:
    response = client.get("/api/mission-control/deployments/latest")
    assert response.status_code == 200, response.text
    return response.json()["deployment"]


def _picker(client, scenario_id: str) -> list[dict]:
    missions = client.get("/api/attack-lab/missions").json()["missions"]
    return next(item for item in missions if item["scenario_id"] == scenario_id)["versions"]


def test_general_walks_scenario_lab_mission_control_attack_lab_and_back(general_client, flight):
    client, _ = general_client
    assert _mission_control(client) is None

    # Scenario Lab: create (schema 2) -> generate routes -> save.
    plan = _generate_routes_and_confirm(client, _draft())
    saved = client.post("/api/scenarios", json=_body(plan, "Flow mission"))
    assert saved.status_code == 201, saved.text
    scenario_id, first = saved.json()["id"], saved.json()["versions"][0]
    assert first["version_number"] == 1 and first["intent"]["schema_version"] == 2 and first["approval"] is None
    assert first["intent"]["mission_plan"]["aircraft"][0]["route"]["waypoints"][0]["action"] == "takeoff"

    # Deploy as GENERAL (no approval needed) -> Mission Control shows this mission with its routes.
    deployed = client.post(f"/api/scenarios/{scenario_id}/versions/{first['id']}/deploy")
    assert deployed.status_code == 200, deployed.text
    assert deployed.json()["scenario_version_number"] == 1 and deployed.json()["legacy"] is False
    shown = _mission_control(client)
    assert (shown["scenario_title"], shown["scenario_version_id"], shown["scenario_version_number"]) == ("Flow mission", first["id"], 1)
    assert shown["schema_version"] == 2 and shown["legacy"] is False and shown["superseded"] is False
    assert len(shown["intent"]["mission_plan"]["aircraft"][0]["route"]["waypoints"]) >= 3
    start = offset()
    assert shown["mission_start"]["uav_id"] == "px4-1"
    assert abs(shown["mission_start"]["latitude"] - start["latitude"]) < 1e-9 and abs(shown["mission_start"]["longitude"] - start["longitude"]) < 1e-9
    assert shown["execution_status"] == "DEPLOYED_TO_MISSION_CONTROL_NOT_UPLOADED_TO_PX4"

    # Attack Lab lists the saved version, marked latest and deployed.
    versions = _picker(client, scenario_id)
    assert [(v["id"], v["latest"], v["deployed"], v["in_mission_control"]) for v in versions] == [(first["id"], True, True, True)]

    # Create and validate an experiment from a preset.
    applied = client.post("/api/attack-lab/presets/slow_drift/apply", json={"scenario_id": scenario_id, "scenario_version_id": first["id"], "seed": 4})
    assert applied.status_code == 200, applied.text
    experiment = {"name": "Slow drift on the flow mission", "scenario_id": scenario_id, "scenario_version_id": first["id"],
                  "preset_id": "slow_drift", "seed": 4, "config": applied.json()["config"], "status": "draft"}
    assert client.post("/api/attack-lab/validate", json=experiment).json() == {"valid": True, "issues": []}
    created = client.post("/api/attack-lab/experiments", json=experiment)
    assert created.status_code == 201, created.text
    assert created.json()["superseded"] is False and created.json()["config"]["response_policy"] == "operator_decides"
    experiment_id = created.json()["id"]

    # Back to Scenario Lab: edit, save a new version.
    edited = {**plan, "notes": "second version"}
    updated = client.put(f"/api/scenarios/{scenario_id}", json=_body(edited, "Flow mission"))
    assert updated.status_code == 200, updated.text
    second = updated.json()["versions"][0]
    assert second["version_number"] == 2 and second["id"] != first["id"]

    # The experiment stays pinned and shows as superseded; the picker marks which version is which.
    assert client.get(f"/api/attack-lab/experiments/{experiment_id}").json()["superseded"] is True
    assert client.get("/api/attack-lab/experiments").json()["experiments"][0]["scenario_version_id"] == first["id"]
    versions = _picker(client, scenario_id)
    assert [(v["version_number"], v["latest"], v["deployed"], v["superseded"]) for v in versions] == [(2, True, False, False), (1, False, True, True)]
    # Mission Control still shows v1 and says a newer version exists.
    stale = _mission_control(client)
    assert stale["scenario_version_number"] == 1 and stale["superseded"] is True and stale["latest_version_number"] == 2

    # The old version can no longer be deployed; the client refetches and deploys the latest.
    refused = client.post(f"/api/scenarios/{scenario_id}/versions/{first['id']}/deploy")
    assert refused.status_code == 409 and refused.json()["detail"]["code"] == "version_superseded"
    assert refused.json()["detail"]["latest_version_id"] == second["id"]
    assert client.get(f"/api/scenarios/{scenario_id}").json()["versions"][0]["id"] == second["id"]
    assert client.post(f"/api/scenarios/{scenario_id}/versions/{second['id']}/deploy").status_code == 200
    current = _mission_control(client)
    assert (current["scenario_version_id"], current["scenario_version_number"], current["superseded"]) == (second["id"], 2, False)
    assert current["intent"]["mission_plan"]["notes"] == "second version"
    assert [(v["version_number"], v["deployed"], v["in_mission_control"]) for v in _picker(client, scenario_id)] == [(2, True, True), (1, True, False)]

    # Nothing about the experiment leaks into Scenario Lab or Mission Control payloads.
    for payload in (current, client.get(f"/api/scenarios/{scenario_id}").json(), client.get("/api/scenarios").json()):
        assert not forbidden_fields(_all_keys(payload)) and "slow_drift" not in json.dumps(payload)


def test_redeploying_an_earlier_mission_makes_it_the_one_mission_control_shows(general_client, flight):
    """Deploying the same version twice keeps one record, but it must become the current deployment again."""
    client, _ = general_client
    ids = []
    for title in ("First mission", "Second mission"):
        saved = client.post("/api/scenarios", json=_body(_generate_routes_and_confirm(client, _draft()), title)).json()
        ids.append((saved["id"], saved["versions"][0]["id"]))
        assert client.post(f"/api/scenarios/{saved['id']}/versions/{saved['versions'][0]['id']}/deploy").status_code == 200
    assert _mission_control(client)["scenario_title"] == "Second mission"
    first_record = client.post(f"/api/scenarios/{ids[0][0]}/versions/{ids[0][1]}/deploy")
    assert first_record.status_code == 200
    assert _mission_control(client)["scenario_title"] == "First mission"
    assert _mission_control(client)["id"] == first_record.json()["id"]


def test_a_legacy_deployment_is_labelled_legacy_and_a_newer_schema_2_deploy_replaces_it(general_client, flight):
    from app.persistence.database import create_scenario, save_mission_deployment

    client, user = general_client
    legacy = create_scenario(user.id, "Old mission", CONFIG, {
        "mission_objectives": ["x"], "mission_description": "legacy", "proposed_tasks": [], "uav_role_suggestions": [], "operating_area_notes": "",
        "environment_notes": "", "constraints": [], "geographic_mission": {"objective": "inspection", "uav_assignments": [], "survey_zones": [], "constraints": [], "ai_recommendations": []}})
    version = legacy["versions"][0]
    save_mission_deployment(legacy["id"], version["id"], user.id, "GENERAL", {
        "mission_id": legacy["id"], "scenario_title": "Old mission", "scenario_version_hash": version["config_hash"],
        "intent": version["intent"], "execution_status": "DEPLOYED_TO_MISSION_CONTROL_NOT_UPLOADED_TO_PX4"})
    shown = _mission_control(client)
    assert shown["legacy"] is True and shown["schema_version"] == 1 and shown["mission_start"] is None
    assert shown["scenario_version_number"] == 1
    saved = client.post("/api/scenarios", json=_body(_generate_routes_and_confirm(client, _draft()), "New mission")).json()
    assert client.post(f"/api/scenarios/{saved['id']}/versions/{saved['versions'][0]['id']}/deploy").status_code == 200
    shown = _mission_control(client)
    assert shown["scenario_title"] == "New mission" and shown["legacy"] is False and shown["mission_start"] is not None


def test_operator_submits_general_approves_operator_deploys(general_client, operator_client, flight):
    general, _ = general_client
    operator, _ = operator_client
    plan = _generate_routes_and_confirm(operator, _draft(2))
    saved = operator.post("/api/scenarios", json=_body(plan, "Operator flow"))
    assert saved.status_code == 201, saved.text
    scenario_id, version = saved.json()["id"], saved.json()["versions"][0]

    blocked = operator.post(f"/api/scenarios/{scenario_id}/versions/{version['id']}/deploy")
    assert blocked.status_code == 409 and blocked.json()["detail"]["code"] == "approval_required"
    assert general.post(f"/api/scenarios/{scenario_id}/versions/{version['id']}/submit").status_code in (403, 404)  # Generals do not submit

    submitted = operator.post(f"/api/scenarios/{scenario_id}/versions/{version['id']}/submit")
    assert submitted.status_code == 200 and submitted.json()["status"] == "PENDING"
    assert operator.get("/api/scenarios/my-approval-requests").json()["requests"][0]["status"] == "PENDING"
    pending = general.get("/api/general/scenario-approvals").json()["requests"]
    assert [item["scenario_version_id"] for item in pending] == [version["id"]]
    assert operator.post(f"/api/scenarios/{scenario_id}/versions/{version['id']}/deploy").json()["detail"]["code"] == "approval_required"

    approved = general.post(f"/api/general/scenario-approvals/{submitted.json()['id']}/decision", json={"decision": "approve", "reason": "Routes reviewed."})
    assert approved.status_code == 200, approved.text
    assert operator.get(f"/api/scenarios/{scenario_id}").json()["versions"][0]["approval"]["status"] == "APPROVED"

    deployed = operator.post(f"/api/scenarios/{scenario_id}/versions/{version['id']}/deploy")
    assert deployed.status_code == 200, deployed.text
    shown = _mission_control(operator)
    assert shown["scenario_title"] == "Operator flow" and shown["scenario_version_number"] == 1 and shown["legacy"] is False
    assert [item["uav_id"] for item in shown["intent"]["mission_plan"]["aircraft"]] == ["px4-1", "px4-2"]
    assert _mission_control(general)["scenario_version_id"] == version["id"]  # the General sees every deployment

    # Attack Lab: the operator sees the saved, deployed version (read-only); the General defines the experiment.
    assert [(v["deployed"], v["latest"]) for v in _picker(operator, scenario_id)] == [(True, True)]
    assert len(_picker(general, scenario_id)[0]["presets"]["presets"]) == 6  # two aircraft: nothing hidden
    applied = general.post("/api/attack-lab/presets/paired_drift/apply", json={"scenario_id": scenario_id, "scenario_version_id": version["id"]}).json()
    experiment = {"name": "Two-aircraft drift", "scenario_id": scenario_id, "scenario_version_id": version["id"], "preset_id": "paired_drift",
                  "seed": applied["seed"], "config": applied["config"], "status": "draft"}
    assert operator.post("/api/attack-lab/experiments", json=experiment).status_code == 403
    assert general.post("/api/attack-lab/experiments", json=experiment).status_code == 201

    # A fleet that is no longer verified blocks deployment with a structured reason (the safety rule is unchanged).
    edited = operator.put(f"/api/scenarios/{scenario_id}", json=_body({**plan, "notes": "v2"}, "Operator flow")).json()["versions"][0]
    resubmitted = operator.post(f"/api/scenarios/{scenario_id}/versions/{edited['id']}/submit").json()
    assert general.post(f"/api/general/scenario-approvals/{resubmitted['id']}/decision", json={"decision": "approve", "reason": "ok"}).status_code == 200


def test_deploy_is_blocked_with_a_structured_reason_when_the_fleet_is_not_verified(general_client, flight, monkeypatch):
    client, _ = general_client
    saved = client.post("/api/scenarios", json=_body(_generate_routes_and_confirm(client, _draft()), "Unverified fleet")).json()
    monkeypatch.setattr("app.fleet_routes._verified_fleet", lambda count: ([], []))
    blocked = client.post(f"/api/scenarios/{saved['id']}/versions/{saved['versions'][0]['id']}/deploy")
    assert blocked.status_code == 503
    detail = blocked.json()["detail"]
    assert detail["code"] == "fleet_not_verified" and detail["unverified"] == ["px4-1"] and "Activate and verify" in detail["message"]
    assert _mission_control(client) is None


def test_no_static_api_path_is_captured_by_an_earlier_parameterised_route(general_client):
    """Regression: `/api/scenarios/my-approval-requests` used to be answered by `/api/scenarios/{scenario_id}` (404)."""
    from starlette.routing import Match
    from app.main import app

    client, _ = general_client
    response = client.get("/api/scenarios/my-approval-requests")
    assert response.status_code == 200 and response.json() == {"requests": []}
    routes = [route for route in app.routes if hasattr(route, "methods")]
    for index, route in enumerate(routes):
        if "{" in route.path:
            continue
        for method in route.methods:
            scope = {"type": "http", "path": route.path, "method": method, "root_path": ""}
            shadowing = [earlier.path for earlier in routes[:index] if earlier.matches(scope)[0] == Match.FULL]
            assert not shadowing, f"{method} {route.path} is shadowed by {shadowing}"
