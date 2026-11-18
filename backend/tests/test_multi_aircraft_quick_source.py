"""Part 2: the quick source (with its per-aircraft rows) survives a save of a multi-aircraft plan and is dropped only when the plan was fine-tuned."""
from __future__ import annotations

import json

from test_mission_workflow_e2e import CONFIG

WHERE = {"latitude": 12.9716, "longitude": 77.5946}
ROWS = [{}, {"name": "Wing", "role": "decoy", "altitude_m": 65, "start_offset_m": 12}]


def test_a_two_aircraft_quick_mission_keeps_its_quick_source_and_rows_through_a_browser_like_round_trip(general_client):
    client, _ = general_client
    planned = client.post("/api/mission-plans/quick", json={"mission_type": "area_survey", "where": WHERE, "drone_count": 2, "aircraft": ROWS}).json()
    plan = json.loads(json.dumps(planned["plan"]))                     # what a browser sends back (65.0 becomes 65, and so on)
    source = {"mission_type": "area_survey", "where": WHERE, "points": [], "drop_index": None, "where_source": "map", "place_name": None, "size_m": None, "drone_count": 2,
              "roles": ["decoy"], "aircraft": [{"name": None, "role": None, "altitude_m": None, "speed_mps": None, "start_offset_m": None},
                                               {"name": "Wing", "role": "decoy", "altitude_m": 65, "speed_mps": None, "start_offset_m": 12}],
              "speed_mps": None, "altitude_m": None, "name": "Two aircraft", "fingerprint": planned["summary"]["fingerprint"]}
    saved = client.post("/api/scenarios", json={"title": "Two aircraft", "config": CONFIG, "intent": {"schema_version": 2, "mission_plan": plan, "uav_fault_assignments": [], "quick_source": source}})
    assert saved.status_code == 201, saved.text
    stored = saved.json()["versions"][0]["intent"]
    assert stored["quick_source"] is not None, "the quick source was dropped although the plan is exactly what Quick Mission made"
    assert stored["quick_source"]["aircraft"][1] == {"name": "Wing", "role": "decoy", "altitude_m": 65.0, "speed_mps": None, "start_offset_m": 12.0}
    assert stored["mission_plan"]["aircraft"][1]["callsign"] == "Wing" and "callsign" not in stored["mission_plan"]["aircraft"][0]
    # fine-tuned (another altitude for the decoy): the quick source is dropped, as before
    plan["aircraft"][1]["altitude_m"] = 66
    plan["aircraft"][1]["route"] = None
    plan["aircraft"][1]["confirmed"] = False
    plan["workflow_stage"] = "aircraft"
    tuned = client.put(f"/api/scenarios/{saved.json()['id']}", json={"title": "Two aircraft", "config": CONFIG, "intent": {"schema_version": 2, "mission_plan": plan, "uav_fault_assignments": [], "quick_source": source}})
    assert tuned.status_code == 200, tuned.text
    assert tuned.json()["versions"][0]["intent"].get("quick_source") is None


def test_a_point_on_the_zero_meridian_does_not_break_the_fingerprint(general_client):
    """Found in the browser check: a start rounded to -0.0 came back from the browser as 0, the fingerprint differed and the quick values were dropped."""
    from app.mission_planning.quick import plan_fingerprint
    client, _ = general_client
    on_meridian = {"latitude": 19.695207, "longitude": -1.9e-18}
    planned = client.post("/api/mission-plans/quick", json={"mission_type": "area_survey", "where": on_meridian, "drone_count": 2}).json()
    text = json.dumps(planned["plan"])
    # Generated route waypoints may still carry -0.0 (they are compared as numbers, where -0.0 == 0); the fingerprint must not care either way.
    as_browser = json.loads(text.replace("-0.0,", "0,").replace("-0.0}", "0}"))
    assert plan_fingerprint(as_browser) == planned["summary"]["fingerprint"]
    assert plan_fingerprint({"operating_area": {"kind": "circle", "centre": {"latitude": 1.0, "longitude": -0.0}}, "aircraft": []}) == \
        plan_fingerprint({"operating_area": {"kind": "circle", "centre": {"latitude": 1.0, "longitude": 0.0}}, "aircraft": []})
