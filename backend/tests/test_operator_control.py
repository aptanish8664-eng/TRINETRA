"""Part E: suggestion thresholds, the rejoin point, the remaining route a taker flies, taker readiness, and the story. No simulator is needed for these."""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from app import operator_control as oc
from app.mission_planning.generators import generate_route
from app.research import suggestions
from test_mission_planning import LIMITS, make_plan, offset


def _aircraft(points=((200, 0), (200, 200), (0, 200), (0, 400))):
    plan = make_plan("waypoint_route", parameters={"points": [offset(north, east) for north, east in points]})
    aircraft = plan.aircraft[0]
    return aircraft.model_copy(update={"route": generate_route(aircraft, plan, LIMITS)})


def test_thresholds_are_named_constants_with_a_plain_explanation_for_every_level():
    assert suggestions.SUGGEST_WATCH < suggestions.SUGGEST_ACT < suggestions.CONFIRMED_AT <= 1.0
    assert [suggestions.level_for(p) for p in (None, 0.0, 0.29, 0.30, 0.59, 0.60, 0.84, 0.85, 1.0)] == ["clear", "clear", "clear", "watch", "watch", "suspected", "suspected", "confirmed", "confirmed"]
    assert set(suggestions.EXPLANATION) == set(suggestions.LEVELS)
    assert suggestions.next_reminder(0.1) == suggestions.SUGGEST_WATCH and suggestions.next_reminder(0.4) == suggestions.SUGGEST_ACT and suggestions.next_reminder(0.9) is None


def test_below_the_lowest_threshold_nothing_is_suggested_above_it_only_a_button_with_a_reason():
    assert suggestions.suggestion("clear", "px4-1", 0.1, [], ["px4-2"]) is None
    watch = suggestions.suggestion("watch", "px4-1", 0.4, ["its GPS jumped"], ["px4-2"])
    assert watch["action"] == "continue_monitoring" and "GPS jumped" in watch["reason"] and "40%" in watch["reason"]
    act = suggestions.suggestion("suspected", "px4-1", 0.7, [], ["px4-3", "px4-2"])
    assert act["action"] == "reassign" and act["taker"] == "px4-3"                     # the best ready aircraft, never executed by itself
    assert suggestions.suggestion("suspected", "px4-1", 0.7, [], [])["action"] == "hold"
    assert suggestions.suggestion("confirmed", "px4-1", 0.9, [], [])["action"] == "return_to_launch"


def test_rejoin_is_the_aircrafts_own_next_waypoint_unless_a_later_one_is_clearly_closer():
    aircraft = _aircraft()
    waypoints = list(aircraft.route.waypoints)
    first = waypoints[1]
    nxt = oc.rejoin_point(waypoints, (first.latitude, first.longitude), 2)           # the executor says waypoint 2 (1-based) is next -> index 1
    assert nxt["index"] == 1 and nxt["distance_m"] < 1 and "own next waypoint" in nxt["why"] and nxt["waypoint_number"] == 2 and nxt["of"] == len(waypoints)
    near_later = waypoints[3]
    later = oc.rejoin_point(waypoints, (near_later.latitude, near_later.longitude), 2)
    assert later["index"] == 3 and "nearest of its next waypoints" in later["why"]
    assert oc.rejoin_point(waypoints, None, None)["index"] == 1
    # a route that loops back near its start must never make the aircraft skip most of its mission
    start = waypoints[0]
    skipping = oc.rejoin_point(waypoints, (start.latitude, start.longitude), 2)
    assert skipping["index"] <= 1 + oc.REJOIN_WINDOW
    paused = oc.rejoin_point(waypoints, (waypoints[3].latitude, waypoints[3].longitude), 2, own_next_only=True)
    assert paused["index"] == 1 and "already flying to" in paused["why"]       # a paused run continues to the waypoint it was already flying to                       # route not started and position unknown: the first waypoint after take-off
    last = oc.rejoin_point(waypoints, (waypoints[-1].latitude, waypoints[-1].longitude), len(waypoints))
    assert last["is_landing"] is True and last["index"] == len(waypoints) - 1


def test_the_remaining_route_starts_with_a_take_off_at_the_takers_position_and_ends_with_the_original_landing():
    aircraft = _aircraft()
    original = list(aircraft.route.waypoints)
    derived = oc.remaining_route(aircraft, 2, (12.0, 77.0), None)
    waypoints = list(derived.route.waypoints)
    assert [w.seq for w in waypoints] == list(range(len(waypoints)))
    assert waypoints[0].action == "takeoff" and (waypoints[0].latitude, waypoints[0].longitude) == (12.0, 77.0)
    assert [(w.latitude, w.longitude) for w in waypoints[1:]] == [(w.latitude, w.longitude) for w in original[2:]]
    assert waypoints[-1].action == "land" and len(waypoints) == len(original) - 2 + 1
    from app.mission_execution.items import build_mission
    assert build_mission(derived.model_copy(update={"uav_id": "px4-3"}), "a" * 32)["items"][0]["command"] == 22        # it really is a flyable mission (take-off first)
    airborne = oc.remaining_route(aircraft, 2, (12.0, 77.0), 40.0)
    assert airborne.route.waypoints[0].altitude_m == 40.0                             # an airborne taker keeps its altitude
    # the original's own take-off is never repeated, and a route that no longer ends in a landing is refused
    assert sum(w.action == "takeoff" for w in oc.remaining_route(aircraft, 0, (1.0, 2.0), None).route.waypoints) == 1
    broken = aircraft.model_copy(update={"route": aircraft.route.model_copy(update={"waypoints": original[:-1]})})
    with pytest.raises(HTTPException):
        oc.remaining_route(broken, 1, (1.0, 2.0), None)


def _vehicle(**over):
    base = {"id": "px4-2", "status": "connected", "gps_raw": {"valid": True, "fix_type": 3}, "battery": {"remaining_percent": 90}, "armed": False,
            "position": {"latitude": 12.9, "longitude": 77.5, "valid": True, "relative_altitude_m": 0.0}}
    base.update(over)
    return base


def test_taker_readiness_names_every_reason_and_a_good_aircraft_is_ready():
    assert oc.taker_problems(_vehicle(), None) == []
    assert oc.taker_problems(None, None) == ["no telemetry has been received from this aircraft"]
    bad = oc.taker_problems(_vehicle(status="stale", gps_raw={"valid": False}, battery={"remaining_percent": 5}), None)
    assert any("stale" in item for item in bad) and any("3D GPS" in item for item in bad) and any("battery" in item for item in bad)
    assert any("unknown" in item for item in oc.taker_problems(_vehicle(battery={}), None))
    assert any("valid position" in item for item in oc.taker_problems(_vehicle(position={"valid": False}), None))


def test_airborne_means_armed_and_above_two_metres():
    assert oc._is_airborne(_vehicle(armed=True, position={"relative_altitude_m": 30.0, "valid": True})) is True
    assert oc._is_airborne(_vehicle(armed=False, position={"relative_altitude_m": 30.0, "valid": True})) is False
    assert oc._is_airborne(_vehicle(armed=True, position={"relative_altitude_m": 0.5, "valid": True})) is False
    assert oc._is_airborne(None) is None


def test_the_story_is_ordered_bounded_and_isolated_per_deployment():
    oc.reset_for_tests()
    oc.story_add("d1", "level_watch", "first", aircraft="px4-1")
    oc.story_add("d1", "operator_reassign", "second", aircraft="px4-1", by="General")
    oc.story_add("d2", "level_watch", "other")
    assert [item["text"] for item in oc.story_for("d1")] == ["first", "second"] and len(oc.story_for("d2")) == 1
    for index in range(oc.STORY_MAX + 20):
        oc.story_add("d3", "x", str(index))
    assert len(oc.story_for("d3")) == oc.STORY_MAX and oc.story_for("d3")[-1]["text"] == str(oc.STORY_MAX + 19)
    oc.reset_for_tests()


def test_observe_writes_one_story_line_per_level_change_not_one_per_sample():
    oc.reset_for_tests()
    def row(p): return {"aircraft_id": "px4-2", "assessment": {"anomaly_confidence": p, "tier": "Low"}}
    for p in (0.1, 0.12, 0.35, 0.4, 0.45, 0.7, 0.72, 0.2):
        oc._observe("dep", [row(p)], "x")
    texts = [item["text"] for item in oc.story_for("dep")]
    assert len(texts) == 3 and "Watch" in texts[0] and "Suspected" in texts[1] and "rose" in texts[1] and "fell" in texts[2] and "Clear" in texts[2]
    oc.reset_for_tests()


def test_a_reassigned_route_is_remembered_only_for_the_run_it_was_made_for(monkeypatch):
    oc.reset_for_tests()
    aircraft = _aircraft()
    derived = oc.remaining_route(aircraft, 3, (1.0, 2.0), None).model_copy(update={"uav_id": "px4-3"})
    plan = type("Plan", (), {"aircraft": [aircraft.model_copy(update={"uav_id": "px4-3"})]})()
    oc._flown[("dep", "px4-3")] = {"run_id": "run-a", "aircraft": derived}
    monkeypatch.setattr(oc, "active_mission_execution", lambda uav: {"id": "run-a"})
    assert len(oc._own_route(plan, "px4-3", "dep").route.waypoints) == len(derived.route.waypoints)          # the route it is flying now
    monkeypatch.setattr(oc, "active_mission_execution", lambda uav: {"id": "run-b"})
    assert len(oc._own_route(plan, "px4-3", "dep").route.waypoints) == len(aircraft.route.waypoints)          # a later run: its own planned route again
    oc.reset_for_tests()


def test_the_mission_for_a_taker_in_the_air_carries_the_in_air_flag_only_when_asked():
    from app.mission_execution.items import build_mission
    aircraft = _aircraft().model_copy(update={"uav_id": "px4-2"})
    assert "in_air" not in build_mission(aircraft, "a" * 32)
    assert build_mission(aircraft, "a" * 32, in_air=True)["in_air"] is True


def test_a_reminder_is_checked_per_aircraft_and_fires_once(monkeypatch):
    from types import SimpleNamespace
    oc.reset_for_tests()
    deployment = {"id": "dep"}
    monkeypatch.setattr(oc, "_plan_and_roles", lambda context, dep: (deployment, None, None, {"px4-1": "primary", "px4-2": "decoy"}))
    rows = [{"aircraft_id": "px4-1", "assessment": {"anomaly_confidence": 0.05, "tier": "Low", "evidence": []}}, {"aircraft_id": "px4-2", "assessment": {"anomaly_confidence": 0.1, "tier": "Low", "evidence": []}}]
    monkeypatch.setattr(oc, "_live_rows", lambda context: rows)
    oc._acks[("dep", "px4-2")] = {"at": "t", "by": "x", "reminder_probability": 0.5, "probability_then": 0.1}
    context = SimpleNamespace(user=SimpleNamespace(full_name="General"))
    first = oc.threat_summary("dep", context)                                            # px4-2 is still below its reminder: nothing is raised
    assert first["raised_again"] is False and first["acknowledgements"][0]["aircraft_id"] == "px4-2"
    rows[1]["assessment"]["anomaly_confidence"] = 0.55
    second = oc.threat_summary("dep", context)                                           # px4-2 (not the first row) crosses ITS reminder
    assert second["raised_again"] is True and second["acknowledgements"] == [] and any(item["kind"] == "reminder_raised" and item["aircraft_id"] == "px4-2" for item in second["story"])
    assert oc.threat_summary("dep", context)["raised_again"] is False                    # once only
    oc.reset_for_tests()


def test_run_record_keeps_the_attack_story_of_its_flight():
    from datetime import datetime, timedelta, timezone
    from app import operator_control
    from app.research import live_experiments

    class _Run:  # only the fields the story filter reads
        deployment_id = "dep-story"
        started_at = datetime.now(timezone.utc) - timedelta(seconds=5)

    operator_control.story_add("dep-story", "threat_raised", "Probability rose to 62 %", aircraft="px4-1")
    story = live_experiments._attack_story(_Run(), datetime.now(timezone.utc) + timedelta(seconds=5))
    assert [item["text"] for item in story] == ["Probability rose to 62 %"]
    assert story[0]["aircraft"] == "px4-1"
    assert live_experiments._attack_story(_Run(), datetime.now(timezone.utc) - timedelta(seconds=30)) == []
