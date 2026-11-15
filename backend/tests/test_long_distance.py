"""Long-distance flights (2026-11-14): geodesic lengths, flat-plane refusals and the new default limits."""
from __future__ import annotations

import math

import pytest

from app.mission_planning import generators as gen
from app.mission_planning.generators import PlanningLimits, RouteError, generate_route
from app.mission_planning.schemas import MissionPlan
from app.mission_planning.validation import validate_aircraft
from test_mission_planning import ORIGIN, make_plan, offset, route_of

BENGALURU = (12.9716, 77.5946)
CHENNAI = (13.0827, 80.2707)
START = {"latitude": BENGALURU[0], "longitude": BENGALURU[1]}
BIG = {"max_distance_from_start_m": 1_000_000, "max_altitude_m": 100}


def long_route(speed=50.0, return_mode="land_at_destination", points=None, area=None) -> MissionPlan:
    points = points or [{"latitude": CHENNAI[0], "longitude": CHENNAI[1]}]
    return make_plan("waypoint_route", {"points": points}, objectives=["custom_mission"],
                     aircraft_extra={"speed_mps": speed, "return_mode": return_mode, "start_location": START},
                     plan_extra={"operating_area": area or {"kind": "circle", "centre": {"latitude": 13.03, "longitude": 78.9}, "radius_m": 200_000},
                                 "constraints": BIG})


def issues_of(plan: MissionPlan, **kwargs) -> list[str]:
    return [item.code for item in validate_aircraft(plan.aircraft[0], plan, PlanningLimits.from_config(), require_route=kwargs.get("require_route", False))[0]]


def test_geodesic_distance_matches_the_known_ellipsoid_values():
    from app.mission_planning.geometry import distance_m, geodesic_m

    assert abs(geodesic_m(BENGALURU, CHENNAI) - 290_543) < 5          # WGS84 inverse problem, Bengaluru to Chennai
    assert abs(geodesic_m((0, 0), (0, 1)) - 111_319.5) < 1            # one degree of longitude at the equator
    assert abs(geodesic_m((0, 0), (1, 0)) - 110_574.4) < 1            # one degree of latitude at the equator
    # Vincenty's own published test line (Flinders Peak to Buninyong): 54,972.271 m
    assert abs(geodesic_m((-37.951033417, 144.424867889), (-37.652821139, 143.926495528)) - 54_972.271) < 0.001
    assert 16_900_000 < geodesic_m((52.2, 0.1), (-33.9, 151.2)) < 17_000_000                      # a very long line still converges
    assert geodesic_m(BENGALURU, BENGALURU) == 0.0 and distance_m(BENGALURU, CHENNAI) == geodesic_m(BENGALURU, CHENNAI)
    assert abs(geodesic_m((12.0, 77.0), (12.0, 77.0 + 1e-6)) - 0.109) < 0.01


def test_bengaluru_to_chennai_uses_geodesic_length_and_the_points_own_coordinates():
    plan = long_route()
    route = route_of(plan)
    great_circle = gen.geodesic_m(BENGALURU, CHENNAI)
    assert [w.action for w in route.waypoints] == ["takeoff", "waypoint", "land"]
    assert (route.waypoints[1].latitude, route.waypoints[1].longitude) == CHENNAI      # exact, not a flat-plane round trip
    assert (route.waypoints[0].latitude, route.waypoints[0].longitude) == BENGALURU
    assert abs(route.length_m - great_circle) / great_circle < 0.01                    # within 1% of the great-circle distance
    assert abs(route.length_m - great_circle) < 100                                    # the climb and the descent add the rest
    assert abs(route.duration_s - route.length_m / 50.0) < 0.5                         # duration is length over the planned speed
    assert route.assumptions.startswith("Waypoints are the points you picked")
    # Measured: placing Chennai through the flat local plane (what routes did before) lands 301 m from Chennai and the leg is 100 m short.
    east, north, _ = gen.to_enu(BENGALURU, CHENNAI[0], CHENNAI[1])
    planar_point = gen.from_enu(BENGALURU, east, north)
    assert gen.geodesic_m(planar_point, CHENNAI) > 250 and abs(math.hypot(east, north) - great_circle) > 80


def test_bengaluru_to_chennai_validates_with_the_new_limits_and_out_and_back_is_allowed():
    one_way = long_route(speed=50.0)
    confirmed = one_way.model_copy(update={"aircraft": [one_way.aircraft[0].model_copy(update={"route": route_of(one_way), "confirmed": True})]})
    issues, route = validate_aircraft(confirmed.aircraft[0], confirmed, PlanningLimits.from_config(), require_route=True)
    assert [item.code for item in issues] == [] and route is not None
    assert route.duration_s < 300 * 60 and route.length_m < 1_000_000
    both = long_route(speed=50.0, return_mode="return_to_start")
    both_route = route_of(both)
    assert abs(both_route.length_m - 2 * gen.geodesic_m(BENGALURU, CHENNAI)) < 200    # about 581 km
    assert both_route.length_m < 1_000_000 and both_route.duration_s / 60 < 300        # 581 km at 50 m/s is about 194 min
    assert issues_of(both) == []
    assert "endurance_time_exceeded" in issues_of(long_route(speed=20.0, return_mode="return_to_start"))   # about 484 min: over 300


def test_a_short_point_route_keeps_the_flat_plane_method_so_stored_routes_still_verify():
    plan = make_plan("waypoint_route", {"points": [offset(200, 0), offset(200, 200)]})
    assert route_of(plan).assumptions == gen.FLAT_EARTH_ASSUMPTION
    assert gen.geodesic_reach_m(plan.aircraft[0], ORIGIN) < PlanningLimits.from_config().geodesic_above_m


def test_geodesic_mode_covers_every_point_task_and_the_return_leg():
    far = {"latitude": CHENNAI[0], "longitude": CHENNAI[1]}
    inspect = make_plan("point_inspection", {"points": [far], "orbit_radius_m": 30, "orbit_points": 8, "hover_s": 5}, objectives=["inspection"],
                        aircraft_extra={"speed_mps": 40, "start_location": START}, plan_extra={"constraints": BIG})
    ring = route_of(inspect)
    assert ring.assumptions.startswith("Waypoints are the points you picked")
    assert all(abs(gen.geodesic_m(CHENNAI, (w.latitude, w.longitude)) - 30) < 0.5 for w in ring.waypoints[1:9])   # a true 30 m ring
    assert (ring.waypoints[-1].latitude, ring.waypoints[-1].longitude) == BENGALURU and ring.waypoints[-1].action == "land"
    loiter = make_plan("loiter_hold", {"hold_point": far}, aircraft_extra={"loiter_duration_s": 60, "start_location": START}, plan_extra={"constraints": BIG})
    held = route_of(loiter)
    assert (held.waypoints[1].latitude, held.waypoints[1].longitude) == CHENNAI and held.waypoints[1].hover_s == 60
    drop = make_plan("payload_dropoff", {"drop_point": far, "drop_altitude_m": 8, "hover_duration_s": 20, "payload_category": "medical_kit"},
                     objectives=["relief_supply_delivery"], aircraft_extra={"start_location": START, "speed_mps": 40}, plan_extra={"constraints": BIG})
    delivery = route_of(drop)
    assert [w.action for w in delivery.waypoints][:3] == ["takeoff", "waypoint", "payload_release_point"]
    assert delivery.events and delivery.events[0].payload_category == "medical_kit"


@pytest.mark.parametrize("task, parameters, objectives", [
    ("coverage_survey", {"line_spacing_m": 500, "heading_deg": 0}, ["area_survey"]),
    ("expanding_square_search", {"datum": {"latitude": 13.5, "longitude": 78.5}, "track_spacing_m": 100, "search_radius_m": 1000}, ["search_rescue"]),
    ("sector_search", {"datum": {"latitude": 13.5, "longitude": 78.5}, "sector_radius_m": 1000}, ["search_rescue"]),
    ("perimeter_patrol", {}, ["perimeter_monitoring"]),
])
def test_area_patterns_beyond_the_flat_plane_range_are_refused_with_the_waypoint_route_advice(task, parameters, objectives):
    plan = make_plan(task, parameters, objectives=objectives, aircraft_extra={"start_location": START}, plan_extra={
        "operating_area": {"kind": "circle", "centre": {"latitude": 13.4, "longitude": 78.5}, "radius_m": 5000}, "constraints": BIG})
    issues, route = validate_aircraft(plan.aircraft[0], plan, PlanningLimits.from_config(), require_route=False)
    assert route is None and issues
    texts = " ".join(item.message for item in issues)
    assert "Use a waypoint route for long legs." in texts and "flat local plane" in texts
    assert {item.code for item in issues} & {"start_too_far_from_area", "planar_range_exceeded"}
    with pytest.raises(RouteError) as error:
        generate_route(plan.aircraft[0], plan, PlanningLimits.from_config())
    assert error.value.code == "planar_range_exceeded" and "Use a waypoint route for long legs." in error.value.message


def test_a_wide_area_near_the_start_is_refused_but_a_small_pattern_still_works():
    wide = make_plan("coverage_survey", {"line_spacing_m": 1000, "heading_deg": 0}, objectives=["area_survey"], plan_extra={
        "operating_area": {"kind": "circle", "centre": offset(0, 0), "radius_m": 60_000}, "constraints": BIG})
    with pytest.raises(RouteError) as error:
        generate_route(wide.aircraft[0], wide, PlanningLimits.from_config())
    assert error.value.code == "planar_range_exceeded" and "60.0 km" in error.value.message
    small = make_plan("coverage_survey", {"line_spacing_m": 100, "heading_deg": 0}, objectives=["area_survey"], plan_extra={
        "operating_area": {"kind": "circle", "centre": offset(300, 0), "radius_m": 500}, "constraints": {"max_distance_from_start_m": 5000, "max_altitude_m": 100}})
    assert generate_route(small.aircraft[0], small, PlanningLimits.from_config()).waypoints


def test_the_new_default_limits_are_in_code_and_served_by_the_limits_endpoint(general_client):
    from app import config

    limits = PlanningLimits.from_config()
    assert (limits.max_flight_s, limits.max_flight_distance_m, limits.max_start_to_area_m, limits.max_waypoints) == (18000.0, 1_000_000.0, 1_000_000.0, 500)
    assert (limits.max_speed_mps, limits.min_speed_mps, limits.max_altitude_m) == (float(config.MP_MAX_SPEED_MPS), 1.0, 120.0)
    assert limits.max_planar_extent_m == 20_000.0 and limits.geodesic_above_m == 20_000.0
    client, _ = general_client
    served = client.get("/api/mission-plans/limits").json()["limits"]
    for key in ("max_flight_s", "max_flight_distance_m", "max_start_to_area_m", "max_waypoints", "max_speed_mps", "min_speed_mps", "max_altitude_m", "max_planar_extent_m"):
        assert served[key] == getattr(limits, key)


def test_a_route_just_inside_1000_km_is_accepted_and_one_just_beyond_it_is_not():
    import dataclasses
    limits = dataclasses.replace(PlanningLimits.from_config(), max_flight_s=1_000_000.0)   # take the 300 minute time limit out of the picture
    area = {"kind": "circle", "centre": {"latitude": 17.5, "longitude": 77.5946}, "radius_m": 600_000}
    inside = long_route(speed=50.0, points=[{"latitude": 12.9716 + 8.9, "longitude": 77.5946}], area=area)         # about 985 km
    beyond = long_route(speed=50.0, points=[{"latitude": 12.9716 + 9.2, "longitude": 77.5946}], area=area)         # about 1,017 km
    assert gen.geodesic_m(BENGALURU, (12.9716 + 8.9, 77.5946)) < 1_000_000 < gen.geodesic_m(BENGALURU, (12.9716 + 9.2, 77.5946))
    codes_of = lambda plan: [i.code for i in validate_aircraft(plan.aircraft[0], plan, limits, require_route=False)[0]]
    assert codes_of(inside) == []
    assert {"endurance_distance_exceeded", "route_too_far"} <= set(codes_of(beyond))
