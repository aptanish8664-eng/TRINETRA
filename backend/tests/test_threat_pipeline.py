"""Offline GPS-inconsistency pipeline, threat engine and research API (synthetic data only)."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from app.research import threat_engine as engine
from app.research.attacks import AttackConfig, assert_no_ground_truth
from app.research.threat_data import FEATURE_COLUMNS, HZ, compute_features, generate_dataset, scenario_cells, simulate_flight
from app.research.threat_models import ThreatModel, train_pipeline


@pytest.fixture(scope="session")
def trained(tmp_path_factory):
    root = tmp_path_factory.mktemp("threat")
    manifest = generate_dataset(root / "dataset", seed=11, flights_per_cell=8)
    metrics = train_pipeline(root / "dataset", root / "artifacts", seed=11)
    return {"root": root, "manifest": manifest, "metrics": metrics, "artifacts": root / "artifacts", "dataset": root / "dataset"}


@pytest.fixture
def model_dir(trained, monkeypatch):
    from app import research_routes
    monkeypatch.setattr(research_routes, "model_dir", trained["artifacts"])
    return trained["artifacts"]


# --- data design ---------------------------------------------------------------------------------------------

def test_splits_are_by_flight_and_every_split_sees_every_scenario(trained):
    flights = pd.read_csv(trained["dataset"] / "flights.csv")
    sets = {name: set(flights[flights.split == name].flight_id) for name in ("train", "val", "test")}
    assert not (sets["train"] & sets["val"]) and not (sets["train"] & sets["test"]) and not (sets["val"] & sets["test"])
    assert flights.seed.is_unique
    cells = {cell["cell"] for cell in scenario_cells()}
    for name in ("train", "val", "test"):
        assert set(flights[flights.split == name].cell) == cells
    assert "normal" in cells and any(c.startswith("hard:") for c in cells) and {c.split(":")[0] for c in cells if ":" in c and not c.startswith("hard")} == {"offset", "jump", "drift", "freeze", "noise", "velocity_inconsistency"}
    manifest = trained["manifest"]
    assert manifest["config_hash"] and manifest["splits"]["train"]["rows"] > manifest["splits"]["test"]["rows"] > 0
    assert (trained["dataset"] / "labels_HIDDEN.csv").exists()


def test_features_never_contain_ground_truth(trained):
    features = pd.read_csv(trained["dataset"] / "features.csv")
    assert_no_ground_truth([column for column in features.columns if column in FEATURE_COLUMNS])
    for forbidden in ("is_attacked", "attack_type", "label"):
        assert forbidden not in features.columns
    flight = simulate_flight("T", 5, [AttackConfig(type="drift", start_s=40, duration_s=30, magnitude=1.0)])
    aircraft = flight.aircraft[0]
    frame = compute_features(aircraft.t, aircraft.gps.position, aircraft.gps.velocity, aircraft.imu_velocity)
    assert list(frame.columns) == FEATURE_COLUMNS
    # The attacked and an attack-free copy differ in the features only through the sensors, never through a flag.
    clean = simulate_flight("T", 5, [])
    assert aircraft.truth.is_attacked.any() and not clean.aircraft[0].truth.is_attacked.any()


def test_generation_is_reproducible(tmp_path):
    first = generate_dataset(tmp_path / "a", seed=3, flights_per_cell=1)
    second = generate_dataset(tmp_path / "b", seed=3, flights_per_cell=1)
    assert first["config_hash"] == second["config_hash"]
    assert pd.read_csv(tmp_path / "a" / "features.csv").equals(pd.read_csv(tmp_path / "b" / "features.csv"))


# --- models, calibration, tiers ---------------------------------------------------------------------------------

def test_models_are_evaluated_on_the_untouched_test_split(trained):
    metrics = trained["metrics"]
    gb = metrics["models"]["hist_gradient_boosting"]["test"]
    base = metrics["models"]["baseline_cusum"]["test"]
    for name in ("precision", "recall", "f1", "roc_auc", "false_alarms_per_hour", "detection_rate", "latency_median_s"):
        assert name in gb and name in base
    assert gb["roc_auc"] > 0.7 and gb["roc_auc"] > base["roc_auc"]
    assert set(gb["by_attack_type"]) >= {"offset", "jump", "drift", "freeze", "noise", "velocity_inconsistency"}
    assert set(gb["by_magnitude_tier"]) >= {"low", "mid", "high", "hard"}
    assert metrics["models"]["lstm_next_state"]["status"] in {"skipped", "available_but_not_trained"}
    assert "split by flight (asserted)" in metrics["leakage_controls"]


def test_calibration_is_monotone_and_not_worse_than_raw(trained):
    metrics = trained["metrics"]["calibration"]
    assert metrics["brier_test_calibrated"] <= metrics["brier_test_raw"] + 0.01
    assert metrics["reliability_test"] and metrics["reliability_rank_correlation"] >= 0.8
    assert metrics["max_abs_gap_bins_50plus_samples"] <= 0.2
    model = ThreatModel(trained["artifacts"])
    grid = np.linspace(0, 1, 50)
    assert np.all(np.diff(model.bundle["final_iso"].predict(grid)) >= -1e-12)


def test_tier_thresholds_are_ordered_and_keep_normal_flights_mostly_low(trained):
    tiers = trained["metrics"]["tiers"]
    t = tiers["thresholds"]
    assert 0 < t["medium"] < t["high"] < t["critical"] <= 100
    assert tiers["validation_distribution"]["normal"]["Low"] >= 0.97
    assert tiers["validation_distribution"]["attacked"]["Low"] < tiers["validation_distribution"]["normal"]["Low"]
    thresholds = engine.TierThresholds(**t)
    assert [thresholds.tier(s) for s in (0, t["medium"], t["high"], t["critical"])] == ["Low", "Medium", "High", "Critical"]


def test_tampered_artifacts_are_refused(trained, tmp_path):
    import shutil
    copy = tmp_path / "copy"
    shutil.copytree(trained["artifacts"], copy)
    with open(copy / "model.joblib", "ab") as handle:
        handle.write(b"x")
    with pytest.raises(ValueError, match="hash mismatch"):
        ThreatModel(copy)


# --- engine ----------------------------------------------------------------------------------------------------

def test_three_outputs_and_threat_score_formula():
    assert engine.threat_score(1.0, 1.0, 1.0) == pytest.approx(100.0)
    assert engine.threat_score(0.0, 1.0, 1.0) == 0.0
    assert engine.threat_score(0.5, 0.4, 0.6) == pytest.approx(100 * 0.5 * (0.65 * 0.4 + 0.35 * 0.6))
    primary = engine.mission_impact("primary", 50, 0.5)
    assert primary > engine.mission_impact("decoy", 50, 0.5)
    assert engine.mission_impact("primary", 20, 0.5) > engine.mission_impact("primary", 90, 0.5)
    assert engine.severity(100, 10, 10) > engine.severity(1, 0, 0)


def test_evidence_uses_only_features_and_is_ranked_by_excess():
    reference = {"res_pos_w10": 4.0, "res_vel": 1.0, "res_rate": 1.0}
    items = engine.evidence_items({"res_pos_w10": 42.0, "res_vel": 1.5, "res_rate": 0.2, "unknown": 99.0}, reference)
    assert [i["feature"] for i in items] == ["res_pos_w10", "res_vel"]
    assert "42 m away from the IMU-based estimate" in items[0]["text"]
    assert engine.evidence_items({"res_pos_w10": 1.0}, reference) == []


def test_handover_candidates_respect_battery_and_role_and_are_scored():
    rows = engine.handover_candidates([
        {"aircraft_id": "px4-2", "role": "standby", "battery_pct": 80, "distance_to_task_m": 100, "workload": 0.1},
        {"aircraft_id": "px4-3", "role": "standby", "battery_pct": 35, "distance_to_task_m": 10, "workload": 0.0},
        {"aircraft_id": "px4-4", "role": "decoy", "battery_pct": 90, "distance_to_task_m": 500, "workload": 0.0},
        {"aircraft_id": "px4-5", "role": "primary", "battery_pct": 90, "distance_to_task_m": 1, "workload": 0.0},
    ])
    by_id = {row["aircraft_id"]: row for row in rows}
    assert by_id["px4-3"]["eligible"] is False and "below the 40% minimum" in by_id["px4-3"]["ineligible_reason"]
    assert by_id["px4-5"]["eligible"] is False and "role" in by_id["px4-5"]["ineligible_reason"]
    assert [row["aircraft_id"] for row in rows[:2]] == ["px4-2", "px4-4"] and rows[0]["score"] > rows[1]["score"]
    assert all(not row["eligible"] for row in rows[2:])
    assert engine.handover_candidates([{"aircraft_id": "px4-3", "role": "standby", "battery_pct": 50}], min_battery_pct=60)[0]["eligible"] is False


def test_action_ranking_follows_tier_and_marks_handover_ineligible_without_candidates():
    none = engine.handover_candidates([])
    low = engine.rank_actions("Low", 0.02, "primary", 80, none)
    assert low[0]["action"] == "continue_monitor" and all(row["reason"] and row["tradeoff"] for row in low)
    critical = engine.rank_actions("Critical", 0.95, "primary", 80, none)
    assert critical[0]["action"] == "isolate_aircraft"
    handover = next(row for row in critical if row["action"] == "handover_to_standby")
    assert handover["eligible"] is False and "no eligible" in handover["reason"]
    good = engine.handover_candidates([{"aircraft_id": "px4-2", "role": "standby", "battery_pct": 90, "distance_to_task_m": 50}])
    ranked = engine.rank_actions("High", 0.9, "primary", 80, good)
    assert next(r for r in ranked if r["action"] == "handover_to_standby")["eligible"] is True
    assert [row["rank"] for row in ranked] == list(range(1, 7))
    low_battery = {row["action"]: row["rank_score"] for row in engine.rank_actions("High", 0.9, "primary", 20, good)}
    healthy = {row["action"]: row["rank_score"] for row in engine.rank_actions("High", 0.9, "primary", 90, good)}
    assert low_battery["return_to_launch"] > healthy["return_to_launch"]


# --- API -------------------------------------------------------------------------------------------------------

def window(flight, aircraft_index=0, end=None, length=240):
    aircraft = flight.aircraft[aircraft_index]
    end = end or len(aircraft.t)
    start = max(0, end - length)
    rows = []
    for i in range(start, end):
        rows.append({"t": float(aircraft.t[i]), "gps_position": aircraft.gps.position[i].tolist(),
                     "gps_velocity": aircraft.gps.velocity[i].tolist(), "imu_velocity": aircraft.imu_velocity[i].tolist()})
    return rows, float(aircraft.battery_pct[end - 1])


def assess_body(flight, end=None, role="primary"):
    samples, battery = window(flight, end=end)
    return {"aircraft_id": "px4-1", "role": role, "battery_pct": battery, "remaining_fraction": 0.5, "samples": samples}


def test_assess_requires_authentication_and_csrf(model_dir, general_client):
    from fastapi.testclient import TestClient
    from app.main import app
    flight = simulate_flight("A", 21, [])
    assert TestClient(app).post("/api/research/assess", json=assess_body(flight)).status_code == 401
    assert TestClient(app).get("/api/research/model-info").status_code == 401
    client, _ = general_client
    assert client.get("/api/research/model-info").json()["available"] is True


def test_assess_separates_the_three_outputs_and_quiet_flights_stay_low(model_dir, general_client):
    client, _ = general_client
    quiet = simulate_flight("Q", 31, [], n_aircraft=1, total_s=160)
    result = client.post("/api/research/assess", json=assess_body(quiet, end=240)).json()
    assert {"anomaly_confidence", "severity", "mission_impact", "threat_score", "tier", "evidence", "actions"} <= set(result)
    assert result["threat_score_is_probability"] is False and result["tier"] == "Low" and result["threat_score"] < 20
    assert result["actions"][0]["action"] == "continue_monitor"
    attacked = simulate_flight("Z", 31, [AttackConfig(type="offset", start_s=40, duration_s=60, magnitude=200.0, direction_deg=45)], total_s=160)
    hit = client.post("/api/research/assess", json=assess_body(attacked, end=int(70 * HZ))).json()
    assert hit["anomaly_confidence"] > result["anomaly_confidence"] and hit["threat_score"] > result["threat_score"]
    assert hit["tier"] != "Low" and hit["evidence"] and any("IMU" in item["text"] or "navigation filter" in item["text"] for item in hit["evidence"])
    assert hit["held"] in (True, False) and hit["hold_s"] == 30.0
    assert hit["actions"][0]["action"] != "continue_monitor"
    metrics = client.get("/api/research/model-info").json()
    assert metrics["tiers"]["medium"] < metrics["tiers"]["critical"] and "not a probability" in metrics["formula"]


def test_assess_rejects_ground_truth_and_bad_windows(model_dir, general_client):
    client, _ = general_client
    flight = simulate_flight("G", 41, [])
    body = assess_body(flight, end=60)
    for field in ("is_attacked", "attack_schedule", "label", "attacks", "ground_truth"):
        leaked = {**body, field: [1]}
        response = client.post("/api/research/assess", json=leaked)
        assert response.status_code == 422 and response.json()["detail"]["code"] == "ground_truth_rejected"
    nested = {**body, "samples": [{**body["samples"][0], "is_attacked": True}] + body["samples"][1:]}
    assert client.post("/api/research/assess", json=nested).status_code == 422
    assert client.post("/api/research/assess", json={**body, "samples": body["samples"][:2]}).status_code == 422
    assert client.post("/api/research/assess", json={**body, "extra_field": 1}).status_code == 422


def test_decisions_are_recorded_not_executed(model_dir, general_client, operator_client):
    client, _ = general_client
    operator, _ = operator_client
    base = {"aircraft_id": "px4-1", "action": "increase_monitoring", "source": "offline_replay", "source_name": "Drift test", "tier": "Medium", "threat_score": 33.5}
    saved = client.post("/api/research/decisions", json=base)
    assert saved.status_code == 201
    assert saved.json()["executed"] is False and saved.json()["username"] and saved.json()["action"] == "increase_monitoring"
    assert client.post("/api/research/decisions", json={**base, "action": "handover_to_standby"}).status_code == 422
    handover = client.post("/api/research/decisions", json={**base, "action": "handover_to_standby", "handover_candidate": "px4-2"})
    assert handover.status_code == 201 and handover.json()["handover_candidate"] == "px4-2"
    assert client.post("/api/research/decisions", json={**base, "handover_candidate": "px4-2"}).status_code == 422
    assert client.post("/api/research/decisions", json={**base, "attack_schedule": []}).status_code == 422
    assert operator.post("/api/research/decisions", json={**base, "action": "continue_monitor"}).status_code == 201
    listed = client.get("/api/research/decisions").json()["decisions"]
    assert [item["action"] for item in listed][:3] == ["continue_monitor", "handover_to_standby", "increase_monitoring"]
    from fastapi.testclient import TestClient
    from app.main import app
    assert TestClient(app).post("/api/research/decisions", json=base).status_code in (401, 403)


# --- larger dataset, fast evaluator and bootstrap intervals -------------------------------------------------------

def test_manifest_has_counts_per_split_attack_type_and_magnitude_bin(trained):
    counts = trained["manifest"]["counts"]
    assert set(counts) == {"train", "val", "test"}
    for split, entry in counts.items():
        assert entry["flights"] == entry["attacked_flights"] + entry["attack_free_flights"]
        assert set(entry["by_attack_type"]) == {"offset", "jump", "drift", "freeze", "noise", "velocity_inconsistency"}
        assert {"low", "mid", "high", "hard"} >= set(entry["by_magnitude_bin"])
        assert any(key.startswith("drift:") for key in entry["by_attack_type_and_bin"])
        assert entry["attack_free_hours"] > 0
    assert trained["manifest"]["profile"] == "legacy"


def test_large_profile_guards_its_minimums():
    from app.research.threat_data import PROFILES, _assert_minimums
    assert PROFILES["large"]["pattern"].count("train") == 6 and PROFILES["large"]["pattern"].count("val") == 2 and PROFILES["large"]["pattern"].count("test") == 2
    enough = {"by_attack_type": {t: 25 for t in ("offset", "jump", "drift", "freeze", "noise", "velocity_inconsistency")}, "attack_free_hours": 8.0}
    _assert_minimums({"val": enough, "test": enough})
    with pytest.raises(ValueError):
        _assert_minimums({"val": {**enough, "attack_free_hours": 7.9}, "test": enough})
    with pytest.raises(ValueError):
        _assert_minimums({"val": {**enough, "by_attack_type": {**enough["by_attack_type"], "drift": 24}}, "test": enough})


def test_fast_evaluator_matches_the_reference_evaluation_and_bootstrap_brackets_it(trained):
    from app.research import threat_models as tm
    frame = tm.load_tables(trained["dataset"])
    val = frame[frame.split == "val"].reset_index(drop=True)
    score = tm.cusum_scores(val, 4.0)
    for threshold in (3.0, 10.0, 40.0):
        slow = tm.evaluate(val, score, threshold)
        fast = tm.fast_eval(val).summary(score, threshold)
        assert fast["recall"] == pytest.approx(slow["recall"], abs=1e-4)
        assert fast["precision"] == pytest.approx(slow["precision"], abs=1e-4)
        assert fast["false_alarms_per_hour"] == pytest.approx(slow["false_alarms_per_hour"], abs=0.01)
        assert fast["detection_rate"] == pytest.approx(slow["detection_rate"], abs=1e-3)
    ci = tm.fast_eval(val).bootstrap(score, 3.0, rounds=200)
    point = tm.fast_eval(val).summary(score, 3.0)
    for key in ("precision", "recall", "false_alarms_per_hour", "detection_rate"):
        low, high = ci[key]
        assert low <= high
    assert ci["recall"][0] - 0.02 <= point["recall"] <= ci["recall"][1] + 0.02


def test_trained_metrics_carry_bootstrap_intervals_and_the_feature_set_trial(trained):
    metrics = trained["metrics"]
    selected = metrics["models"][metrics["selected_model"]]
    for split in ("validation", "test"):
        for key in ("precision", "recall", "f1", "false_alarms_per_hour", "detection_rate"):
            low, high = selected[split]["ci95"][key]
            assert low <= high
    assert metrics["bootstrap"]["unit"] == "flights"
    assert metrics["feature_set_trial"]["chosen_feature_set"] in {"v2", "v2_long_windows"}
    assert all(col in metrics["feature_columns"] for col in FEATURE_COLUMNS) or metrics["feature_set_trial"]["chosen_feature_set"] == "v2"

