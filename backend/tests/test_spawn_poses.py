"""Every aircraft, including standby, is spawned at its OWN planned start point, never closer than 3 m to another, with a warning in the plan when two starts are too close."""
from __future__ import annotations

import importlib.util
import math
from pathlib import Path

import pytest

from app.mission_planning.geometry import to_enu
from app.mission_planning.schemas import GeoPoint
from app.mission_planning.start import MIN_START_SEPARATION_M, aircraft_instance, mission_start, spawn_poses, start_separation_warnings
from app.mission_planning.validation import plan_warnings
from app.telemetry.fleet_manager import build_fleet_manager_command
from test_mission_planning import LIMITS, make_plan, offset

_spec = importlib.util.spec_from_file_location("wsl_fleet_manager_spawn", Path(__file__).parents[1] / "tools" / "wsl_fleet_manager.py")
manager = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(manager)


def plan_with(starts, roles=None):
    """A plan whose aircraft px4-1..px4-N have the given start offsets (north_m, east_m) or None (no start and no route)."""
    base = make_plan("waypoint_route")
    roles = roles or ["primary"] + ["decoy"] * 2 + ["standby"] * (len(starts) - 3 if len(starts) > 3 else 0)
    aircraft = []
    for index, start in enumerate(starts):
        update = {"uav_id": f"px4-{index + 1}", "role": roles[index] if index < len(roles) else "standby", "route": None, "confirmed": False}
        update["start_location"] = GeoPoint(**offset(*start)) if start is not None else None
        aircraft.append(base.aircraft[0].model_copy(update=update))
    ids = [item.uav_id for item in aircraft]
    return base.model_copy(update={"aircraft": aircraft, "selected_uav_ids": ids, "activated_uav_ids": ids, "required_uav_count": len(ids)})


def separation(poses):
    return min((math.hypot(a["east_m"] - b["east_m"], a["north_m"] - b["north_m"]) for i, a in enumerate(poses) for b in poses[i + 1:]), default=1e9)


def test_each_aircraft_including_standby_is_spawned_at_its_own_planned_start():
    plan = plan_with([(0, 0), (40, 15), (-25, 60), (100, -30), (0, 90)])      # (north, east) metres from the primary's start
    home = mission_start(plan)
    poses = {item["uav_id"]: item for item in home["spawn"]}
    assert set(poses) == {f"px4-{n}" for n in range(1, 6)}                      # all five, standby ones too
    assert (poses["px4-1"]["east_m"], poses["px4-1"]["north_m"]) == (0.0, 0.0)  # the primary's start is the home itself
    for uav_id, (north, east) in zip(poses, [(0, 0), (40, 15), (-25, 60), (100, -30), (0, 90)]):
        assert poses[uav_id]["east_m"] == pytest.approx(east, abs=0.05) and poses[uav_id]["north_m"] == pytest.approx(north, abs=0.05)
        assert poses[uav_id]["source"] == "start_location" and not poses[uav_id]["adjusted"]
    assert home["spawn_warnings"] == [] and separation(home["spawn"]) > 20


def test_starts_closer_than_the_minimum_are_moved_apart_and_the_warning_names_both_aircraft():
    plan = plan_with([(0, 0), (1.0, 0.5), (0, 0), (0.5, 0), (2.0, 2.0)])       # everything piled in the same few metres
    home = mission_start(plan)
    assert separation(home["spawn"]) >= MIN_START_SEPARATION_M - 1e-6
    assert sum(item["adjusted"] for item in home["spawn"]) == 4 and not home["spawn"][0]["adjusted"]    # the first aircraft keeps its place
    assert len(home["spawn_warnings"]) == 4 and "px4-2 starts only" in home["spawn_warnings"][0] and "px4-1" in home["spawn_warnings"][0]
    assert all("at least 3 m apart" in text for text in home["spawn_warnings"])


def test_the_old_behaviour_everything_in_one_place_cannot_happen_for_identical_starts():
    plan = plan_with([(0, 0)] * 5)
    poses = mission_start(plan)["spawn"]
    assert separation(poses) >= MIN_START_SEPARATION_M - 1e-6 and len({(p["east_m"], p["north_m"]) for p in poses}) == 5


def test_an_aircraft_without_a_start_or_route_is_placed_on_a_line_north_of_the_primary_and_apart_from_the_others():
    plan = plan_with([(0, 0), None, None, (3.0, 0)])
    poses = {item["uav_id"]: item for item in mission_start(plan)["spawn"]}
    assert poses["px4-2"]["source"] == "fallback_line" and poses["px4-3"]["source"] == "fallback_line"
    assert separation(list(poses.values())) >= MIN_START_SEPARATION_M - 1e-6


def test_the_plan_warns_before_anything_runs_when_two_starts_are_too_close():
    plan = plan_with([(0, 0), (1.0, 1.0), (50, 50)])
    assert set(start_separation_warnings(plan)) == {"px4-2"}
    codes = [issue.code for issue in plan_warnings(plan.aircraft[1], plan, LIMITS, None)]
    assert "start_too_close" in codes
    assert "start_too_close" not in [issue.code for issue in plan_warnings(plan.aircraft[2], plan, LIMITS, None)]


def test_instance_numbers_follow_the_aircraft_id():
    assert aircraft_instance("px4-1") == 0 and aircraft_instance("px4-12") == 11 and aircraft_instance("uav-1") is None


def test_the_command_carries_fixed_spawn_flags_and_refuses_bad_ones():
    home = {"latitude": 47.0, "longitude": 8.0, "altitude_m": 488.0, "spawn": [{"instance": 0, "east_m": 0.0, "north_m": 0.0}, {"instance": 3, "east_m": -12.345, "north_m": 80.0}]}
    command = build_fleet_manager_command("start", count=4, home=home)
    assert "--spawn-pose=0:0.00:0.00" in command and "--spawn-pose=3:-12.35:80.00" in command
    for bad in ([{"instance": 0, "east_m": 0.0, "north_m": 0.0}, {"instance": 0, "east_m": 1.0, "north_m": 1.0}],
                [{"instance": 99, "east_m": 0.0, "north_m": 0.0}], [{"instance": 1, "east_m": float("inf"), "north_m": 0.0}], [{"instance": 1, "east_m": "5", "north_m": 0.0}]):
        with pytest.raises(ValueError):
            build_fleet_manager_command("start", count=4, home={**home, "spawn": bad})


def test_the_wsl_manager_spawns_each_instance_where_it_was_told_and_falls_back_to_the_old_line_only_without_a_plan():
    manager.SPAWN_POSES.clear()
    assert manager.spawn_pose_text(2) == "0.00,6.00,0,0,0,0"                   # fallback: the old fixed line
    manager.SPAWN_POSES.update({2: manager.parse_spawn_pose("2:120.5:-33.25")[1:]})
    assert manager.spawn_pose_text(2) == "120.50,-33.25,0,0,0,0"
    for bad in ("2:1", "99:0:0", "1:nan:0", "1:2000000:0", "a:0:0"):
        with pytest.raises(ValueError):
            manager.parse_spawn_pose(bad)
    manager.SPAWN_POSES.clear()


def test_the_spawn_offsets_use_the_same_projection_as_the_rest_of_the_planner():
    plan = plan_with([(0, 0), (500, 700)])
    home = mission_start(plan)
    far = plan.aircraft[1].start_location
    east, north, _ = to_enu((home["latitude"], home["longitude"]), far.latitude, far.longitude)
    assert home["spawn"][1]["east_m"] == pytest.approx(east, abs=0.01) and home["spawn"][1]["north_m"] == pytest.approx(north, abs=0.01)


def test_each_aircraft_is_judged_against_its_own_start_not_the_primarys():
    from app.fleet_routes import simulation_location
    plan = plan_with([(0, 0), (60, 0), (-45, 35)])
    vehicles = []
    for aircraft in plan.aircraft:                                   # every aircraft sits exactly at its own planned start
        vehicles.append({"id": aircraft.uav_id, "system_id": int(aircraft.uav_id.split("-")[1]), "status": "connected",
                         "position": {"latitude": aircraft.start_location.latitude, "longitude": aircraft.start_location.longitude, "valid": True}})
    location = simulation_location(plan, vehicles)
    assert location["max_distance_to_start_m"] == pytest.approx(0.0, abs=0.2)           # before: up to the 60 m the others are from the primary
    assert location["relocation_recommended"] is False
    assert all(row["planned_start"] is not None for row in location["aircraft"])
    vehicles[1]["position"]["latitude"] += 0.0009                                         # px4-2 is ~100 m from ITS start: only it is far
    far = {row["aircraft_id"]: row["distance_to_start_m"] for row in simulation_location(plan, vehicles)["aircraft"]}
    assert far["px4-1"] < 0.5 and far["px4-3"] < 0.5 and far["px4-2"] > 90
