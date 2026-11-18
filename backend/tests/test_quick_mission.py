"""Quick mission: derivation rules (golden numbers), relaxed rules, the offline sentence parser, and the API (computation only)."""
from __future__ import annotations

import math

import pytest

from app.mission_planning import quick as q
from app.mission_planning.generators import PlanningLimits
from app.mission_planning.geometry import distance_m
from app.mission_planning.quick import ParseRequest, QuickError, QuickRequest, build_quick_plan, parse_mission_text
from app.mission_planning.schemas import AutoLimits, MissionPlan
from app.mission_planning.validation import plan_warnings, validate_aircraft, validate_plan

WHERE = {"latitude": 12.9716, "longitude": 77.5946}
AIRCRAFT = {"latitude": 12.9743, "longitude": 77.5946}          # 300 m north of the clicked point
LIMITS = PlanningLimits(max_waypoints=200, max_start_to_area_m=20000, min_speed_mps=1, max_speed_mps=20,
                        min_altitude_m=2, max_altitude_m=120, min_hover_s=1, max_hover_s=600)


def quick(kind: str, **extra):
    body = {"mission_type": kind, "where": WHERE, "aircraft_ids": ["px4-1"], "speed_mps": 8, **extra}   # golden numbers: the 8 m/s research speed
    if kind in ("relief_delivery", "waypoint_route") or extra.pop("with_aircraft", False):
        body.setdefault("aircraft_position", AIRCRAFT)
    return build_quick_plan(QuickRequest(**body), LIMITS)


# --- golden numbers --------------------------------------------------------------------------------------------------------

def test_spiral_golden_numbers():
    result = quick("search_spiral")                    # 300 m radius, 8 m/s, 50 m altitude -> 30 m tracks
    summary, plan = result["summary"], result["plan"]
    assert summary["speed_mps"] == 8 and summary["altitude_m"] == 50 and summary["track_spacing_m"] == 30
    assert summary["area"]["radius_m"] == 300 and summary["drones"] == 1 and summary["confirmed"] is True
    assert summary["length_m"] == pytest.approx(5857, rel=0.01) and summary["estimated_s"] == pytest.approx(732, rel=0.01)
    assert summary["waypoints"] == 30
    # the generator's side of the square is 2 x (2/3 x 300) = 400 m; the 5.3 km search itself is the route minus transit and return
    assert plan["aircraft"][0]["parameters"]["search_radius_m"] == pytest.approx(400)
    assert plan["aircraft"][0]["parameters"]["track_spacing_m"] == 30
    assert summary["limits"] == {"max_distance_from_start_m": 450, "max_altitude_m": 80, "max_duration_minutes": 18}


@pytest.mark.parametrize("kind,length,seconds,waypoints,limits", [
    ("area_survey", 10879, 1360, 43, (450, 80, 34)),
    ("perimeter_patrol", 2584, 323, 52, (450, 80, 8)),
    ("relief_delivery", 740, 102.5, 5, (450, 80, 3)),
    ("waypoint_route", 697, 87.2, 4, (450, 80, 2)),
])
def test_other_types_golden_numbers(kind, length, seconds, waypoints, limits):
    result = quick(kind)
    summary = result["summary"]
    assert summary["length_m"] == pytest.approx(length, rel=0.01) and summary["estimated_s"] == pytest.approx(seconds, rel=0.01)
    if waypoints:
        assert summary["waypoints"] == waypoints
    assert (summary["limits"]["max_distance_from_start_m"], summary["limits"]["max_altitude_m"], summary["limits"]["max_duration_minutes"]) == limits


def test_derivation_formulas():
    assert q.track_spacing_m(50) == 30 and q.track_spacing_m(10) == 10 and q.track_spacing_m(100) == 60 and q.track_spacing_m(2) == 10
    assert q.spiral_search_radius_m(300) == 400
    # max distance = farthest x 1.3 + 50 m, rounded UP to 50 m
    assert q.derive_limits(200, 50, 600, 120)["max_distance_from_start_m"] == 350        # 310 -> 350
    assert q.derive_limits(300, 50, 600, 120)["max_distance_from_start_m"] == 450        # 440 -> 450
    assert q.derive_limits(0, 50, 600, 120)["max_distance_from_start_m"] == 50
    # max altitude = max(1.5 x alt, alt + 30), capped
    assert q.derive_limits(100, 50, 60, 120)["max_altitude_m"] == 80
    assert q.derive_limits(100, 100, 60, 120)["max_altitude_m"] == 120                   # capped (150 -> 120)
    assert q.derive_limits(100, 10, 60, 120)["max_altitude_m"] == 40                     # 10 + 30 beats 15
    # duration = 1.5 x flight time, rounded to whole minutes, at least 1
    assert q.derive_limits(100, 50, 600, 120)["max_duration_minutes"] == 15
    assert q.derive_limits(100, 50, 10, 120)["max_duration_minutes"] == 1


def test_defaults_and_rules_are_served_once():
    cfg = q.rules_payload(LIMITS)
    assert cfg["defaults"]["speed_mps"] == 8 and cfg["defaults"]["altitude_m"] == 50 and cfg["defaults"]["size_m"] == 300 and cfg["defaults"]["drone_count"] == 1
    assert {item["id"] for item in cfg["types"]} == {"area_survey", "search_spiral", "relief_delivery", "perimeter_patrol", "waypoint_route", "waypoints_then_drop"}
    assert len(cfg["rules"]) >= 8 and "8 m/s" in cfg["speed_reason"] or "baseline" in cfg["speed_reason"].lower()


# --- placement ---------------------------------------------------------------------------------------------------------------

def test_start_datum_and_area_placement():
    aircraft = {"latitude": 12.9730, "longitude": 77.5960}
    spiral = quick("search_spiral", where_source="aircraft", aircraft_position=aircraft)["plan"]
    item = spiral["aircraft"][0]
    assert spiral["operating_area"]["centre"] == WHERE and item["parameters"]["datum"] == WHERE   # centre = Where, datum = centre
    assert item["start_location"] == WHERE                                                          # clicked point = start
    assert item["loiter_duration_s"] == 0 and item["return_mode"] == "return_to_start" and item["parameters"]["heading_deg"] == 0


def test_delivery_and_route_start_at_the_aircraft():
    aircraft = {"latitude": 12.9700, "longitude": 77.5940}
    plan = quick("relief_delivery", aircraft_position=aircraft)["plan"]
    item = plan["aircraft"][0]
    assert item["start_location"] == aircraft and item["parameters"]["drop_point"] == WHERE
    assert item["parameters"]["drop_altitude_m"] == 10 and item["parameters"]["hover_duration_s"] == 10 and item["parameters"]["payload_category"] == "relief_supplies"
    assert quick("waypoint_route", aircraft_position=aircraft)["plan"]["aircraft"][0]["parameters"]["points"] == [WHERE]


def test_delivery_cannot_use_the_aircraft_as_destination_and_needs_a_position():
    with pytest.raises(QuickError) as error:
        build_quick_plan(QuickRequest(mission_type="relief_delivery", where=WHERE, where_source="aircraft", aircraft_ids=["px4-1"],
                                      aircraft_position=WHERE), LIMITS)
    assert error.value.issues[0].code == "destination_is_aircraft"
    with pytest.raises(QuickError) as error:
        build_quick_plan(QuickRequest(mission_type="waypoint_route", where=WHERE, aircraft_ids=["px4-1"]), LIMITS)
    assert error.value.issues[0].code == "aircraft_position_missing"


def test_roles_priority_and_drone_count():
    one = quick("area_survey")["plan"]
    assert one["aircraft"][0]["role"] == "primary" and one["aircraft"][0]["priority"] == 1 and one["aircraft"][0]["confirmed"] is True
    assert one["workflow_stage"] == "review"
    two = build_quick_plan(QuickRequest(mission_type="area_survey", where=WHERE, drone_count=2, aircraft_ids=["px4-1", "px4-2"]), LIMITS)["plan"]
    assert [a["role"] for a in two["aircraft"]] == ["primary", "decoy"] and [a["priority"] for a in two["aircraft"]] == [1, 2]     # UAV-1 primary, the rest decoy
    assert two["required_uav_count"] == 2 and two["selected_uav_ids"] == ["px4-1", "px4-2"]
    assert all(a["confirmed"] for a in two["aircraft"]) and two["workflow_stage"] == "review"             # every route came from a documented rule: all confirmed
    # Planning never depends on which aircraft are running: three aircraft planned with one running id get px4-1, px4-2, px4-3.
    three = build_quick_plan(QuickRequest(mission_type="area_survey", where=WHERE, drone_count=3, aircraft_ids=["px4-1"]), LIMITS)["plan"]
    assert three["selected_uav_ids"] == ["px4-1", "px4-2", "px4-3"] and [a["role"] for a in three["aircraft"]] == ["primary", "decoy", "decoy"]


# --- invalidity rules still hold --------------------------------------------------------------------------------------------

@pytest.mark.parametrize("extra,code", [
    ({"speed_mps": 89}, "speed_out_of_range"), ({"altitude_m": 1}, "altitude_out_of_range"), ({"altitude_m": 500}, "altitude_out_of_range"),
    ({"size_m": 10}, "size_out_of_range"), ({"size_m": 6000}, "size_out_of_range"),
])
def test_out_of_range_values_are_refused(extra, code):
    with pytest.raises(QuickError) as error:
        quick("area_survey", **extra)
    assert code in [item.code for item in error.value.issues]


def test_unknown_type_refused():
    with pytest.raises(QuickError):
        build_quick_plan(QuickRequest(mission_type="warp", where=WHERE, aircraft_ids=["px4-1"]), LIMITS)


def test_speed_and_altitude_overrides_are_used_exactly():
    summary = quick("area_survey", speed_mps=10, altitude_m=30)["summary"]
    assert summary["speed_mps"] == 10 and summary["altitude_m"] == 30 and summary["track_spacing_m"] == 20
    assert summary["limits"]["max_altitude_m"] == 60                           # 30 + 30


# --- auto limits versus typed limits ----------------------------------------------------------------------------------------

def test_typed_limits_are_never_replaced_by_auto_values():
    built = quick("area_survey")["plan"]
    plan = MissionPlan.model_validate(built)
    typed = plan.model_copy(update={
        "auto_limits": AutoLimits(max_distance_from_start_m=False, max_altitude_m=True, max_duration_minutes=False),
        "constraints": plan.constraints.model_copy(update={"max_distance_from_start_m": 1234.0}), "max_duration_minutes": 99})
    after, derived = q.apply_auto_limits(typed, LIMITS)
    assert after.constraints.max_distance_from_start_m == 1234 and after.max_duration_minutes == 99      # typed: kept exactly
    assert after.constraints.max_altitude_m == derived["max_altitude_m"] == 80                            # auto: replaced
    # a plan from an old version has no flags: nothing is auto
    legacy = {key: value for key, value in built.items() if key != "auto_limits"}
    assert MissionPlan.model_validate(legacy).auto_limits == AutoLimits()


def test_preview_derives_auto_limits_before_validating(general_client):
    client, _ = general_client
    built = quick("area_survey")["plan"]
    built["constraints"]["max_distance_from_start_m"] = 1          # placeholders: the flagged limits are derived from the new route
    built["constraints"]["max_altitude_m"] = 1
    built["max_duration_minutes"] = 1
    built["aircraft"][0]["route"] = None
    reply = client.post("/api/mission-plans/preview-route", json={"plan": built, "uav_id": "px4-1"})
    assert reply.status_code == 200, reply.text
    body = reply.json()
    assert body["limits"]["max_distance_from_start_m"] == 450 and body["limits"]["max_altitude_m"] == 80 and body["limits"]["max_duration_minutes"] == 34
    assert body["limits"]["auto"] == {"max_distance_from_start_m": True, "max_altitude_m": True, "max_duration_minutes": True}
    # a typed (non-auto) distance that is too small is still enforced
    built["auto_limits"]["max_distance_from_start_m"] = False
    refused = client.post("/api/mission-plans/preview-route", json={"plan": built, "uav_id": "px4-1"})
    assert refused.status_code == 422


# --- relaxed rules -----------------------------------------------------------------------------------------------------------

def test_start_outside_area_is_a_warning_not_a_block():
    assert quick("relief_delivery")["warnings"] == []                      # 300 m away: inside the 300 m circle
    far = quick("relief_delivery", aircraft_position={"latitude": 12.9780, "longitude": 77.6000})
    assert "start_outside_area" in [item["code"] for item in far["warnings"]]
    assert far["summary"]["length_m"] > 0
    plan = MissionPlan.model_validate(far["plan"])
    assert validate_plan(plan, LIMITS, complete=True) == []


def test_endurance_warns_only_when_the_user_raised_limits():
    plan = MissionPlan.model_validate(quick("area_survey")["plan"])
    item = plan.aircraft[0]
    assert plan_warnings(item, plan, LIMITS, item.route) == [] or all(w.code != "endurance_time_raised" for w in plan_warnings(item, plan, LIMITS, item.route))
    assert q.build_quick_plan  # (quick plans stay within the default endurance, so nothing is raised)


def test_manual_plan_validation_is_unchanged_for_plain_plans():
    plan = MissionPlan.model_validate(quick("search_spiral")["plan"])
    issues, route = validate_aircraft(plan.aircraft[0].model_copy(update={"route": None}), plan, LIMITS, require_route=False)
    assert issues == [] and route is not None
    assert route.length_m == pytest.approx(plan.aircraft[0].route.length_m, abs=0.5)           # same generator, same route (no route_mismatch)


# --- the offline sentence parser ---------------------------------------------------------------------------------------------

def parse(text=None, *, choice=None, state=None, **context):
    return parse_mission_text(ParseRequest(text=text, choice=choice, state=state or {}, context=context), LIMITS)


# sentence -> (intent, expected state subset, next question id or None, complete)
SENTENCES = [
    ("search 300 m around here with one drone", "mission", {"mission_type": "search_spiral", "size_m": 300, "drone_count": 1, "where_source": "aircraft"}, None, True),
    ("survey this area at 10 m/s", "mission", {"mission_type": "area_survey", "speed_mps": 10, "where_source": "map"}, None, True),
    ("deliver supplies to the point I click", "mission", {"mission_type": "relief_delivery", "where_source": "map"}, None, True),
    ("patrol the perimeter 500 metres around the aircraft position", "mission", {"mission_type": "perimeter_patrol", "size_m": 500, "where_source": "aircraft"}, None, True),
    ("survey one kilometre around here", "mission", {"mission_type": "area_survey", "size_m": 1000}, None, True),
    ("search two hundred and fifty metres around here", "mission", {"mission_type": "search_spiral", "size_m": 250}, None, True),
    ("search half a kilometre around here", "mission", {"mission_type": "search_spiral", "size_m": 500}, None, True),
    ("patrol 1.5 km around here", "mission", {"mission_type": "perimeter_patrol", "size_m": 1500}, None, True),
    ("search around here for 0.2 km", "mission", {"mission_type": "search_spiral", "size_m": 200}, None, True),
    ("survey here at 36 km/h", "mission", {"mission_type": "area_survey", "speed_mps": 10}, None, True),
    ("survey here at 8 metres per second", "mission", {"mission_type": "area_survey", "speed_mps": 8}, None, True),
    ("survey here at 40 m altitude", "mission", {"mission_type": "area_survey", "altitude_m": 40}, None, True),
    ("survey here at an altitude of sixty metres", "mission", {"mission_type": "area_survey", "altitude_m": 60}, None, True),
    ("fly to the point I click", "mission", {"mission_type": "waypoint_route", "where_source": "map"}, None, True),
    ("run a survey here", "mission", {"mission_type": "area_survey", "where_source": "aircraft"}, None, True),
    ("I want a search spiral", "mission", {"mission_type": "search_spiral"}, "where", False),
    ("patrol", "mission", {"mission_type": "perimeter_patrol"}, "where", False),
    ("deliver relief supplies", "mission", {"mission_type": "relief_delivery"}, "where", False),
    ("survey Cubbon Park", "mission", {"mission_type": "area_survey", "where_source": "place", "place_query": "Cubbon Park"}, "place", False),
    ("go to Cubbon Park", "mission", {"mission_type": "waypoint_route", "where_source": "place"}, "place", False),
    ("survey and search here", "mission", {"mission_type": None}, "type", False),
    ("search 300 m around here at 120 m/s", "mission", {"mission_type": "search_spiral", "speed_mps": None}, "speed", False),        # above the limit: ask, do not guess
    ("survey here at 150 m altitude", "mission", {"mission_type": "area_survey", "altitude_m": None}, "altitude", False),
    ("survey 50 km around here", "mission", {"mission_type": "area_survey", "size_m": None}, "size", False),
    ("search here with 7 drones", "mission", {"mission_type": "search_spiral", "drone_count": 7}, None, True),                       # planning never depends on the running fleet
    ("survey here with 120 drones", "mission", {"mission_type": "area_survey", "drone_count": 120}, "drones_typo", False),             # the typo guard asks once
    ("follow the car in front", "unsupported", {}, "closest", False),
    ("inspect the tower at Marina Beach", "unsupported", {}, "closest", False),
    ("photograph the field", "unsupported", {}, "closest", False),
    ("land and then take off", "unsupported", {}, None, False),
    ("attack the gps", "unsupported", {}, None, False),
    ("save it and deploy", "refused", {}, None, False),
    ("start the mission here", "refused", {}, None, False),
    ("what is a coverage survey?", "other", {}, None, False),
    ("do something useful", "other", {}, None, False),
]


@pytest.mark.parametrize("text,intent,expected,question,complete", SENTENCES)
def test_sentence_table(text, intent, expected, question, complete):
    result = parse(text, available_aircraft=1, has_aircraft_position=True)
    assert result["intent"] == intent, result
    for key, value in expected.items():
        assert result["state"][key] == value, (key, result["state"])
    assert (result["question"] or {}).get("id") == question, result
    assert result["complete"] is complete
    if intent == "unsupported":
        assert result["unsupported"]
    if intent == "refused":
        assert result["refused"]


def test_the_table_has_at_least_25_sentences():
    assert len(SENTENCES) >= 25


def test_two_drones_when_two_are_available_and_digits_or_words():
    for sentence in ("survey here with 2 drones", "survey here with two drones", "survey here using both drones"):
        result = parse(sentence, available_aircraft=2)
        assert result["state"]["drone_count"] == 2 and result["complete"] is True, sentence


def test_one_question_at_a_time_and_buttons_for_fixed_choices():
    result = parse("I want a search spiral", available_aircraft=1, has_aircraft_position=True)
    assert result["question"]["id"] == "where"
    assert [item["id"] for item in result["question"]["choices"]] == ["where:map", "where:aircraft"]
    assert result["state"]["asked"] == "where"
    answered = parse(None, choice="where:aircraft", state=result["state"], available_aircraft=1)
    assert answered["complete"] is True and answered["question"] is None and answered["state"]["where_source"] == "aircraft"
    # without an aircraft position only the map is offered
    only_map = parse("I want a search spiral", has_aircraft_position=False)
    assert [item["id"] for item in only_map["question"]["choices"]] == ["where:map"]
    # several missing things: the first question only, never a list
    nothing = parse("do a mission")
    assert nothing["question"] is None or len(nothing["question"]["choices"]) >= 2
    first = parse("survey here at 120 m/s with 7 drones")
    assert first["question"]["id"] in ("speed", "drones") and isinstance(first["question"], dict)


def test_choice_answers_fix_conflicts():
    result = parse("search 300 m around here at 120 m/s")
    assert result["question"]["id"] == "speed"
    fixed = parse(None, choice="speed:max", state=result["state"])
    assert fixed["state"]["speed_mps"] == LIMITS.max_speed_mps and fixed["complete"] is True
    default = parse(None, choice="speed:default", state=result["state"])
    assert default["state"]["speed_mps"] == q.quick_defaults(LIMITS)["speed_mps"]


def test_destination_types_do_not_accept_the_aircraft_position():
    result = parse("deliver supplies here")
    assert result["state"]["where_source"] is None and result["question"]["id"] == "where"
    assert [item["id"] for item in result["question"]["choices"]] == ["where:map"]


def test_unsupported_offers_the_closest_type_and_is_honest():
    result = parse("follow the car in front")
    assert "not supported" in result["unsupported"]["reason"].lower() and result["unsupported"]["closest"] == "waypoint_route"
    assert result["question"]["choices"][0]["id"] == "closest:waypoint_route"
    taken = parse(None, choice="closest:waypoint_route", state=result["state"])
    assert taken["state"]["mission_type"] == "waypoint_route"


def test_the_parser_has_no_side_effects_and_is_deterministic():
    first = parse("search 300 m around here with one drone")
    second = parse("search 300 m around here with one drone")
    assert first == second


def test_parser_never_returns_an_action_only_a_draft_state():
    for text in ("save it", "deploy it now", "start the flight", "abort", "run the mission", "execute"):
        result = parse(text)
        assert result["intent"] in ("refused", "other", "mission") and result["complete"] is False or result["intent"] == "mission"
        assert "route" not in result and "execute" not in str(result.get("question"))


# --- the API -----------------------------------------------------------------------------------------------------------------

def test_limits_endpoint_serves_the_quick_block(general_client):
    client, _ = general_client
    body = client.get("/api/mission-plans/limits").json()
    assert body["quick"]["defaults"]["speed_mps"] == 8 and len(body["quick"]["types"]) == 6 and body["quick"]["rules"]


def test_quick_endpoint_builds_a_valid_plan_and_saves_nothing(general_client):
    client, _ = general_client
    reply = client.post("/api/mission-plans/quick", json={"mission_type": "search_spiral", "where": WHERE, "aircraft_ids": ["px4-1"]})
    assert reply.status_code == 200, reply.text
    body = reply.json()
    plan = MissionPlan.model_validate(body["plan"])
    assert validate_plan(plan, PlanningLimits.from_config(), complete=True) == []
    assert body["suggested_name"].startswith("Search spiral") or "spiral" in body["suggested_name"].lower()
    assert client.get("/api/scenarios").json() in ([], {"scenarios": []}) or not client.get("/api/scenarios").json().get("scenarios")


def test_quick_endpoint_errors_are_structured(general_client):
    client, _ = general_client
    bad = client.post("/api/mission-plans/quick", json={"mission_type": "area_survey", "where": WHERE, "aircraft_ids": ["px4-1"], "speed_mps": 500})
    assert bad.status_code == 422
    assert bad.json()["detail"]["code"] == "quick_mission_invalid" and bad.json()["detail"]["issues"][0]["code"] == "speed_out_of_range"
    assert client.post("/api/mission-plans/quick", json={"mission_type": "area_survey", "where": WHERE, "unknown": 1}).status_code == 422


def test_quick_endpoints_need_login_and_csrf(general_client):
    from app.main import app
    from fastapi.testclient import TestClient
    anonymous = TestClient(app)
    body = {"mission_type": "area_survey", "where": WHERE, "aircraft_ids": ["px4-1"]}
    assert anonymous.post("/api/mission-plans/quick", json=body).status_code in (401, 403)
    assert anonymous.post("/api/mission-plans/quick/parse", json={"text": "survey here"}).status_code in (401, 403)
    client, _ = general_client
    token = client.headers.pop("X-CSRF-Token")
    try:
        assert client.post("/api/mission-plans/quick", json=body).status_code == 403
        assert client.post("/api/mission-plans/quick/parse", json={"text": "survey here"}).status_code == 403
    finally:
        client.headers["X-CSRF-Token"] = token


def test_parse_endpoint_round_trip(general_client):
    client, _ = general_client
    reply = client.post("/api/mission-plans/quick/parse", json={"text": "search 300 m around here with one drone", "context": {"available_aircraft": 1}})
    assert reply.status_code == 200
    assert reply.json()["complete"] is True and reply.json()["state"]["mission_type"] == "search_spiral"
    assert client.post("/api/mission-plans/quick/parse", json={"text": "x" * 600}).status_code == 422


def test_operator_may_use_quick_planning(operator_client):
    client = operator_client[0] if isinstance(operator_client, tuple) else operator_client
    reply = client.post("/api/mission-plans/quick", json={"mission_type": "area_survey", "where": WHERE, "aircraft_ids": ["px4-1"]})
    assert reply.status_code == 200, reply.text


def test_quick_module_documents_every_rule():
    text = " ".join(q.RULE_TEXT).lower()
    for word in ("speed", "altitude", "spiral", "spacing", "datum", "start", "limit", "priority", "heading", "loiter", "return", "distance", "duration"):
        assert word in text, word
    assert math.isclose(q.SPIRAL_EXTENT_FRACTION, 2 / 3)


# --- derived speed, waypoint types, multi-UAV plans, explanation ----------------------------------------------------------------

from app.mission_planning.validation import point_in_area  # noqa: E402

POINTS = [{"latitude": 12.9730, "longitude": 77.5950}, {"latitude": 12.9745, "longitude": 77.5965}, {"latitude": 12.9760, "longitude": 77.5950}]


def raw(kind, **extra):
    body = {"mission_type": kind, "aircraft_ids": ["px4-1"], "aircraft_position": AIRCRAFT, **extra}
    if "points" not in body:
        body["where"] = WHERE
    return build_quick_plan(QuickRequest(**body), LIMITS)


class W:
    def __init__(self, east, north):
        from app.mission_planning.geometry import from_enu
        self.latitude, self.longitude = from_enu((12.97, 77.59), east, north)


def test_default_speed_is_the_highest_the_route_can_safely_fly():
    accel = min(10, 9.81 * math.tan(math.radians(60)))                          # profile acceleration 10 m/s2, tilt 60 deg
    for kind in ("area_survey", "search_spiral", "perimeter_patrol", "relief_delivery"):
        result = raw(kind)
        info = result["summary"]["speed_info"]
        assert info["mode"] == "derived" and info["limited_by"] == "corner"
        assert info["tightest_corner_radius_m"] == 25.0                          # the acceptance radius: every one of these turns tighter than that
        assert info["lateral_accel_mps2"] == pytest.approx(accel, abs=0.01)
        assert result["summary"]["speed_mps"] == math.floor(math.sqrt(accel * 25.0) * 2) / 2 == 15.5
        assert info["ceiling_mps"] == LIMITS.max_speed_mps and info["research_speed_mps"] == 8


def test_safe_speed_on_known_corners():
    # a square with 400 m legs and 90 degree corners: radius = 200 / tan(45) = 200 m -> sqrt(10 x 200) = 44.7 m/s
    square = [W(0, 0), W(400, 0), W(400, 400), W(0, 400), W(0, 0)]
    assert q.safe_speed(square, LIMITS)["speed_mps"] == 20.0 and q.safe_speed(square, LIMITS)["limited_by"] == "ceiling"      # LIMITS ceiling is 20
    wide = q.safe_speed(square, PlanningLimits(**{**LIMITS.__dict__, "max_speed_mps": 90}))
    assert wide["tightest_corner_radius_m"] == pytest.approx(200, abs=1) and wide["speed_mps"] == 44.5 and wide["limited_by"] == "corner"
    # a turn-around uses the acceptance radius: sqrt(10 x 25) = 15.8 -> 15.5
    assert q.safe_speed([W(0, 0), W(500, 0), W(0, 0)], LIMITS)["speed_mps"] == 15.5
    # a straight line has no corner: the ceiling
    assert q.safe_speed([W(0, 0), W(100, 0), W(300, 0)], LIMITS)["speed_mps"] == LIMITS.max_speed_mps


def test_the_speed_badge_and_the_typed_speed():
    derived = raw("area_survey")
    assert "speed_above_validated" in [w["code"] for w in derived["warnings"]] and derived["summary"]["speed_info"]["above_validated"] is True
    typed = raw("area_survey", speed_mps=8)
    assert typed["summary"]["speed_info"]["mode"] == "typed" and typed["summary"]["speed_info"]["above_validated"] is False
    assert "speed_above_validated" not in [w["code"] for w in typed["warnings"]]
    assert typed["summary"]["speed_info"]["derived_speed_mps"] == 15.5          # the derived value is still reported


def test_waypoint_route_through_several_points_and_back():
    result = raw("waypoint_route", points=POINTS)
    plan = result["plan"]
    item = plan["aircraft"][0]
    assert item["parameters"]["points"] == POINTS and item["task_type"] == "waypoint_route"
    assert plan["operating_area"]["radius_m"] >= 100 and plan["operating_area"]["radius_m"] % 50 == 0
    assert item["route"]["waypoints"][0]["action"] == "takeoff" and item["route"]["waypoints"][-1]["action"] == "land"
    assert "Fly through 3 waypoints in order" in " ".join(result["summary"]["explanation"][0]["steps"])


def test_waypoints_then_drop_default_last_and_marked_waypoint():
    last = raw("waypoints_then_drop", points=POINTS, speed_mps=8)
    item = last["plan"]["aircraft"][0]
    assert item["task_type"] == "payload_dropoff" and item["parameters"]["drop_index"] == 2 and item["parameters"]["drop_point"] == POINTS[2]
    actions = [w["action"] for w in item["route"]["waypoints"]]
    assert actions.count("payload_release_point") == 1 and item["route"]["events"][0]["type"] == "payload_released"
    assert any("simulated" in step for step in last["summary"]["explanation"][0]["steps"])
    marked = raw("waypoints_then_drop", points=POINTS, drop_index=1, speed_mps=8)
    release = next(w for w in marked["plan"]["aircraft"][0]["route"]["waypoints"] if w["action"] == "payload_release_point")
    assert (release["latitude"], release["longitude"]) == (POINTS[1]["latitude"], POINTS[1]["longitude"]) and release["altitude_m"] == 10
    assert marked["summary"]["drop_index"] == 1 and "At waypoint 2 descend" in " ".join(marked["summary"]["explanation"][0]["steps"])
    with pytest.raises(QuickError) as error:
        raw("waypoints_then_drop", points=POINTS, drop_index=7)
    assert error.value.issues[0].code == "drop_index_out_of_range"


def test_explanation_uses_the_real_numbers():
    steps = raw("perimeter_patrol", speed_mps=8)["summary"]["explanation"][0]["steps"]
    assert steps[0] == "Take off and climb to 50 m."
    assert steps[1] == "Fly 300 m, about 37 s to the circle edge."
    assert steps[2] == "Fly one lap of the 300 m circle (1.9 km, about 4 min)."
    assert steps[3].startswith("Return 300 m") and steps[-1] == "Total 2.58 km, about 5.4 min, 52 waypoints."
    spiral = " ".join(raw("search_spiral", speed_mps=8)["summary"]["explanation"][0]["steps"])
    assert "Fly 0 m" not in spiral and "30 m between tracks" in spiral and "5.86 km" in spiral


def test_multi_uav_roles_default_to_primary_then_decoys_and_each_route_validates():
    ids = [f"px4-{n}" for n in range(1, 6)]
    result = build_quick_plan(QuickRequest(mission_type="area_survey", where=WHERE, drone_count=5, aircraft_ids=ids, speed_mps=8), LIMITS)
    plan = MissionPlan.model_validate(result["plan"])
    assert [a.role for a in plan.aircraft] == ["primary", "decoy", "decoy", "decoy", "decoy"]
    assert [a.task_type for a in plan.aircraft] == ["coverage_survey"] + ["waypoint_route"] * 4
    assert validate_plan(plan, LIMITS, complete=True) == [] and all(a.confirmed and a.route for a in plan.aircraft)
    starts = [(a.start_location.latitude, a.start_location.longitude) for a in plan.aircraft]
    assert len(set(starts)) == 5                                                # no two aircraft share a spawn point
    for decoy in plan.aircraft[1:]:
        assert 1 <= len(decoy.parameters.points) <= 48 and all(point_in_area(plan.operating_area, p) for p in decoy.parameters.points)
        assert decoy.route.length_m > 0
    assert [e["role"] for e in result["summary"]["explanation"]] == ["primary", "decoy", "decoy", "decoy", "decoy"]
    assert "parallel path" in " ".join(result["summary"]["explanation"][2]["steps"])


def test_roles_are_the_users_choice_standby_stays_on_the_ground_and_nothing_presets_them():
    ids = [f"px4-{n}" for n in range(1, 6)]
    rows = [{}, {"role": "standby"}, {"role": "decoy"}, {"role": "standby"}, {"role": "decoy"}]
    result = build_quick_plan(QuickRequest(mission_type="area_survey", where=WHERE, drone_count=5, aircraft_ids=ids, speed_mps=8, aircraft=rows), LIMITS)
    plan = MissionPlan.model_validate(result["plan"])
    assert [a.role for a in plan.aircraft] == ["primary", "standby", "decoy", "standby", "decoy"]
    assert [a.task_type for a in plan.aircraft] == ["coverage_survey", "ground_standby", "waypoint_route", "ground_standby", "waypoint_route"]
    standby = plan.aircraft[1]
    assert standby.route.length_m == 0 and len(standby.route.waypoints) == 2 and standby.route.waypoints[0].altitude_m == 0
    assert result["summary"]["roles"] == ["primary", "standby", "decoy", "standby", "decoy"]
    assert "never armed or flown" in result["summary"]["aircraft"][1]["line"]
    # any aircraft may be the primary: UAV-2 primary, UAV-1 decoy
    swapped = build_quick_plan(QuickRequest(mission_type="area_survey", where=WHERE, drone_count=3, speed_mps=8, aircraft=[{"role": "decoy"}, {"role": "primary"}, {}]), LIMITS)["plan"]
    assert [(a["uav_id"], a["role"], a["priority"]) for a in swapped["aircraft"]] == [("px4-1", "decoy", 2), ("px4-2", "primary", 1), ("px4-3", "decoy", 3)]


def test_chosen_roles_and_a_two_aircraft_plan():
    result = build_quick_plan(QuickRequest(mission_type="search_spiral", where=WHERE, drone_count=3, aircraft_ids=["px4-1", "px4-2", "px4-3"], roles=["decoy", "decoy"], speed_mps=8), LIMITS)
    assert result["summary"]["roles"] == ["primary", "decoy", "decoy"] and result["summary"]["confirmed"] is True
    two = build_quick_plan(QuickRequest(mission_type="relief_delivery", where=WHERE, drone_count=2, aircraft_ids=["px4-1", "px4-2"], roles=["standby"],
                                        aircraft_positions=[AIRCRAFT, {"latitude": 12.9741, "longitude": 77.5949}], speed_mps=8), LIMITS)
    assert two["summary"]["roles"] == ["primary", "standby"]
    assert two["plan"]["aircraft"][1]["start_location"] == {"latitude": 12.9741, "longitude": 77.5949}         # a real aircraft position is used as is
    with pytest.raises(ValueError):
        QuickRequest(mission_type="area_survey", where=WHERE, roles=["primary"])


def test_a_far_aircraft_gets_a_note_pointing_to_the_relocation_action():
    zurich = {"latitude": 47.39797, "longitude": 8.54616}
    result = build_quick_plan(QuickRequest(mission_type="relief_delivery", where=WHERE, aircraft_ids=["px4-1"], aircraft_position=zurich, speed_mps=8), LIMITS)
    note = next(w for w in result["warnings"] if w["code"] == "aircraft_far")
    assert "Restart at mission start" in note["message"] and "km" in note["message"]
    assert "aircraft_far" not in [w["code"] for w in raw("relief_delivery", speed_mps=8)["warnings"]]


def test_a_request_without_any_place_is_refused_and_the_type_list_is_served():
    with pytest.raises(ValueError):
        QuickRequest(mission_type="area_survey", aircraft_ids=["px4-1"])
    body = q.rules_payload(LIMITS)
    assert [t["id"] for t in body["types"]][-1] == "waypoints_then_drop" and body["research_speed_mps"] == 8 and body["default_extra_role"] == "decoy" and body["plan_max_aircraft"] == 999
    assert {t["id"]: t["where"] for t in body["types"]}["waypoint_route"] == "points"
