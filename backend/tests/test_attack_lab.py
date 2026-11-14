"""Attack Lab experiments: pinned to an exact schema-2 version, validated, role-gated, never leaked elsewhere."""
from __future__ import annotations

import json

import pytest

from app.research.attacks import assert_no_ground_truth_deep, forbidden_fields
from test_mission_workflow_e2e import body as plan_body, complete_plan, flight  # noqa: F401  (flight is a fixture)

ATTACK = {"target_aircraft": "px4-1", "type": "drift", "start_s": 20, "duration_s": 40, "magnitude": 1.0, "direction_deg": 90}


def saved_plan(client, count=1, title="Pinned plan", **kwargs):
    response = client.post("/api/scenarios", json=plan_body(complete_plan(count, **kwargs), title))
    assert response.status_code == 201, response.text
    scenario = response.json()
    return scenario["id"], scenario["versions"][0]["id"]


def experiment(scenario_id, version_id, **overrides):
    config = {"attacks": [ATTACK], "response_policy": "operator_decides", "allow_overlap": False}
    config.update(overrides.pop("config", {}))
    return {"name": "Drift test", "scenario_id": scenario_id, "scenario_version_id": version_id, "preset_id": None, "seed": 7,
            "config": config, "status": "draft", **overrides}


def issues(response):
    assert response.status_code == 422, response.text
    return {item["code"] for item in response.json()["detail"]["issues"]}


def test_limits_expose_shared_schema_presets_and_no_execution(general_client):
    client, _ = general_client
    limits = client.get("/api/attack-lab/limits").json()
    assert limits["name"] == "Attack Lab" and limits["executes"] is False
    assert {item["type"] for item in limits["types"]} == {"offset", "jump", "drift", "freeze", "noise", "velocity_inconsistency"}
    assert [item["id"] for item in limits["presets"]] == ["baseline", "slow_drift", "rapid_jump", "strong_offset", "noise_burst", "paired_drift"]
    assert [item["label"] for item in limits["presets"]][:5] == ["Baseline (no attack)", "Slow drift", "Rapid jump", "Strong offset", "Noise burst"]
    assert all(item["does"] and item["useful"] for item in limits["presets"])
    assert limits["handover"]["min_battery_pct"] == 40.0 and limits["max_attacks"] >= 1
    assert "eligibility" in limits["handover"]["label"].lower() and "never triggers" in limits["handover"]["note"]
    assert limits["response"]["policy"] == "operator_decides" and "response_policies" not in limits
    assert set(limits["field_help"]) == {"start", "duration", "magnitude", "direction", "seed"}
    assert "will not see this schedule" in limits["notes"]["hidden_schedule"] and "does not affect the aircraft" in limits["notes"]["software_only"]
    text = str(limits).lower()
    assert "scripted" not in text and "benchmark" not in text and "isolation /" not in text


def test_general_creates_lists_reads_and_edits_drafts_pinned_to_the_version(general_client, flight):
    client, _ = general_client
    scenario_id, version_id = saved_plan(client)
    created = client.post("/api/attack-lab/experiments", json=experiment(scenario_id, version_id))
    assert created.status_code == 201, created.text
    record = created.json()
    assert record["status"] == "draft" and record["scenario_version_id"] == version_id and record["executed"] is False
    assert len(record["config_hash"]) == 64 and record["superseded"] is False and "not executed" in record["note"]
    assert client.get(f"/api/attack-lab/experiments/{record['id']}").json()["config"]["attacks"][0]["type"] == "drift"
    assert [item["id"] for item in client.get("/api/attack-lab/experiments").json()["experiments"]] == [record["id"]]
    edited = client.put(f"/api/attack-lab/experiments/{record['id']}", json=experiment(scenario_id, version_id, name="Renamed", seed=9))
    assert edited.status_code == 200 and edited.json()["seed"] == 9 and edited.json()["config_hash"] != record["config_hash"]
    ready = client.put(f"/api/attack-lab/experiments/{record['id']}", json=experiment(scenario_id, version_id, status="ready"))
    assert ready.json()["status"] == "ready"
    locked = client.put(f"/api/attack-lab/experiments/{record['id']}", json=experiment(scenario_id, version_id))
    assert locked.status_code == 409 and locked.json()["detail"]["code"] == "not_draft"
    assert client.delete(f"/api/attack-lab/experiments/{record['id']}").status_code == 409


def test_experiment_stays_pinned_when_the_plan_gets_a_newer_version(general_client, flight):
    client, _ = general_client
    scenario_id, version_id = saved_plan(client)
    record = client.post("/api/attack-lab/experiments", json=experiment(scenario_id, version_id)).json()
    newer = client.put(f"/api/scenarios/{scenario_id}", json=plan_body(complete_plan().model_copy(update={"notes": "newer"}))).json()
    assert newer["versions"][0]["id"] != version_id
    again = client.get(f"/api/attack-lab/experiments/{record['id']}").json()
    assert again["scenario_version_id"] == version_id and again["superseded"] is True
    assert client.get("/api/attack-lab/experiments").json()["experiments"][0]["superseded"] is True


def test_draft_can_be_deleted(general_client, flight):
    client, _ = general_client
    scenario_id, version_id = saved_plan(client)
    record = client.post("/api/attack-lab/experiments", json=experiment(scenario_id, version_id)).json()
    assert client.delete(f"/api/attack-lab/experiments/{record['id']}").status_code == 204
    assert client.get(f"/api/attack-lab/experiments/{record['id']}").status_code == 404


def test_each_validation_rejection_is_structured(general_client, flight):
    client, _ = general_client
    scenario_id, version_id = saved_plan(client, count=2)
    post = lambda **kw: client.post("/api/attack-lab/experiments", json=experiment(scenario_id, version_id, **kw))
    assert "target_not_in_plan" in issues(post(config={"attacks": [{**ATTACK, "target_aircraft": "px4-4"}]}))
    assert "attack_outside_route" in issues(post(config={"attacks": [{**ATTACK, "start_s": 100, "duration_s": 3000}]}))
    assert "invalid_field" in issues(post(config={"attacks": [{**ATTACK, "magnitude": 9000}]}))
    assert "invalid_field" in issues(post(config={"attacks": [{**ATTACK, "duration_s": 0}]}))
    assert "invalid_field" in issues(post(config={"attacks": [{**ATTACK, "type": "weapon"}]}))
    overlapping = {"attacks": [ATTACK, {**ATTACK, "start_s": 30, "duration_s": 20}]}
    assert "attacks_overlap" in issues(post(config=overlapping))
    assert post(config={**overlapping, "allow_overlap": True}).status_code == 201
    assert "version_not_found" in issues(client.post("/api/attack-lab/experiments", json=experiment(scenario_id, "nope")))
    assert "scenario_not_found" in issues(client.post("/api/attack-lab/experiments", json=experiment("missing", version_id)))
    response = client.post("/api/attack-lab/validate", json=experiment(scenario_id, version_id, config={"attacks": [{**ATTACK, "target_aircraft": "px4-9"}]}))
    assert response.status_code == 200 and response.json()["valid"] is False and response.json()["issues"][0]["code"] == "target_not_in_plan"
    assert client.post("/api/attack-lab/validate", json=experiment(scenario_id, version_id)).json() == {"valid": True, "issues": []}


def test_legacy_schema_one_versions_are_rejected(general_client):
    from app.persistence.database import create_scenario
    client, user = general_client
    legacy = create_scenario(user.id, "Legacy", {"uav_count": 4, "task_count": 10, "seed": 21, "duration": 180, "spoofing_type": "none", "target_uav": "UAV-02",
                                                 "spoof_start": 15, "spoof_duration": 35, "spoof_magnitude": 3, "response_policy": "adaptive", "detector_threshold": None},
                             {"mission_objectives": ["x"], "mission_description": "legacy", "proposed_tasks": [], "uav_role_suggestions": [], "operating_area_notes": "",
                              "environment_notes": "", "constraints": [], "geographic_mission": {"objective": "inspection", "uav_assignments": [], "survey_zones": [], "constraints": [], "ai_recommendations": []}})
    assert "version_not_schema_2" in issues(client.post("/api/attack-lab/experiments", json=experiment(legacy["id"], legacy["versions"][0]["id"])))


def test_preset_constraints(general_client, flight):
    client, _ = general_client
    one_id, one_version = saved_plan(client, count=1, title="One aircraft")
    two_id, two_version = saved_plan(client, count=2, title="Two aircraft")
    make = lambda sid, vid, preset, attacks=None, **config: client.post("/api/attack-lab/experiments", json=experiment(
        sid, vid, preset_id=preset, config={"attacks": [ATTACK] if attacks is None else attacks, **config}))
    assert "preset_baseline_has_attacks" in issues(make(one_id, one_version, "baseline"))
    assert make(one_id, one_version, "baseline", attacks=[]).status_code == 201
    assert "preset_needs_attack" in issues(make(one_id, one_version, "slow_drift", attacks=[]))
    # Every single-aircraft attack preset is valid on a one-aircraft plan: an attack needs no second aircraft.
    for preset in ("slow_drift", "rapid_jump", "strong_offset", "noise_burst"):
        assert make(one_id, one_version, preset).status_code == 201, preset
    # Only a preset whose ATTACK targets two aircraft needs a second one.
    assert "preset_needs_two_aircraft" in issues(make(one_id, one_version, "paired_drift"))
    assert make(two_id, two_version, "paired_drift").status_code == 201
    assert "invalid_field" in issues(make(one_id, one_version, "no_such_preset"))
    # The response is always the operator's: an earlier scripted value is stored and returned as operator_decides.
    scripted = make(two_id, two_version, "strong_offset", response_policy="benchmark_scripted")
    assert scripted.status_code == 201 and scripted.json()["config"]["response_policy"] == "operator_decides"
    assert "benchmark_flagged" not in scripted.json()
    # Preset ids stored before the rename still load, shown under the attack-describing id.
    legacy = make(two_id, two_version, "role_handover")
    assert legacy.status_code == 201 and legacy.json()["preset_id"] == "strong_offset"
    assert make(one_id, one_version, "mild_drift").json()["preset_id"] == "slow_drift"


def test_presets_apply_returns_valid_experiments_sized_from_the_real_route(general_client, flight):
    client, _ = general_client
    scenario_id, version_id = saved_plan(client, count=2)
    expected_types = {"baseline": [], "slow_drift": ["drift"], "rapid_jump": ["jump"], "strong_offset": ["offset"],
                      "noise_burst": ["noise"], "paired_drift": ["drift", "drift"]}
    for preset in expected_types:
        applied = client.post(f"/api/attack-lab/presets/{preset}/apply", json={"scenario_id": scenario_id, "scenario_version_id": version_id, "seed": 5})
        assert applied.status_code == 200, applied.text
        proposal = applied.json()
        assert proposal["preset_id"] == preset and proposal["config"]["handover"]["min_battery_pct"] == 40.0
        assert [item["type"] for item in proposal["config"]["attacks"]] == expected_types[preset]
        assert proposal["config"]["response_policy"] == "operator_decides"
        assert set(proposal["config"]) == {"attacks", "response_policy", "allow_overlap", "handover", "measurements"}  # attack only: no scripted response
        saved = client.post("/api/attack-lab/experiments", json={"name": f"{preset} run", "scenario_id": scenario_id, "scenario_version_id": version_id,
                                                                  "preset_id": preset, "seed": proposal["seed"], "config": proposal["config"], "status": "draft"})
        assert saved.status_code == 201, (preset, saved.text)
    assert {item["target_aircraft"] for item in client.post("/api/attack-lab/presets/paired_drift/apply", json={
        "scenario_id": scenario_id, "scenario_version_id": version_id}).json()["config"]["attacks"]} == {"px4-1", "px4-2"}
    assert client.post("/api/attack-lab/presets/unknown/apply", json={}).status_code == 404
    one_id, one_version = saved_plan(client, count=1, title="One aircraft only")
    refused = client.post("/api/attack-lab/presets/paired_drift/apply", json={"scenario_id": one_id, "scenario_version_id": one_version})
    assert "preset_needs_two_aircraft" in issues(refused) and "second aircraft" in refused.json()["detail"]["message"]


def test_mission_picker_lists_schema_2_versions_with_deployed_latest_and_hidden_presets(general_client, operator_client, flight):
    client, _ = general_client
    operator, _ = operator_client
    assert client.get("/api/attack-lab/missions").json() == {"missions": [], "legacy_only_count": 0}
    scenario_id, first = saved_plan(client, count=1, title="Picker plan")
    assert client.post(f"/api/scenarios/{scenario_id}/versions/{first}/deploy").status_code == 200
    second = client.put(f"/api/scenarios/{scenario_id}", json=plan_body(complete_plan().model_copy(update={"notes": "v2"}), "Picker plan")).json()["versions"][0]["id"]
    payload = client.get("/api/attack-lab/missions").json()
    mission = payload["missions"][0]
    assert mission["scenario_id"] == scenario_id and mission["title"] == "Picker plan"
    newest, oldest = mission["versions"]
    assert (newest["id"], newest["latest"], newest["deployed"], newest["superseded"], newest["in_mission_control"]) == (second, True, False, False, False)
    assert (oldest["id"], oldest["latest"], oldest["deployed"], oldest["superseded"], oldest["in_mission_control"]) == (first, False, True, True, True)
    assert oldest["deployed_at"] and newest["deployed_at"] is None
    assert newest["aircraft"] == [{"uav_id": "px4-1", "role": "primary", "reassignable": True, "duration_s": newest["estimated_duration_s"]}]
    assert newest["area_summary"].startswith(("circle", "polygon")) and newest["estimated_duration_s"] > 0
    assert [item["id"] for item in newest["presets"]["hidden"]] == ["paired_drift"]
    assert "second aircraft" in newest["presets"]["hidden"][0]["reason"]
    assert [item["id"] for item in newest["presets"]["presets"]] == ["baseline", "slow_drift", "rapid_jump", "strong_offset", "noise_burst"]
    # "label" here is a preset display name, not a detector label; nothing else from the leak list may appear.
    assert set(forbidden_fields(_all_keys(payload))) <= {"label"} and "start_s" not in json.dumps(payload)
    two_id, _two = saved_plan(client, count=2, title="Two aircraft picker")
    two = next(item for item in client.get("/api/attack-lab/missions").json()["missions"] if item["scenario_id"] == two_id)
    assert two["versions"][0]["presets"]["hidden"] == [] and len(two["versions"][0]["presets"]["presets"]) == 6
    assert operator.get("/api/attack-lab/missions").json()["missions"] == []  # operators see only their own missions


def test_operator_is_read_only(general_client, operator_client, flight):
    general, _ = general_client
    operator, _ = operator_client
    scenario_id, version_id = saved_plan(general)
    record = general.post("/api/attack-lab/experiments", json=experiment(scenario_id, version_id)).json()
    assert operator.post("/api/attack-lab/experiments", json=experiment(scenario_id, version_id)).status_code == 403
    assert operator.put(f"/api/attack-lab/experiments/{record['id']}", json=experiment(scenario_id, version_id)).status_code == 403
    assert operator.delete(f"/api/attack-lab/experiments/{record['id']}").status_code == 403
    assert operator.get("/api/attack-lab/limits").status_code == 200
    # an operator sees only experiments pinned to missions they own
    assert operator.get("/api/attack-lab/experiments").json()["experiments"] == []
    from fastapi.testclient import TestClient
    from app.main import app
    assert TestClient(app).get("/api/attack-lab/experiments").status_code == 401


def test_attack_details_never_appear_in_scenario_lab_or_mission_control_payloads(general_client, flight):
    client, _ = general_client
    scenario_id, version_id = saved_plan(client)
    client.post("/api/attack-lab/experiments", json=experiment(scenario_id, version_id, preset_id=None, config={"attacks": [{**ATTACK, "type": "offset", "magnitude": 321.5}]}))
    scenario = client.get(f"/api/scenarios/{scenario_id}")
    listing = client.get("/api/scenarios")
    assert client.post(f"/api/scenarios/{scenario_id}/versions/{version_id}/deploy").status_code == 200
    deployment = client.get("/api/mission-control/deployments/latest")
    for response in (scenario, listing, deployment):
        text = json.dumps(response.json())
        assert "321.5" not in text and "attack_schedule" not in text
        assert not forbidden_fields(_all_keys(response.json())), forbidden_fields(_all_keys(response.json()))
    assert_no_ground_truth_deep(deployment.json()["deployment"]["intent"]["mission_plan"])


def _all_keys(value):
    if isinstance(value, dict):
        for key, item in value.items():
            yield key
            yield from _all_keys(item)
    elif isinstance(value, list):
        for item in value:
            yield from _all_keys(item)


def test_replay_hides_the_schedule_until_finished_and_baseline_stays_quiet(general_client, flight, tmp_path_factory, monkeypatch):
    from app import research_routes
    from app.research.threat_data import generate_dataset
    from app.research.threat_models import train_pipeline
    root = tmp_path_factory.mktemp("replay-model")
    generate_dataset(root / "d", seed=11, flights_per_cell=8)
    train_pipeline(root / "d", root / "a", seed=11)
    monkeypatch.setattr(research_routes, "model_dir", root / "a")
    client, _ = general_client
    scenario_id, version_id = saved_plan(client)
    attacked = client.post("/api/attack-lab/experiments", json=experiment(scenario_id, version_id, config={"attacks": [{**ATTACK, "type": "offset", "magnitude": 200, "start_s": 30, "duration_s": 50}]})).json()
    baseline = client.post("/api/attack-lab/experiments", json=experiment(scenario_id, version_id, name="Baseline", preset_id="baseline", config={"attacks": []})).json()
    replay = client.post("/api/research/replays", json={"experiment_id": attacked["id"]})
    assert replay.status_code == 201, replay.text
    payload = replay.json()
    assert not forbidden_fields(_all_keys(payload)) and "attack" not in json.dumps(payload).lower()
    assert payload["source"] == "offline_replay" and payload["aircraft"][0]["aircraft_id"] == "px4-1" and len(payload["aircraft"][0]["t"]) > 100
    early = client.get(f"/api/research/replays/{payload['replay_id']}/evaluation")
    assert early.status_code == 409 and early.json()["detail"]["code"] == "replay_not_finished"
    assert client.post(f"/api/research/replays/{payload['replay_id']}/finish").json() == {"finished": True}
    evaluation = client.get(f"/api/research/replays/{payload['replay_id']}/evaluation").json()
    row = evaluation["aircraft"][0]
    assert evaluation["attacks"][0]["type"] == "offset" and row["attacked"] is True and row["detected"] is True
    assert row["detection_latency_s"] is not None and row["max_gps_position_error_m"] > 100
    assert evaluation["not_measured"]
    quiet = client.post("/api/research/replays", json={"experiment_id": baseline["id"]}).json()
    client.post(f"/api/research/replays/{quiet['replay_id']}/finish")
    quiet_eval = client.get(f"/api/research/replays/{quiet['replay_id']}/evaluation").json()["aircraft"][0]
    assert quiet_eval["attacked"] is False and quiet_eval["max_tier"] in ("Low", "Medium") and quiet_eval["false_alarms"] <= 1
    assert client.post("/api/research/replays", json={"experiment_id": "missing"}).status_code == 404
