"""Live experiment runner (Part 3): Implement switch rules, injection into the monitor's copy, run records, export."""
from __future__ import annotations

import io
from datetime import datetime, timezone

import numpy as np
import pytest

from app.persistence.database import list_experiment_results
from app.research import live_experiments as live
from app.research.attacks import AttackConfig
from test_attack_lab import ATTACK, experiment, flight, saved_plan  # noqa: F401  (flight is a fixture)


@pytest.fixture(autouse=True)
def clean_runner():
    live.reset_for_tests()
    yield
    live.reset_for_tests()


def armed(attacks, seed=3, exp_id="e1"):
    return live.ArmedExperiment(exp_id, "Test", "owner", seed, "hash", [AttackConfig.model_validate(a) for a in attacks])


def start_run(experiments, execution_id="run1"):
    run = live.Run(execution_id, "dep", "ver", None, experiments, datetime.now(timezone.utc))
    live._runs[execution_id] = run
    return run


def window(start=0.0, count=120):
    t = start + np.arange(count) * 0.5
    position = np.column_stack([np.arange(count) * 1.0, np.zeros(count), np.full(count, 30.0)])
    velocity = np.column_stack([np.full(count, 2.0), np.zeros(count), np.zeros(count)])
    return t, position, velocity


def attack(**kw):
    return {"target_aircraft": "px4-1", "type": "offset", "start_s": 10, "duration_s": 20, "magnitude": 50, "direction_deg": 90, **kw}


def test_nothing_changes_without_a_run():
    t, p, v = window()
    out_p, out_v, active = live.apply_to_window("px4-1", t, p, v)
    assert out_p is p and out_v is v and active is False


def test_offset_moves_only_the_target_aircraft_and_only_inside_its_window():
    start_run([armed([attack()])])
    t, p, v = window()
    out_p, _out_v, active = live.apply_to_window("px4-1", t, p, v)
    inside = (t >= 10) & (t < 30)
    assert np.allclose(out_p[~inside], p[~inside])
    assert out_p[inside][-1, 0] - p[inside][-1, 0] == pytest.approx(50.0, abs=0.5)       # direction 90 = east, fully ramped in
    assert np.array_equal(p, window()[1])                                                # the caller's array is untouched
    other, _v2, other_active = live.apply_to_window("px4-2", *window()[:1], p, v)
    assert np.allclose(other, p) and other_active is False
    assert live.apply_to_window("px4-1", t[:15], p[:15], v[:15])[2] is False           # attack not started yet in this window
    assert active is False                                                                 # last sample (t=59.5) is after the attack ended


def test_active_flag_is_true_while_the_attack_runs():
    start_run([armed([attack()])])
    t, p, v = window(count=50)                                                             # t ends at 24.5: inside 10..30
    assert live.apply_to_window("px4-1", t, p, v)[2] is True


def test_freeze_holds_one_position_even_when_the_window_slides():
    start_run([armed([attack(type="freeze", magnitude=0, direction_deg=None)])])
    t, p, v = window(count=40)
    first, first_v, _ = live.apply_to_window("px4-1", t, p, v)
    inside = (t >= 10) & (t < 30)
    assert np.allclose(first[inside], first[inside][0]) and np.allclose(first_v[inside], 0.0)
    t2, p2, v2 = window(start=5.0, count=40)                                               # the window slid
    second, _sv, _ = live.apply_to_window("px4-1", t2, p2, v2)
    inside2 = (t2 >= 10) & (t2 < 30)
    assert np.allclose(second[inside2], first[inside][0])                                  # same frozen position as before


def test_noise_is_the_same_for_the_same_flight_time():
    start_run([armed([attack(type="noise", magnitude=15, direction_deg=None)])])
    t, p, v = window(count=60)
    a = live.apply_to_window("px4-1", t, p, v)[0]
    b = live.apply_to_window("px4-1", t[10:], p[10:], v[10:])[0]
    assert np.allclose(a[10:], b)                                                          # a sliding window sees the same noise per sample
    assert np.abs(a - p).max() > 1.0 and np.allclose(a[:, 2], p[:, 2])


def test_run_record_measures_latency_false_alarms_and_outcome():
    run = start_run([armed([attack(start_s=20, duration_s=30)])])
    for t in range(0, 120):                                                                # one assessment per second
        tier = "Medium" if 5 <= t < 7 else "High" if 24 <= t < 40 else "Low"              # a false alarm at 5 s, a reaction 4 s after start
        live.observe("px4-1", float(t), {"threat_score": 80.0 if tier == "High" else 30.0, "tier": tier}, 20 <= t < 50)
    run.reached_t["px4-1"] = 119.0
    record = live.build_record(run, run.experiments[0], "completed", datetime.now(timezone.utc))
    first = record["attacks"][0]
    assert first["outcome"] == "detected" and first["latency_s"] == 4.0 and first["first_alert_latency_s"] == 4.0
    assert record["false_alarms"] == 1 and record["outcome"] == "detected" and record["end_reason"] == "completed"
    assert record["timeline"]["px4-1"] and any("one flight is one sample" in text.lower() for text in record["caveats"])


def test_a_flight_that_ends_before_the_attack_is_not_counted_as_missed():
    run = start_run([armed([attack(start_s=300, duration_s=30)])])
    for t in range(0, 60):
        live.observe("px4-1", float(t), {"threat_score": 10.0, "tier": "Low"}, False)
    run.reached_t["px4-1"] = 59.0
    record = live.build_record(run, run.experiments[0], "aborted", datetime.now(timezone.utc))
    assert record["attacks"][0]["outcome"] == "not_reached" and record["outcome"] == "not_reached"
    assert any("never ran" in text for text in record["caveats"])


def test_a_quiet_monitor_is_a_missed_attack():
    run = start_run([armed([attack(start_s=10, duration_s=20)])])
    for t in range(0, 100):
        live.observe("px4-1", float(t), {"threat_score": 5.0, "tier": "Low"}, 10 <= t < 30)
    run.reached_t["px4-1"] = 99.0
    record = live.build_record(run, run.experiments[0], "completed", datetime.now(timezone.utc))
    assert record["outcome"] == "missed" and record["attacks"][0]["latency_s"] is None


# --- the API -------------------------------------------------------------------------------------------------

def make(client, **kw):
    scenario_id, version_id = saved_plan(client)
    created = client.post("/api/attack-lab/experiments", json=experiment(scenario_id, version_id, **kw))
    assert created.status_code == 201, created.text
    return scenario_id, version_id, created.json()


def test_implement_switch_blocks_edit_and_delete_until_it_is_off(general_client, flight):
    client, _ = general_client
    scenario_id, version_id, record = make(client)
    on = client.post(f"/api/attack-lab/experiments/{record['id']}/implement", json={"enabled": True})
    assert on.status_code == 200 and on.json()["implemented"] is True and on.json()["config_hash"] == record["config_hash"]
    assert client.get("/api/attack-lab/live").json()["armed"] == 0                         # no deployment yet: nothing armed for this viewer
    assert client.put(f"/api/attack-lab/experiments/{record['id']}", json=experiment(scenario_id, version_id, name="Other")).status_code == 409
    refused = client.delete(f"/api/attack-lab/experiments/{record['id']}")
    assert refused.status_code == 409 and refused.json()["detail"]["code"] == "implemented"
    off = client.post(f"/api/attack-lab/experiments/{record['id']}/implement", json={"enabled": False})
    assert off.json()["implemented"] is False and off.json()["status"] == "ready"
    assert client.delete(f"/api/attack-lab/experiments/{record['id']}").status_code == 204


def test_copy_of_an_implemented_experiment_is_a_plain_draft(general_client, flight):
    client, _ = general_client
    _s, _v, record = make(client)
    client.post(f"/api/attack-lab/experiments/{record['id']}/implement", json={"enabled": True})
    copy = client.post(f"/api/attack-lab/experiments/{record['id']}/copy")
    assert copy.status_code == 201 and copy.json()["status"] == "draft" and copy.json()["implemented"] is False


def test_overlapping_experiments_on_one_aircraft_are_refused_and_separate_ones_allowed(general_client, flight):
    client, _ = general_client
    scenario_id, version_id, first = make(client)
    assert client.post(f"/api/attack-lab/experiments/{first['id']}/implement", json={"enabled": True}).status_code == 200
    overlapping = client.post("/api/attack-lab/experiments", json=experiment(scenario_id, version_id, name="Overlap")).json()
    refused = client.post(f"/api/attack-lab/experiments/{overlapping['id']}/implement", json={"enabled": True})
    assert refused.status_code == 409 and refused.json()["detail"]["code"] == "overlapping_experiments"
    later = {**ATTACK, "start_s": 95, "duration_s": 5}
    separate = client.post("/api/attack-lab/experiments", json=experiment(scenario_id, version_id, name="Later", config={"attacks": [later]}))
    assert separate.status_code == 201, separate.text
    separate = separate.json()
    assert client.post(f"/api/attack-lab/experiments/{separate['id']}/implement", json={"enabled": True}).status_code == 200


def test_a_baseline_without_attacks_cannot_be_implemented(general_client, flight):
    client, _ = general_client
    _s, _v, record = make(client, config={"attacks": []})
    refused = client.post(f"/api/attack-lab/experiments/{record['id']}/implement", json={"enabled": True})
    assert refused.status_code == 409 and refused.json()["detail"]["code"] == "nothing_to_run"


def test_operator_cannot_switch_implement(general_client, operator_client, flight):
    general, _ = general_client
    _s, _v, record = make(general)
    operator, _ = operator_client
    assert operator.post(f"/api/attack-lab/experiments/{record['id']}/implement", json={"enabled": True}).status_code in (403, 404)


def test_closing_a_run_stores_one_record_per_experiment_and_the_export_lists_it(general_client, flight):
    client, principal = general_client
    _s, _v, record = make(client)
    client.post(f"/api/attack-lab/experiments/{record['id']}/implement", json={"enabled": True})
    experiments = live.armed_experiments(record["scenario_version_id"])
    assert [item.id for item in experiments] == [record["id"]]
    run = start_run(experiments, "exec-1")
    for t in range(0, 100):
        live.observe("px4-1", float(t), {"threat_score": 70.0 if 24 <= t < 50 else 5.0, "tier": "High" if 24 <= t < 50 else "Low"}, 20 <= t < 60)
    run.reached_t["px4-1"] = 99.0
    saved = live.close_run("exec-1", "completed")
    assert len(saved) == 1 and live.close_run("exec-1", "completed") == []                 # closing twice stores nothing more
    stored = client.get("/api/attack-lab/results").json()["results"]
    assert len(stored) == 1 and stored[0]["result"]["kind"] == "live_run" and stored[0]["result"]["attacks"][0]["outcome"] == "detected"
    exported = client.get("/api/attack-lab/results/export")
    assert exported.status_code == 200 and exported.headers["content-type"].startswith("application/vnd.openxmlformats")
    import zipfile
    book = zipfile.ZipFile(io.BytesIO(exported.content))
    workbook = book.read("xl/workbook.xml").decode()
    assert all(name in workbook for name in ("README", "Runs", "Attacks", "Operator responses", "Timeline"))
    assert "Drift test" in book.read("xl/sharedStrings.xml").decode()


def test_live_status_never_shows_the_schedule(general_client, flight):
    client, _ = general_client
    body = client.get("/api/attack-lab/live").json()
    assert set(body) == {"armed", "run_active", "experiments_in_run", "note"}


# --- the monitor really reads the injected copy ---------------------------------------------------------------

from test_live_threat import feed, live as live_store  # noqa: E402,F401  (fixture)
from app import research_routes  # noqa: E402


def test_the_live_monitor_reacts_to_an_implemented_attack_and_stays_quiet_without_one(live_store, model_dir):
    feed(live_store, 1, 120.0)
    quiet = research_routes.compute_live(live_store, {})["aircraft"][0]
    assert quiet["state"] == "live" and quiet["assessment"]["tier"] == "Low"
    research_routes.reset_live_threat()
    run = start_run([armed([attack(start_s=100.0, duration_s=60.0, magnitude=200.0)])])
    attacked = research_routes.compute_live(live_store, {})["aircraft"][0]
    assert attacked["assessment"]["tier"] != "Low" and attacked["assessment"]["threat_score"] > quiet["assessment"]["threat_score"]
    assert run.samples["px4-1"] and run.samples["px4-1"][-1][3] is True            # the run recorded that an attack was active
    assert "offset" not in str(attacked)                                            # the live payload carries no attack type or schedule


def test_an_already_alarming_monitor_must_escalate_to_count_as_a_reaction():
    run = start_run([armed([attack(start_s=40, duration_s=20)])])
    for t in range(0, 120):
        live.observe("px4-1", float(t), {"threat_score": 16.0, "tier": "Medium" if t >= 20 else "Low"}, 40 <= t < 60)
    run.reached_t["px4-1"] = 119.0
    record = live.build_record(run, run.experiments[0], "completed", datetime.now(timezone.utc))
    assert record["attacks"][0]["tier_before_attack"] == "Medium" and record["attacks"][0]["outcome"] == "unclear" and record["outcome"] == "unclear"
    assert any("already at Medium" in text for text in record["caveats"])


def test_reactions_inside_another_experiments_window_are_not_false_alarms():
    first, second = armed([attack(start_s=20, duration_s=10)], exp_id="a"), armed([attack(start_s=100, duration_s=10, type="jump")], exp_id="b")
    run = start_run([first, second])
    for t in range(0, 200):
        tier = "High" if 20 <= t < 40 or 100 <= t < 125 else "Low"
        live.observe("px4-1", float(t), {"threat_score": 60.0 if tier == "High" else 3.0, "tier": tier}, 20 <= t < 30 or 100 <= t < 110)
    run.reached_t["px4-1"] = 199.0
    assert live.build_record(run, first, "completed", datetime.now(timezone.utc))["false_alarms"] == 0
