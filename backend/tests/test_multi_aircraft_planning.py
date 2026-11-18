"""Part 2: a Quick Mission plans ANY number of aircraft with per-aircraft rows; the simulation capacity is a separate setting.

* planning (and saving) never depends on how many aircraft are running or on `SIM_MAX_AIRCRAFT`;
* the capacity limits only ACTIVATING / STARTING / deploying-with-aircraft, always with the same plain sentence;
* saving a schema-2 plan does not need live fleet telemetry (only deploy and start verify the running aircraft).
"""
from __future__ import annotations

import json

import pytest

from app import config
from app.mission_planning.generators import PlanningLimits
from app.mission_planning.quick import (ParseRequest, QuickError, QuickRequest, build_quick_plan, capacity_message, capacity_options, parse_mission_text, rules_payload)
from app.mission_planning.schemas import MissionPlan
from app.mission_planning.validation import validate_plan
from test_mission_workflow_e2e import CONFIG, flight  # noqa: F401  (flight is a fixture)

WHERE = {"latitude": 12.9716, "longitude": 77.5946}
LIMITS = PlanningLimits(max_waypoints=200, max_start_to_area_m=20000, min_speed_mps=1, max_speed_mps=20,
                        min_altitude_m=2, max_altitude_m=120, min_hover_s=1, max_hover_s=600)
CAPACITY_SENTENCE = "This mission has {n} aircraft, this machine runs up to {m} reliably. You can start the first {m} aircraft, activate fewer, or still deploy as a plan only."


def plan_for(count: int, kind: str = "area_survey", rows: list | None = None, **extra):
    return build_quick_plan(QuickRequest(mission_type=kind, where=WHERE, drone_count=count, speed_mps=8, aircraft=rows or [], **extra), LIMITS)


# --- any number of aircraft -----------------------------------------------------------------------------------------------

@pytest.mark.parametrize("count", [1, 2, 5, 12, 100])
def test_any_count_plans_validates_and_every_decoy_flies_its_own_path(count):
    result = plan_for(count)
    plan = MissionPlan.model_validate(result["plan"])
    assert len(plan.aircraft) == count == plan.required_uav_count == len(plan.selected_uav_ids) == len(plan.activated_uav_ids)
    assert [a.uav_id for a in plan.aircraft] == [f"px4-{n}" for n in range(1, count + 1)]
    assert [a.role for a in plan.aircraft] == ["primary"] + ["decoy"] * (count - 1)                    # UAV-1 primary, the rest decoy
    assert validate_plan(plan, LIMITS, complete=True) == []
    paths = {tuple((round(w.latitude, 6), round(w.longitude, 6), round(w.altitude_m, 1)) for w in a.route.waypoints[1:-1]) for a in plan.aircraft}
    assert len(paths) == count                                                                         # no two aircraft fly the identical path
    starts = {(round(a.start_location.latitude, 6), round(a.start_location.longitude, 6)) for a in plan.aircraft}
    assert len(starts) == count                                                                        # no two aircraft share a spawn point
    summary = result["summary"]
    assert summary["drones"] == count and len(summary["aircraft"]) == count and all(row["line"] for row in summary["aircraft"])
    assert json.loads(json.dumps(result["plan"]))["required_uav_count"] == count


def test_100_aircraft_use_altitude_layers_only_when_the_lanes_run_out():
    result = plan_for(100)
    lanes = result["summary"]["decoy_lanes"]
    assert lanes >= 24 and 99 > lanes                                                                  # more decoys than lanes in one layer ...
    altitudes = sorted({row["altitude_m"] for row in result["summary"]["aircraft"]})
    assert altitudes[0] == 50 and altitudes[1] == 55 and len(altitudes) == -(-99 // lanes) + 0         # ... so higher layers 5 m apart, nothing else changes
    small = plan_for(12)
    assert {row["altitude_m"] for row in small["summary"]["aircraft"]} == {50}                         # 12 decoys fit one layer: all at the mission altitude


def test_the_id_format_is_the_only_ceiling_of_a_plan():
    with pytest.raises(QuickError) as error:
        plan_for(1000)
    assert error.value.issues[0].code == "too_many_aircraft" and "px4-999" in error.value.issues[0].message
    assert rules_payload(LIMITS)["plan_max_aircraft"] == 999 and rules_payload(LIMITS)["simulation_capacity"] == config.SIM_MAX_AIRCRAFT


# --- per-aircraft rows ----------------------------------------------------------------------------------------------------

def test_rows_set_name_role_altitude_speed_and_start_offset_and_auto_stays_auto():
    rows = [{"name": "Lead"}, {"role": "standby"}, {"name": "East", "altitude_m": 60, "speed_mps": 6, "start_offset_m": 20}]
    result = plan_for(3, rows=rows)
    plan = result["plan"]
    assert [a.get("callsign") for a in plan["aircraft"]] == ["Lead", None, "East"]                    # an untyped name is not stored: auto stays auto
    assert [a["role"] for a in plan["aircraft"]] == ["primary", "standby", "decoy"]
    east = plan["aircraft"][2]
    assert east["altitude_m"] == 60 and east["speed_mps"] == 6
    lead = plan["aircraft"][0]
    north = (east["start_location"]["latitude"] - lead["start_location"]["latitude"]) * 111_320
    assert north == pytest.approx(20, abs=0.5)                                                         # 20 m north of the mission start
    names = [row["name"] for row in result["summary"]["aircraft"]]
    assert names == ["Lead", "UAV-2", "East"]
    assert "never armed or flown" in result["summary"]["aircraft"][1]["line"]
    assert "(decoy) flies a parallel path" in result["summary"]["aircraft"][2]["line"] and "60 m" in result["summary"]["aircraft"][2]["line"]
    assert result["summary"]["aircraft"][0]["line"].startswith("Lead (primary) flies the mission route")


def test_a_plan_without_typed_names_is_stored_exactly_as_before_the_field_existed():
    plain = plan_for(2)["plan"]
    assert all("callsign" not in item for item in plain["aircraft"])


def test_validation_needs_a_primary_distinct_paths_names_and_limits():
    cases = [
        ([{"role": "decoy"}, {"role": "standby"}], "primary_count"),
        ([{}, {"role": "primary"}], "identical_path"),                                                 # two primaries flying the same pattern
        ([{"name": "A"}, {"name": "a"}], "duplicate_name"),
        ([{}, {"altitude_m": 900}], "aircraft_altitude_out_of_range"),
        ([{}, {"speed_mps": 99}], "aircraft_speed_out_of_range"),
        ([{}, {"role": "standby", "start_offset_m": 0}], "start_conflict"),                            # the standby stands exactly on the primary's start
    ]
    for rows, code in cases:
        with pytest.raises(QuickError) as error:
            plan_for(2, rows=rows)
        assert error.value.issues[0].code == code, (rows, error.value.issues[0])
    # two primaries are fine once their paths differ (another altitude), and the plan then allows more than one primary
    two = plan_for(2, rows=[{}, {"role": "primary", "altitude_m": 70}])["plan"]
    assert [a["role"] for a in two["aircraft"]] == ["primary", "primary"] and two["allow_multiple_primary"] is True


def test_roles_that_the_older_clients_send_still_work():
    result = build_quick_plan(QuickRequest(mission_type="area_survey", where=WHERE, drone_count=3, aircraft_ids=["px4-1", "px4-2", "px4-3"], roles=["standby", "decoy"], speed_mps=8), LIMITS)
    assert result["summary"]["roles"] == ["primary", "standby", "decoy"]


# --- capacity is separate from planning ---------------------------------------------------------------------------------

def test_planning_is_never_blocked_by_the_capacity_setting_it_only_adds_the_plain_sentence(monkeypatch):
    monkeypatch.setattr(config, "SIM_MAX_AIRCRAFT", 2)
    result = plan_for(12)
    assert len(result["plan"]["aircraft"]) == 12
    note = next(item for item in result["warnings"] if item["code"] == "exceeds_simulation_capacity")
    assert note["message"] == CAPACITY_SENTENCE.format(n=12, m=2) == capacity_message(12, 2)
    assert result["summary"]["simulation_capacity"] == 2
    assert not [item for item in plan_for(2)["warnings"] if item["code"] == "exceeds_simulation_capacity"]
    assert [option["id"] for option in capacity_options(12, 2)] == ["start_first", "activate_fewer", "plan_only"]
    assert capacity_options(12, 2)[0]["aircraft"] == 2


def test_the_same_warning_is_not_repeated_for_every_aircraft():
    far = {"latitude": 12.9716, "longitude": 77.5946}
    result = build_quick_plan(QuickRequest(mission_type="relief_delivery", where=far, drone_count=30, speed_mps=8, aircraft_position={"latitude": 12.9743, "longitude": 77.5946}), LIMITS)
    codes = [item["code"] for item in result["warnings"]]
    assert len(codes) == len(set(codes)) or max(codes.count(code) for code in set(codes)) <= 3


# --- the assistant understands counts and roles ---------------------------------------------------------------------------

def parse(text=None, **kw):
    return parse_mission_text(ParseRequest(text=text, **kw), LIMITS)


def test_the_assistant_reads_counts_decoys_and_role_changes():
    result = parse("survey here with 12 UAVs, 2 decoys")
    assert result["state"]["drone_count"] == 12 and result["state"]["decoys"] == 2 and result["complete"] is True
    result = parse("search here with 3 drones and 1 standby")
    assert (result["state"]["drone_count"], result["state"]["standbys"]) == (3, 1)
    result = parse("survey here with 12 drones, make UAV-7 standby and aircraft 3 as primary")
    assert result["state"]["drone_count"] == 12 and result["state"]["standbys"] is None and result["state"]["role_changes"] == {"7": "standby", "3": "primary"}
    assert "UAV-7 as standby" in result["understood"]
    # planning is not limited by how many aircraft are running
    assert parse("search here with 7 drones", context={"available_aircraft": 1, "has_aircraft_position": True})["state"]["drone_count"] == 7


def test_the_typo_guard_asks_once_and_is_not_a_limit():
    first = parse("survey here with 120 drones")
    assert first["question"]["id"] == "drones_typo" and "Did you mean 12?" in first["question"]["text"] and first["complete"] is False
    keep = parse(None, choice="drones:120", state=first["state"])
    assert keep["state"]["drone_count"] == 120 and keep["question"] is None and keep["complete"] is True    # asked once, never again
    fix = parse(None, choice="drones:12", state=first["state"])
    assert fix["state"]["drone_count"] == 12 and fix["complete"] is True
    assert parse("survey here with 50 drones")["question"] is None                                          # the guard starts above 50
    over = parse("survey here with 5000 drones")
    assert over["question"]["id"] == "drones" and "px4-999" in over["question"]["text"]


# --- through the real HTTP stack ------------------------------------------------------------------------------------------

def quick_body(count, **extra):
    return {"mission_type": "area_survey", "where": WHERE, "drone_count": count, "speed_mps": 8, **extra}


def save_body(plan: dict, title="Many aircraft"):
    return {"title": title, "config": CONFIG, "intent": {"schema_version": 2, "mission_plan": plan, "uav_fault_assignments": []}}


def test_quick_endpoint_plans_12_aircraft_and_the_save_needs_no_live_telemetry_and_no_running_fleet(general_client, monkeypatch):
    client, _ = general_client
    monkeypatch.setattr("app.scenario_routes._planning_aircraft_ids", lambda: None)               # telemetry disabled / no aircraft at all
    planned = client.post("/api/mission-plans/quick", json=quick_body(12))
    assert planned.status_code == 200, planned.text
    assert len(planned.json()["plan"]["aircraft"]) == 12
    saved = client.post("/api/scenarios", json=save_body(planned.json()["plan"]))
    assert saved.status_code == 201, saved.text                                                    # 12 aircraft, nothing running, still saved
    assert len(saved.json()["versions"][0]["intent"]["mission_plan"]["aircraft"]) == 12
    again = client.put(f"/api/scenarios/{saved.json()['id']}", json=save_body(planned.json()["plan"], "Many aircraft"))
    assert again.status_code == 200, again.text


def test_a_100_aircraft_plan_fits_the_plan_body_limit_while_other_requests_keep_the_small_one(general_client):
    client, _ = general_client
    planned = client.post("/api/mission-plans/quick", json=quick_body(100))
    assert planned.status_code == 200, planned.text
    saved = client.post("/api/scenarios", json=save_body(planned.json()["plan"], "Hundred aircraft"))
    assert saved.status_code == 201, saved.text
    assert len(json.dumps(planned.json()["plan"])) > config.MAX_HTTP_BODY_BYTES                  # bigger than the general limit, accepted on plan routes
    big = client.post("/api/fleet/activate", content=b"{" + b" " * (config.MAX_HTTP_BODY_BYTES + 10) + b"}", headers={"Content-Type": "application/json"})
    assert big.status_code == 413                                                                  # every other route keeps the small limit


def test_activation_above_the_capacity_is_refused_with_the_plain_sentence_and_three_options(general_client, monkeypatch):
    client, _ = general_client
    refused = client.post("/api/fleet/activate", json={"required_uav_count": 6})
    assert refused.status_code == 409
    detail = refused.json()["detail"]
    assert detail["code"] == "capacity_exceeded" and detail["capacity"] == 5
    assert detail["message"] == CAPACITY_SENTENCE.format(n=6, m=5)
    assert [item["id"] for item in detail["options"]] == ["start_first", "activate_fewer", "plan_only"]
    assert detail["verified"] is False
    # within the capacity the request reaches the normal checks (here: the UDP receivers are not bound in tests)
    within = client.post("/api/fleet/activate", json={"required_uav_count": 5})
    assert within.status_code == 503 and within.json()["detail"]["code"] == "receivers_unavailable"


def test_deploy_above_the_capacity_says_so_and_a_plan_only_deploy_works_without_any_aircraft(general_client, monkeypatch):
    client, _ = general_client
    calls = []
    monkeypatch.setattr("app.scenario_routes.invoke_fleet_manager", lambda *a, **k: calls.append(a) or {"instances": []})
    planned = client.post("/api/mission-plans/quick", json=quick_body(6)).json()
    saved = client.post("/api/scenarios", json=save_body(planned["plan"], "Six aircraft")).json()
    version = saved["versions"][0]["id"]
    blocked = client.post(f"/api/scenarios/{saved['id']}/versions/{version}/deploy")
    assert blocked.status_code == 409
    detail = blocked.json()["detail"]
    assert detail["code"] == "capacity_exceeded" and detail["message"] == CAPACITY_SENTENCE.format(n=6, m=5) and detail["required_uav_count"] == 6
    assert calls == []                                                                             # nothing was started or even asked of the fleet manager
    plan_only = client.post(f"/api/scenarios/{saved['id']}/versions/{version}/deploy", json={"mode": "plan_only"})
    assert plan_only.status_code == 200, plan_only.text
    body = plan_only.json()
    assert body["plan_only"] is True and body["execution_status"] == "DEPLOYED_PLAN_ONLY" and body["requested_uav_count"] == 6 and body["activated_uav_count"] == 0
    assert calls == []                                                                             # plan only: no process, telemetry or pose was checked
    latest = client.get("/api/mission-control/deployments/latest").json()["deployment"]
    assert latest["id"] == body["id"] and latest["plan_only"] is True
    # the start pre-check says the same sentence and offers the options
    preflight = client.get(f"/api/mission-control/executions/preflight?deployment_id={body['id']}").json()
    check = next(item for item in preflight["checks"] if item["key"] == "aircraft_count")
    assert check["ok"] is False and check["detail"] == CAPACITY_SENTENCE.format(n=6, m=5) and check["code"] == "capacity_exceeded"
    assert [item["id"] for item in check["options"]] == ["start_first", "activate_fewer", "plan_only"]
    assert preflight["can_start"] is False and CAPACITY_SENTENCE.format(n=6, m=5) in preflight["blocking"]
    started = client.post("/api/mission-control/executions", json={"deployment_id": body["id"], "confirm": True})
    assert started.status_code == 409                                                              # a start never fails silently: the blocking list carries the sentence
    assert CAPACITY_SENTENCE.format(n=6, m=5) in json.dumps(started.json())


def test_within_the_capacity_but_more_aircraft_than_are_running_the_precheck_offers_to_activate_them(general_client, flight, monkeypatch):
    client, _ = general_client
    ids = ["px4-1", "px4-2"]                                                                      # two aircraft run; the mission needs three
    monkeypatch.setattr("app.fleet_routes._verified_fleet", lambda count: (
        [{"aircraft_id": ids[i], "system_id": i + 1, "gazebo_model": f"x500_{i}", "verified": True} for i in range(min(count, 2))], []))
    planned = client.post("/api/mission-plans/quick", json=quick_body(3)).json()
    saved = client.post("/api/scenarios", json=save_body(planned["plan"], "Three aircraft")).json()
    version = saved["versions"][0]["id"]
    deployed = client.post(f"/api/scenarios/{saved['id']}/versions/{version}/deploy")
    assert deployed.status_code == 503 and deployed.json()["detail"]["code"] == "fleet_not_verified"         # only two aircraft run in this fixture
    plan_only = client.post(f"/api/scenarios/{saved['id']}/versions/{version}/deploy", json={"mode": "plan_only"}).json()
    preflight = client.get(f"/api/mission-control/executions/preflight?deployment_id={plan_only['id']}").json()
    check = next(item for item in preflight["checks"] if item["key"] == "aircraft_count")
    assert check["ok"] is False and check["offer_activation"] == 3 and check["code"] == "aircraft_not_running"


def test_an_operator_who_owns_quick_missions_lists_only_their_own_saves_changes_and_deploys_only_after_approval(general_client, operator_client, flight):
    """Part 2B-b: the Operator's own missions, through the same API the Quick Mission page uses."""
    general, _ = general_client
    operator, _ = operator_client
    planned = operator.post("/api/mission-plans/quick", json=quick_body(2)).json()
    saved = operator.post("/api/scenarios", json=save_body(planned["plan"], "Operator quick")).json()
    other = general.post("/api/scenarios", json=save_body(planned["plan"], "General quick")).json()
    own = [item["title"] for item in operator.get("/api/scenarios").json()["scenarios"]]
    assert own == ["Operator quick"]                                                     # an Operator lists only their own missions
    assert "General quick" in [item["title"] for item in general.get("/api/scenarios").json()["scenarios"]]
    assert operator.get(f"/api/scenarios/{other['id']}").status_code == 404           # and cannot open somebody else's
    version = saved["versions"][0]
    again = operator.put(f"/api/scenarios/{saved['id']}", json=save_body({**planned["plan"], "notes": "changed by the operator"}, "Operator quick"))   # an identical save is not a new version
    assert again.status_code == 200 and again.json()["versions"][0]["version_number"] == 2        # Save changes = a new version
    latest = again.json()["versions"][0]["id"]
    blocked = operator.post(f"/api/scenarios/{saved['id']}/versions/{latest}/deploy")
    assert blocked.status_code == 409 and blocked.json()["detail"]["code"] == "approval_required"
    # an Operator cannot deploy "as a plan only" around the approval either
    assert operator.post(f"/api/scenarios/{saved['id']}/versions/{latest}/deploy", json={"mode": "plan_only"}).json()["detail"]["code"] == "approval_required"
    submitted = operator.post(f"/api/scenarios/{saved['id']}/versions/{latest}/submit").json()
    assert general.post(f"/api/general/scenario-approvals/{submitted['id']}/decision", json={"decision": "approve", "reason": "ok"}).status_code == 200
    deployed = operator.post(f"/api/scenarios/{saved['id']}/versions/{latest}/deploy")
    assert deployed.status_code == 200 and deployed.json()["plan_only"] is False and deployed.json()["activated_uav_count"] == 2
    assert version["version_number"] == 1


def test_the_default_simulation_capacity_is_the_measured_one_and_the_environment_can_override_it():
    """docs/FLEET_CAPACITY.md: 6 aircraft started 3 of 3 times on this machine, 7 only 1 of 3. The other tests run with TRINETRA_SIM_MAX_AIRCRAFT=5 (conftest)."""
    assert config.SIM_MAX_AIRCRAFT_DEFAULT == 6
    assert config.SIM_MAX_AIRCRAFT == 5                        # conftest sets the environment variable for the tests
    assert config.env_int("TRINETRA_SIM_MAX_AIRCRAFT", config.SIM_MAX_AIRCRAFT_DEFAULT, 1, 64) == 5
    assert config.env_int("TRINETRA_SIM_MAX_AIRCRAFT_UNSET_FOR_THE_TEST", config.SIM_MAX_AIRCRAFT_DEFAULT, 1, 64) == 6
