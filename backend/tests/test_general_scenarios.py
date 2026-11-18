"""GENERAL's saved-scenarios manager (list, export, cascade delete) and the quick source stored with a version."""
from __future__ import annotations

import json

import pytest
from sqlalchemy import select

from app.mission_planning.quick import plan_fingerprint
from app.mission_planning.schemas import MissionPlan
from app.persistence.database import AuditLogRecord, ExperimentResultRecord, AttackExperimentRecord, SessionLocal, utcnow_naive
import test_mission_execution_api as exec_api
from test_mission_execution_api import deployed, sim, start  # noqa: F401  (sim is a fixture)
from test_mission_workflow_e2e import body as plan_body, complete_plan, flight  # noqa: F401


def create(client, title="Manual scenario", count=1, quick=False):
    payload = plan_body(complete_plan(count), title)
    if quick:
        normalized = MissionPlan.model_validate(payload["intent"]["mission_plan"]).model_dump(mode="json")
        payload["intent"]["quick_source"] = {"mission_type": "waypoint_route", "where": {"latitude": 12.97, "longitude": 77.59}, "size_m": 300, "drone_count": count,
                                             "roles": [], "speed_mps": 5, "altitude_m": 30, "name": title, "fingerprint": plan_fingerprint(normalized)}
    saved = client.post("/api/scenarios", json=payload)
    assert saved.status_code == 201, saved.text
    return saved.json()


# --- quick source ---------------------------------------------------------------------------------------------------------------

def test_a_quick_source_is_stored_with_the_version_and_a_manual_version_has_none(general_client, flight):
    client, _ = general_client
    quick = create(client, "Quick one", quick=True)
    assert quick["versions"][0]["intent"]["quick_source"]["mission_type"] == "waypoint_route"
    manual = create(client, "Manual one")
    assert manual["versions"][0]["intent"].get("quick_source") is None
    rows = {row["title"]: row for row in client.get("/api/general/scenarios").json()["scenarios"]}
    assert rows["Quick one"]["source"] == "quick" and rows["Manual one"]["source"] == "manual"


def test_the_quick_source_is_kept_while_the_quick_fields_are_unchanged_and_dropped_when_fine_tuned(general_client, flight):
    client, _ = general_client
    quick = create(client, "Tuned", quick=True)
    source = quick["versions"][0]["intent"]["quick_source"]
    plan = quick["versions"][0]["intent"]["mission_plan"]
    # a change to a field Quick Mission does not control (the description) keeps the source
    same = {"title": "Tuned", "config": quick["versions"][0]["config"], "intent": {"schema_version": 2, "mission_plan": {**plan, "description": "notes only", "notes": "x"}, "uav_fault_assignments": [], "quick_source": source}}
    kept = client.put(f"/api/scenarios/{quick['id']}", json=same).json()
    assert kept["versions"][0]["version_number"] == 2 and kept["versions"][0]["intent"]["quick_source"]["fingerprint"] == source["fingerprint"]
    # a change to a quick-controlled field (the altitude) drops it
    aircraft = [{**plan["aircraft"][0], "altitude_m": 35, "route": None, "confirmed": False}]
    changed = {"title": "Tuned", "config": quick["versions"][0]["config"], "intent": {"schema_version": 2, "mission_plan": {**plan, "aircraft": aircraft, "workflow_stage": "aircraft"}, "uav_fault_assignments": [], "quick_source": source}}
    response = client.put(f"/api/scenarios/{quick['id']}", json=changed)
    assert response.status_code == 200, response.text
    assert response.json()["versions"][0]["version_number"] == 3 and response.json()["versions"][0]["intent"].get("quick_source") is None
    # earlier versions are untouched (immutable): version 1 still has its source and hash
    detail = client.get(f"/api/scenarios/{quick['id']}").json()
    by_number = {v["version_number"]: v for v in detail["versions"]}
    assert by_number[1]["intent"]["quick_source"] and by_number[1]["config_hash"] == quick["versions"][0]["config_hash"]


# --- the list, permissions, export ---------------------------------------------------------------------------------------------

def test_list_filters_across_users_and_permissions(general_client, operator_client, flight):
    general, _ = general_client
    operator, operator_user = operator_client
    create(general, "General draft")
    mine = create(operator, "Operator quick", quick=True)
    rows = general.get("/api/general/scenarios").json()["scenarios"]
    assert {row["title"] for row in rows} == {"General draft", "Operator quick"}
    row = next(item for item in rows if item["title"] == "Operator quick")
    assert row["owner_role"] == "OPERATOR" and row["owner_username"] == operator_user.username and row["status"] == "draft" and row["latest_version_number"] == 1
    assert row["deployments"] == 0 and row["executions"] == 0 and row["versions"][0]["number"] == 1
    assert [r["title"] for r in general.get(f"/api/general/scenarios?owner={operator_user.username}").json()["scenarios"]] == ["Operator quick"]
    assert [r["title"] for r in general.get("/api/general/scenarios?q=general").json()["scenarios"]] == ["General draft"]
    assert general.get("/api/general/scenarios?status=approved").json()["scenarios"] == []
    # an Operator cannot use the manager at all, and still has only their own scenarios elsewhere
    assert operator.get("/api/general/scenarios").status_code == 403
    assert operator.get(f"/api/general/scenarios/{mine['id']}/export").status_code == 403
    assert operator.post(f"/api/general/scenarios/{mine['id']}/delete", json={"confirm_name": "Operator quick"}).status_code == 403
    assert [s["title"] for s in operator.get("/api/scenarios").json()["scenarios"]] == ["Operator quick"]


def test_export_holds_versions_hashes_deployments_executions_and_events(general_client, sim):
    client, _ = general_client
    scenario_id, version_id, deployment_id = deployed(client, title="Exported")
    execution_id = start(client, deployment_id).json()["id"]
    sim.fly()
    record = client.get(f"/api/general/scenarios/{scenario_id}/export").json()
    assert record["exported_format"] == "trinetra-scenario-record-1" and record["scenario"]["title"] == "Exported"
    assert record["versions"][0]["config_hash"] and record["versions"][0]["intent"]["mission_plan"]["aircraft"][0]["route"]["waypoints"]
    assert record["deployments"][0]["id"] == deployment_id
    assert record["executions"][0]["id"] == execution_id and record["executions"][0]["events"]
    assert client.get("/api/general/scenarios/nope/export").status_code == 404


# --- delete ----------------------------------------------------------------------------------------------------------------------

def _experiment_with_result(scenario_id, version_id, owner_id, name="Slow drift"):
    with SessionLocal() as session:
        session.add(AttackExperimentRecord(id="exp-1", owner_user_id=owner_id, name=name, scenario_id=scenario_id, scenario_version_id=version_id, preset_id="slow_drift",
                                           seed=3, status="ready", config_json="{}", config_hash="h"))
        session.add(ExperimentResultRecord(id="res-1", experiment_id="exp-1", experiment_name=name, owner_user_id=owner_id, scenario_id=scenario_id,
                                           scenario_version_id=version_id, config_hash="h", config_json="{}", payload_json=json.dumps({"detected": True})))
        session.commit()


def _audit(action):
    with SessionLocal() as session:
        return [json.loads(item.metadata_json) if getattr(item, "metadata_json", None) else None for item in session.scalars(select(AuditLogRecord).where(AuditLogRecord.event_type == action)).all()]


def test_delete_is_refused_while_a_flight_is_active_then_while_pinned_then_cascades_with_counts(general_client, sim):
    client, user = general_client
    first_id, first_version, first_deployment = deployed(client, title="First")
    execution_id = start(client, first_deployment).json()["id"]
    sim.fly()
    _experiment_with_result(first_id, first_version, user.id)
    preview = client.get(f"/api/general/scenarios/{first_id}/delete-preview").json()
    assert [b["code"] for b in preview["blockers"]] == ["execution_active", "pinned_in_mission_control"]
    refused = client.post(f"/api/general/scenarios/{first_id}/delete", json={"confirm_name": "First"})
    assert refused.status_code == 409 and refused.json()["detail"]["code"] == "delete_blocked" and "Abort" in refused.json()["detail"]["message"]
    assert client.post(f"/api/mission-control/executions/{execution_id}/abort").status_code == 200
    sim.advance(3)
    sim.runs[0]["fake"].armed = False
    sim.advance(3)
    assert client.get(f"/api/mission-control/executions/{execution_id}").json()["active"] is False
    # final, but Mission Control still shows this deployment (the newest): refused with the reason
    pinned = client.get(f"/api/general/scenarios/{first_id}/delete-preview").json()
    assert [b["code"] for b in pinned["blockers"]] == ["pinned_in_mission_control"] and "Mission Control" in pinned["blockers"][0]["message"]
    # deploy another mission; now the first can go
    deployed(client, title="Second")
    ready = client.get(f"/api/general/scenarios/{first_id}/delete-preview").json()
    assert ready["blockers"] == [] and ready["counts"]["versions"] == 1 and ready["counts"]["deployments"] == 1 and ready["counts"]["executions"] == 1
    assert ready["counts"]["execution_events"] > 3 and ready["counts"]["experiments"] == 1 and ready["counts"]["experiment_results"] == 1
    wrong = client.post(f"/api/general/scenarios/{first_id}/delete", json={"confirm_name": "first"})
    assert wrong.status_code == 422 and wrong.json()["detail"]["code"] == "name_mismatch"
    done = client.post(f"/api/general/scenarios/{first_id}/delete", json={"confirm_name": "First"})
    assert done.status_code == 200, done.text
    body = done.json()
    assert body["counts"]["experiment_results_kept"] == 1 and body["counts"]["experiment_results_deleted"] == 0 and len(body["content_hash"]) == 64
    assert client.get(f"/api/scenarios/{first_id}").status_code == 404
    assert [row["title"] for row in client.get("/api/general/scenarios").json()["scenarios"]] == ["Second"]
    with SessionLocal() as session:
        result = session.get(ExperimentResultRecord, "res-1")
        assert result is not None and result.experiment_deleted_at is not None            # kept, marked as from a deleted experiment
        assert session.get(AttackExperimentRecord, "exp-1") is None
    audit = _audit("scenario_deleted_by_general")
    assert audit and audit[-1]["counts"]["versions"] == 1 and audit[-1]["content_sha256"] == body["content_hash"] and audit[-1]["title"] == "First"
    # the audit log itself has no delete route
    assert client.delete("/api/general/audit").status_code in (404, 405)


def test_results_can_be_deleted_with_the_scenario_and_a_draft_of_another_user_can_be_deleted(general_client, operator_client, flight):
    general, user = general_client
    operator, _ = operator_client
    draft = create(operator, "Operator draft")
    version = draft["versions"][0]["id"]
    _experiment_with_result(draft["id"], version, user.id, "Baseline")
    done = general.post(f"/api/general/scenarios/{draft['id']}/delete", json={"confirm_name": "Operator draft", "delete_results": True})
    assert done.status_code == 200 and done.json()["counts"]["experiment_results_deleted"] == 1
    with SessionLocal() as session:
        assert session.get(ExperimentResultRecord, "res-1") is None


def test_submitted_scenarios_can_be_deleted_by_a_general_but_an_operator_keeps_only_the_old_draft_delete(general_client, operator_client, flight):
    general, _ = general_client
    operator, _ = operator_client
    mine = create(operator, "Submitted one")
    assert operator.post(f"/api/scenarios/{mine['id']}/versions/{mine['versions'][0]['id']}/submit").status_code == 200
    assert operator.delete(f"/api/scenarios/{mine['id']}").status_code == 409              # unchanged: submitted scenarios cannot be deleted by their owner
    assert general.post(f"/api/general/scenarios/{mine['id']}/delete", json={"confirm_name": "Submitted one"}).status_code == 200
    plain = create(operator, "Plain draft")
    assert operator.delete(f"/api/scenarios/{plain['id']}").status_code == 204             # unchanged: own never-submitted draft


def test_a_general_can_save_a_new_version_of_another_users_scenario_but_an_operator_cannot(general_client, operator_client, flight):
    general, user = general_client
    operator, _ = operator_client
    theirs = create(operator, "Theirs")
    mine = create(general, "Mine")
    edit = {"title": "Theirs", "config": theirs["versions"][0]["config"], "intent": {**theirs["versions"][0]["intent"], "mission_plan": {**theirs["versions"][0]["intent"]["mission_plan"], "description": "edited by the General"}}}
    saved = general.put(f"/api/scenarios/{theirs['id']}", json=edit)
    assert saved.status_code == 200 and saved.json()["versions"][0]["version_number"] == 2 and saved.json()["owner_user_id"] == theirs["owner_user_id"]
    assert operator.put(f"/api/scenarios/{mine['id']}", json={"title": "Mine", "config": mine["versions"][0]["config"], "intent": mine["versions"][0]["intent"]}).status_code == 404
