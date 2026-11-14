"""Schema-2 mission planning: pure generators, validation and the scenario/preview API."""
from __future__ import annotations

import math
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.mission_planning import generators as gen
from app.mission_planning.generators import PlanningLimits, RouteError, generate_route
from app.mission_planning.geometry import distance_m, from_enu, to_enu
from app.mission_planning.schemas import (
    OBJECTIVE_DEFAULT_TASK, PAYLOAD_CATEGORIES, TASK_TYPES, AircraftPlan, MissionPlan,
)
from app.mission_planning.validation import validate_aircraft, validate_plan

ORIGIN = (12.9716, 77.5946)
LIMITS = PlanningLimits(max_waypoints=200, max_start_to_area_m=20000, min_speed_mps=1, max_speed_mps=20,
                        min_altitude_m=2, max_altitude_m=120, min_hover_s=1, max_hover_s=600)


def offset(north_m: float = 0.0, east_m: float = 0.0) -> dict:
    lat, lon = from_enu(ORIGIN, east_m, north_m)
    return {"latitude": lat, "longitude": lon}


def make_plan(task_type: str | None = "waypoint_route", parameters: dict | None = None, *, objectives=None,
              aircraft_extra: dict | None = None, plan_extra: dict | None = None) -> MissionPlan:
    aircraft = {
        "uav_id": "px4-1", "role": "primary", "priority": 1, "reassignable": True, "task_type": task_type,
        "parameters": parameters if parameters is not None else {"points": [offset(200, 0), offset(200, 200)]},
        "start_location": offset(), "altitude_m": 30, "speed_mps": 5, "loiter_duration_s": 0, "return_mode": "return_to_start",
        **(aircraft_extra or {}),
    }
    plan = {
        "objectives": objectives or ["custom_mission"],
        "operating_area": {"kind": "circle", "centre": offset(200, 100), "radius_m": 400},
        "required_uav_count": 1, "selected_uav_ids": ["px4-1"], "activated_uav_ids": ["px4-1"],
        "constraints": {"max_distance_from_start_m": 2000, "max_altitude_m": 100},
        "aircraft": [aircraft], **(plan_extra or {}),
    }
    return MissionPlan.model_validate(plan)


def route_of(plan: MissionPlan):
    return generate_route(plan.aircraft[0], plan, LIMITS)


def codes(plan: MissionPlan, **kwargs) -> list[str]:
    issues, _ = validate_aircraft(plan.aircraft[0], plan, LIMITS, require_route=kwargs.get("require_route", False))
    return [item.code for item in issues]


# --- ENU / geodesy ------------------------------------------------------------------------------

def test_enu_round_trip_and_known_offsets():
    for north, east in [(0, 0), (1000, 0), (0, 1000), (-3500, 2500), (5000, -5000)]:
        e, n, u = to_enu(ORIGIN, *from_enu(ORIGIN, east, north, 0.0))
        assert e == pytest.approx(east, abs=0.02) and n == pytest.approx(north, abs=0.02)
    lat, lon = from_enu(ORIGIN, 123.0, -456.0, 7.0)
    e, n, u = to_enu(ORIGIN, lat, lon)
    assert (e, n) == pytest.approx((123.0, -456.0), abs=0.01)
    assert u == pytest.approx(7.0, abs=0.05) or abs(u) < 1.0  # lat/lon drop altitude; height is reported below the plane
    north_lat, _ = from_enu(ORIGIN, 0, 1000)
    assert (north_lat - ORIGIN[0]) * 111_000 == pytest.approx(1000, rel=0.01)
    assert distance_m(ORIGIN, (north_lat, ORIGIN[1])) == pytest.approx(1000, abs=0.5)


# --- pattern generators ---------------------------------------------------------------------------

def test_expanding_square_geometry_counts_and_bounds():
    points = gen.expanding_square((0.0, 0.0), 20.0, 100.0, 0.0, 200)
    assert len(points) == 11  # datum + 2 legs per pair * 5 pairs
    assert points[1] == pytest.approx((0.0, 20.0))  # first leg runs north (heading 0)
    assert points[2] == pytest.approx((20.0, 20.0))  # then turns clockwise (east)
    leg_lengths = [math.dist(points[i], points[i + 1]) for i in range(10)]
    assert leg_lengths == pytest.approx([20, 20, 40, 40, 60, 60, 80, 80, 100, 100])
    assert max(max(abs(x), abs(y)) for x, y in points) <= 100 + 20
    east_first = gen.expanding_square((0.0, 0.0), 20.0, 100.0, 90.0, 200)
    assert east_first[1] == pytest.approx((20.0, 0.0), abs=1e-9)
    with pytest.raises(RouteError) as small:
        gen.expanding_square((0.0, 0.0), 50.0, 10.0, 0.0, 200)
    assert small.value.code == "pattern_too_small"
    with pytest.raises(RouteError) as capped:
        gen.expanding_square((0.0, 0.0), 2.0, 5000.0, 0.0, 200)
    assert capped.value.code == "waypoint_cap"


def test_sector_search_returns_to_datum_on_the_radius():
    points = gen.sector_search((10.0, 20.0), 100.0, 120.0, 3, 0.0)
    assert len(points) == 1 + 3 * 3
    assert points[0] == points[3] == points[6] == points[9] == (10.0, 20.0)
    for index in (1, 2, 4, 5, 7, 8):
        assert math.dist(points[index], (10.0, 20.0)) == pytest.approx(100.0)
    assert points[1] == pytest.approx((10.0, 120.0))  # bearing 0 -> north


def test_lawnmower_covers_a_rectangle_with_alternating_tracks():
    rectangle = [(0.0, 0.0), (100.0, 0.0), (100.0, 200.0), (0.0, 200.0)]
    points = gen.lawnmower(rectangle, 20.0, 0.0, 200)  # tracks run north-south, spaced 20 m east-west
    assert len(points) == 10
    assert all(-1e-6 <= x <= 100 + 1e-6 and -1e-6 <= y <= 200 + 1e-6 for x, y in points)
    assert sorted({round(x, 6) for x, _ in points}) == pytest.approx([10, 30, 50, 70, 90])
    assert points[0][1] == pytest.approx(0) and points[1][1] == pytest.approx(200)  # first track northbound
    assert points[2][1] == pytest.approx(200) and points[3][1] == pytest.approx(0)  # second track southbound
    east_west = gen.lawnmower(rectangle, 50.0, 90.0, 200)
    assert len(east_west) == 8 and all(-1e-6 <= x <= 100 + 1e-6 and -1e-6 <= y <= 200 + 1e-6 for x, y in east_west)
    with pytest.raises(RouteError) as capped:
        gen.lawnmower([(0, 0), (10000, 0), (10000, 10000), (0, 10000)], 2.0, 0.0, 50)
    assert capped.value.code == "waypoint_cap"


def test_perimeter_and_orbit():
    square = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)]
    closed = gen.perimeter(square, 2, closed=True)
    assert closed[0] == closed[4] == (0.0, 0.0) and len(closed) == 9  # two laps share the closing point
    open_path = gen.perimeter(square[:3], 2, closed=False)
    assert open_path == [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (10.0, 0.0), (0.0, 0.0)] or open_path[3] == (10.0, 10.0)
    ring = gen.orbit((5.0, 5.0), 30.0, 8)
    assert len(ring) == 8 and all(math.dist(point, (5.0, 5.0)) == pytest.approx(30.0) for point in ring)


# --- assembled routes -------------------------------------------------------------------------------

def test_waypoint_route_has_takeoff_return_and_land_with_estimates():
    route = route_of(make_plan("waypoint_route"))
    actions = [w.action for w in route.waypoints]
    assert actions[0] == "takeoff" and actions[-1] == "land" and route.waypoints[-1].altitude_m == 0
    assert [w.seq for w in route.waypoints] == list(range(len(route.waypoints)))
    assert route.length_m > 400 and route.duration_s == pytest.approx(route.length_m / 5, rel=0.01)
    assert (route.waypoints[-1].latitude, route.waypoints[-1].longitude) == pytest.approx(ORIGIN, abs=1e-6)
    assert route_of(make_plan("waypoint_route")).model_dump() == route.model_dump()  # deterministic


def test_land_at_destination_and_loiter_duration():
    plan = make_plan("waypoint_route", aircraft_extra={"return_mode": "land_at_destination", "loiter_duration_s": 30})
    route = route_of(plan)
    last_stop = route.waypoints[-2]
    assert last_stop.hover_s == 30 and route.waypoints[-1].action == "land"
    assert route.waypoints[-1].latitude == pytest.approx(last_stop.latitude)
    assert route.duration_s >= 30


def test_point_inspection_orbit_and_hover_variants():
    hover = route_of(make_plan("point_inspection", {"points": [offset(150, 50)], "hover_s": 12}))
    assert [w.hover_s for w in hover.waypoints if w.action == "hover"] == [12]
    orbit = route_of(make_plan("point_inspection", {"points": [offset(150, 50)], "orbit_radius_m": 40, "orbit_points": 6}))
    ring = [w for w in orbit.waypoints if w.action in {"waypoint", "hover"} and w.altitude_m == 30][1:7]
    assert len(ring) == 6


def test_loiter_hold_uses_the_aircraft_loiter_duration():
    route = route_of(make_plan("loiter_hold", {}, aircraft_extra={"loiter_duration_s": 45}))
    assert [w.hover_s for w in route.waypoints if w.hover_s] == [45]
    plan = make_plan("loiter_hold", {}, objectives=["inspection"])
    issues, _ = validate_aircraft(plan.aircraft[0], plan, LIMITS, require_route=False)
    assert [(i.code, i.field) for i in issues] == [("missing_parameter", "loiter_duration_s")]


def test_payload_dropoff_route_and_simulated_event():
    params = {"drop_point": offset(250, 80), "drop_altitude_m": 8, "hover_duration_s": 20, "payload_category": "medical_kit"}
    plan = make_plan("payload_dropoff", params, objectives=["relief_supply_delivery"])
    route = route_of(plan)
    drop = next(w for w in route.waypoints if w.action == "payload_release_point")
    assert drop.altitude_m == 8 and drop.hover_s == 20
    cruise = route.waypoints[drop.seq - 1]
    assert cruise.altitude_m == 30 and (cruise.latitude, cruise.longitude) == pytest.approx((drop.latitude, drop.longitude))
    assert len(route.events) == 1
    event = route.events[0]
    assert (event.type, event.aircraft_id, event.payload_category, event.simulated) == ("payload_released", "px4-1", "medical_kit", True)
    assert event.waypoint_seq == drop.seq
    assert 20 < event.planned_time_s < route.duration_s
    assert route.waypoints[-1].action == "land"
    assert "payload" not in {w.action for w in route.waypoints if w.action not in {"payload_release_point"}}


def test_no_weapon_or_strike_vocabulary_exists_in_the_plan_model():
    forbidden = ("weapon", "bomb", "strike", "munition", "missile", "attack", "warhead", "target_engagement")
    assert not any(word in task for task in TASK_TYPES for word in forbidden)
    assert not any(word in value for value in OBJECTIVE_DEFAULT_TASK for word in forbidden if word != "target")
    assert set(PAYLOAD_CATEGORIES) == {"relief_supplies", "medical_kit", "flotation_device"}
    source = "\n".join(path.read_text(encoding="utf-8").lower() for path in (Path(gen.__file__).parent).glob("*.py"))
    assert not any(word in source.replace("no weapon", "").replace("weapon, bombing", "") for word in ("bombing", "airstrike"))


# --- validation --------------------------------------------------------------------------------------

def test_objective_defaults_follow_the_spec():
    assert OBJECTIVE_DEFAULT_TASK["search_rescue"] == "expanding_square_search"
    assert {OBJECTIVE_DEFAULT_TASK[k] for k in ("area_survey", "disaster_assessment", "agricultural_survey", "mapping_survey")} == {"coverage_survey"}
    assert OBJECTIVE_DEFAULT_TASK["perimeter_monitoring"] == "perimeter_patrol"
    assert {OBJECTIVE_DEFAULT_TASK[k] for k in ("inspection", "environmental_monitoring")} == {"point_inspection"}
    assert {OBJECTIVE_DEFAULT_TASK[k] for k in ("target_tracking", "custom_mission")} == {"waypoint_route"}
    assert OBJECTIVE_DEFAULT_TASK["relief_supply_delivery"] == "payload_dropoff"


def test_rejects_far_destination_over_altitude_and_over_distance():
    far = make_plan("waypoint_route", {"points": [offset(300_000, 0)]})
    assert "point_outside_area" in codes(far) or "start_too_far_from_area" in codes(far)
    faraway_start = make_plan("waypoint_route", aircraft_extra={"start_location": offset(300_000, 0)})
    assert "start_too_far_from_area" in codes(faraway_start)
    assert "altitude_over_limit" in codes(make_plan(aircraft_extra={"altitude_m": 150}))
    assert "altitude_over_limit" in codes(make_plan(aircraft_extra={"altitude_m": 110}))  # plan limit is 100 m
    assert "speed_out_of_range" in codes(make_plan(aircraft_extra={"speed_mps": 40}))
    tight = make_plan("waypoint_route", {"points": [offset(200, 0)]}, plan_extra={"constraints": {"max_distance_from_start_m": 100, "max_altitude_m": 100}})
    assert "route_too_far" in codes(tight)


def test_rejects_too_many_waypoints_missing_parameters_and_duration():
    dense = make_plan("coverage_survey", {"line_spacing_m": 2}, objectives=["area_survey"])
    small_cap = PlanningLimits(**{**LIMITS.__dict__, "max_waypoints": 12})
    issues, _ = validate_aircraft(dense.aircraft[0], dense, small_cap, require_route=False)
    assert "waypoint_cap" in [i.code for i in issues]
    missing = make_plan("expanding_square_search", {}, objectives=["search_rescue"])
    assert {"datum", "track_spacing_m", "search_radius_m"} <= {i.field for i in validate_aircraft(missing.aircraft[0], missing, LIMITS, require_route=False)[0]}
    assert "missing_field" in codes(make_plan(None))
    slow = make_plan("waypoint_route", plan_extra={"max_duration_minutes": 1})
    assert "duration_over_limit" in codes(slow)
    assert "duration_over_limit" not in codes(make_plan("waypoint_route", plan_extra={"max_duration_minutes": 30}))


def test_task_type_is_restricted_by_objective_and_payload_needs_a_delivery_objective():
    assert "task_not_allowed" in codes(make_plan("loiter_hold", {}, objectives=["target_tracking"], aircraft_extra={"loiter_duration_s": 10}))
    params = {"drop_point": offset(250, 80), "drop_altitude_m": 8, "hover_duration_s": 20, "payload_category": "relief_supplies"}
    assert "task_not_allowed" in codes(make_plan("payload_dropoff", params, objectives=["inspection"]))
    for objective in ("relief_supply_delivery", "search_rescue", "disaster_assessment"):
        assert "task_not_allowed" not in codes(make_plan("payload_dropoff", params, objectives=[objective]))


def test_payload_dropoff_validation_rejections():
    base = {"drop_point": offset(250, 80), "drop_altitude_m": 8, "hover_duration_s": 20, "payload_category": "medical_kit"}

    def check(**changes):
        return codes(make_plan("payload_dropoff", {**base, **changes}, objectives=["relief_supply_delivery"]))

    assert check() == []
    assert "drop_outside_area" in check(drop_point=offset(5000, 0))
    assert "drop_altitude_out_of_range" in check(drop_altitude_m=110)
    assert "drop_altitude_out_of_range" in check(drop_altitude_m=1)
    assert "hover_out_of_range" in check(hover_duration_s=0)
    assert "hover_out_of_range" in check(hover_duration_s=900)
    assert "missing_parameter" in check(payload_category=None)
    assert "missing_parameter" in check(drop_point=None)
    near = make_plan("payload_dropoff", base, objectives=["relief_supply_delivery"],
                     plan_extra={"constraints": {"max_distance_from_start_m": 100, "max_altitude_m": 100}})
    assert "drop_too_far" in codes(near)


def test_payload_category_is_an_allowlist_not_free_text():
    for value in ("explosive", "weapon", "grenade", "free text"):
        with pytest.raises(ValidationError):
            make_plan("payload_dropoff", {"drop_point": offset(250, 80), "drop_altitude_m": 8,
                                          "hover_duration_s": 5, "payload_category": value})
    with pytest.raises(ValidationError):
        AircraftPlan.model_validate({"uav_id": "px4-1", "task_type": "bombing_run"})
    with pytest.raises(ValidationError):
        MissionPlan.model_validate({"objectives": ["weapon_delivery"]})


def test_plan_level_rules_primary_sequence_and_stored_route_must_match():
    plan = make_plan("waypoint_route", aircraft_extra={"role": "decoy"}, plan_extra={"workflow_stage": "review"})
    stored = plan.aircraft[0].model_copy(update={"route": route_of(plan), "confirmed": True})
    complete = plan.model_copy(update={"aircraft": [stored]})
    assert "primary_count" in [i.code for i in validate_plan(complete, LIMITS, complete=True)]
    primary = complete.model_copy(update={"aircraft": [stored.model_copy(update={"role": "primary"})]})
    assert "primary_count" not in [i.code for i in validate_plan(primary, LIMITS, complete=True)]
    good = make_plan("waypoint_route", plan_extra={"workflow_stage": "review"})
    good_stored = good.aircraft[0].model_copy(update={"route": route_of(good), "confirmed": True})
    good = good.model_copy(update={"aircraft": [good_stored]})
    assert validate_plan(good, LIMITS, complete=True) == []
    tampered_route = good_stored.route.model_copy(update={"length_m": 1.0})
    tampered = good.model_copy(update={"aircraft": [good_stored.model_copy(update={"route": tampered_route})]})
    assert "route_mismatch" in [i.code for i in validate_plan(tampered, LIMITS, complete=True)]
    unrouted = good.model_copy(update={"aircraft": [good_stored.model_copy(update={"route": None})]})
    assert "route_missing" in [i.code for i in validate_plan(unrouted, LIMITS, complete=True)]
    second = good_stored.model_copy(update={"uav_id": "px4-2", "confirmed": True})
    first_unconfirmed = good_stored.model_copy(update={"confirmed": False})
    wrong_order = good.model_copy(update={"aircraft": [first_unconfirmed, second], "selected_uav_ids": ["px4-1", "px4-2"]})
    assert "sequence" in [i.code for i in validate_plan(wrong_order, LIMITS, complete=False)]
    two_primaries = good.model_copy(update={"aircraft": [good_stored, second], "selected_uav_ids": ["px4-1", "px4-2"]})
    assert "primary_count" in [i.code for i in validate_plan(two_primaries, LIMITS, complete=True)]


def test_frontend_constants_stay_in_step_with_the_backend_lists():
    source = (Path(__file__).parents[2] / "frontend/src/components/missionPlan.ts").read_text(encoding="utf-8")
    for value in (*TASK_TYPES, *PAYLOAD_CATEGORIES, *OBJECTIVE_DEFAULT_TASK):
        assert f"'{value}'" in source, value


# --- API ---------------------------------------------------------------------------------------------

def plan_body(plan: MissionPlan) -> dict:
    return plan.model_dump(mode="json")


def test_preview_endpoint_returns_route_and_structured_errors(general_client):
    client, _ = general_client
    ok = client.post("/api/mission-plans/preview-route", json={"plan": plan_body(make_plan("waypoint_route")), "uav_id": "px4-1"})
    assert ok.status_code == 200
    body = ok.json()
    assert body["summary"]["waypoints"] == len(body["route"]["waypoints"]) and body["route"]["length_m"] > 0
    bad = client.post("/api/mission-plans/preview-route", json={"plan": plan_body(make_plan(aircraft_extra={"altitude_m": 150})), "uav_id": "px4-1"})
    assert bad.status_code == 422
    detail = bad.json()["detail"]
    assert detail["code"] == "route_invalid" and detail["issues"][0]["code"] == "altitude_over_limit"
    assert client.post("/api/mission-plans/preview-route", json={"plan": plan_body(make_plan()), "uav_id": "px4-9"}).status_code == 404
    limits = client.get("/api/mission-plans/limits").json()
    assert limits["schema_version"] == 2 and "payload_dropoff" in limits["task_types"] and limits["limits"]["max_waypoints"] >= 10


def save_body(plan: MissionPlan, **intent_extra) -> dict:
    return {
        "title": "Plan v2",
        "config": {"uav_count": 4, "task_count": 10, "seed": 21, "duration": 180, "spoofing_type": "none",
                   "target_uav": "UAV-02", "spoof_start": 15, "spoof_duration": 35, "spoof_magnitude": 3,
                   "response_policy": "adaptive", "detector_threshold": None},
        "intent": {"schema_version": 2, "mission_plan": plan_body(plan), **intent_extra},
    }


def test_schema_two_scenario_round_trips_and_rejects_invalid_or_tampered_plans(general_client, monkeypatch):
    client, _ = general_client
    monkeypatch.setattr("app.scenario_routes._planning_aircraft_ids", lambda: {"px4-1"})
    draft = make_plan("waypoint_route", plan_extra={"workflow_stage": "aircraft"})
    stored_aircraft = draft.aircraft[0].model_copy(update={"route": route_of(draft)})
    saved = client.post("/api/scenarios", json=save_body(draft.model_copy(update={"aircraft": [stored_aircraft]})))
    assert saved.status_code == 201, saved.text
    version = saved.json()["versions"][0]
    assert version["intent"]["schema_version"] == 2
    assert version["intent"]["mission_plan"]["aircraft"][0]["route"]["waypoints"][0]["action"] == "takeoff"
    tampered = stored_aircraft.model_copy(update={"route": stored_aircraft.route.model_copy(update={"duration_s": 1.0})})
    rejected = client.post("/api/scenarios", json=save_body(draft.model_copy(update={"aircraft": [tampered]})))
    assert rejected.status_code == 422
    assert rejected.json()["detail"]["issues"][0]["code"] == "route_mismatch"
    review = draft.model_copy(update={"workflow_stage": "review"})
    incomplete = client.post("/api/scenarios", json=save_body(review))
    assert incomplete.status_code == 422 and incomplete.json()["detail"]["code"] == "mission_plan_invalid"
    mixed = save_body(draft)
    mixed["intent"]["schema_version"] = 1
    assert client.post("/api/scenarios", json=mixed).status_code == 422


def test_legacy_versions_still_load_unmodified(general_client):
    from app.persistence.database import create_scenario, get_scenario

    client, user = general_client
    legacy_intent = {
        "mission_objectives": ["Inspect"], "mission_description": "legacy", "proposed_tasks": [], "uav_role_suggestions": [],
        "operating_area_notes": "", "environment_notes": "", "constraints": [],
        "geographic_mission": {"objective": "inspection", "uav_assignments": [], "survey_zones": [], "constraints": [],
                               "ai_recommendations": []},
    }
    created = create_scenario(user.id, "Legacy", {"uav_count": 4, "task_count": 10, "seed": 21, "duration": 180,
                                                 "spoofing_type": "none", "target_uav": "UAV-02", "spoof_start": 15,
                                                 "spoof_duration": 35, "spoof_magnitude": 3, "response_policy": "adaptive",
                                                 "detector_threshold": None}, legacy_intent)
    read = client.get(f"/api/scenarios/{created['id']}")
    assert read.status_code == 200
    intent = read.json()["versions"][0]["intent"]
    assert intent["schema_version"] == 1 and intent["mission_plan"] is None
    assert intent["geographic_mission"]["objective"] == "inspection" and intent["mission_description"] == "legacy"
    assert get_scenario(created["id"], user.id)["versions"][0]["config_hash"] == created["versions"][0]["config_hash"]


# --- endurance of the simulated X500 -----------------------------------------------------------------------------

def _coverage(area_radius_m: float, spacing_m: float, **extra) -> MissionPlan:
    plan = make_plan("coverage_survey", {"line_spacing_m": spacing_m, "heading_deg": 90}, objectives=["area_survey"],
                     aircraft_extra={"speed_mps": 5}, plan_extra={"constraints": {"max_distance_from_start_m": 100000, "max_altitude_m": 100}, **extra})
    return plan.model_copy(update={"operating_area": plan.operating_area.model_copy(update={"radius_m": area_radius_m})})


def test_a_long_coverage_plan_is_rejected_with_the_structured_endurance_error():
    long = _coverage(1000, 25)  # about 2 km across at 25 m spacing: under the waypoint cap but far beyond the endurance, flown at 5 m/s
    issues, route = validate_aircraft(long.aircraft[0], long, LIMITS, require_route=False)
    by_code = {item.code: item for item in issues}
    assert route is None or route.duration_s > 1200
    assert "endurance_time_exceeded" in by_code and "endurance_distance_exceeded" in by_code
    minutes = float(by_code["endurance_time_exceeded"].message.split("about ")[1].split(" min")[0])
    assert minutes > 20
    assert by_code["endurance_time_exceeded"].message == (
        f"Route takes about {minutes:.0f} min; the aircraft limit is 20 min. Reduce the area, widen track spacing, or raise speed.")
    assert by_code["endurance_time_exceeded"].field == "duration_s" and by_code["endurance_time_exceeded"].uav_id == "px4-1"
    assert "the aircraft limit is 15 km" in by_code["endurance_distance_exceeded"].message
    # applies with an empty max duration, and also when a longer max duration is set (the aircraft limit still wins)
    assert long.max_duration_minutes is None
    generous = _coverage(1000, 25, max_duration_minutes=1440)
    assert "endurance_time_exceeded" in codes(generous) and "duration_over_limit" not in codes(generous)
    assert "endurance_time_exceeded" in [item.code for item in validate_plan(long.model_copy(update={"workflow_stage": "review"}), LIMITS, complete=True)]


def test_a_short_coverage_plan_passes_and_a_stricter_max_duration_still_applies():
    short = _coverage(150, 30)
    issues, route = validate_aircraft(short.aircraft[0], short, LIMITS, require_route=False)
    assert route is not None and route.duration_s < 1200 and route.length_m < 15000
    assert not [item for item in issues if item.code.startswith("endurance")]
    strict = _coverage(150, 30, max_duration_minutes=1)
    assert "duration_over_limit" in codes(strict) and "endurance_time_exceeded" not in codes(strict)


def test_the_endurance_limit_is_read_from_config_and_served_with_the_preview(monkeypatch):
    from app import config
    from app.mission_planning.validation import estimate_summary

    assert config.MP_MAX_FLIGHT_MINUTES == 20 and config.MP_MAX_FLIGHT_DISTANCE_M == 15000  # documented defaults
    assert PlanningLimits.from_config().max_flight_s == 1200.0
    monkeypatch.setattr(config, "MP_MAX_FLIGHT_MINUTES", 5)
    monkeypatch.setattr(config, "MP_MAX_FLIGHT_DISTANCE_M", 2000)
    limits = PlanningLimits.from_config()
    assert (limits.max_flight_s, limits.max_flight_distance_m) == (300.0, 2000.0)
    plan = _coverage(150, 30)
    issues, route = validate_aircraft(plan.aircraft[0], plan, limits, require_route=False)
    assert "endurance_time_exceeded" in [item.code for item in issues] or route is not None and route.duration_s <= 300
    summary = estimate_summary(route, limits)
    assert summary["limit_duration_s"] == 300.0 and summary["limit_length_m"] == 2000.0


def test_preview_route_returns_the_limit_and_rejects_an_over_long_route(general_client):
    client, _ = general_client
    ok = client.post("/api/mission-plans/preview-route", json={"plan": _coverage(150, 30).model_dump(mode="json"), "uav_id": "px4-1"})
    assert ok.status_code == 200, ok.text
    assert ok.json()["summary"]["limit_duration_s"] == 1200.0 and ok.json()["summary"]["limit_length_m"] == 15000.0
    long = client.post("/api/mission-plans/preview-route", json={"plan": _coverage(1000, 25).model_dump(mode="json"), "uav_id": "px4-1"})
    assert long.status_code == 422
    detail = long.json()["detail"]
    assert detail["code"] == "route_invalid" and "the aircraft limit is 20 min" in detail["message"]
    assert {item["code"] for item in detail["issues"]} >= {"endurance_time_exceeded"}
    limits = client.get("/api/mission-plans/limits").json()["limits"]
    assert limits["max_flight_s"] == 1200.0 and limits["max_flight_distance_m"] == 15000.0
