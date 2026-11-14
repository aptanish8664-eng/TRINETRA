"""Train, evaluate, calibrate and load the offline GPS-inconsistency detector.

Pipeline (one command):  python -m app.research.threat_models [--flights-per-cell 8]
Leakage controls: features come from sensor streams only; labels live in a separate file and are used only as the
training target and for evaluation; splits are by flight; thresholds and calibration are fit on validation only; the
test split is touched once, for reporting. Synthetic data only - results do not transfer to real attacks.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.isotonic import IsotonicRegression
from sklearn.metrics import brier_score_loss, precision_recall_curve, roc_auc_score

from . import threat_engine as engine
from .threat_data import DT, FEATURE_COLUMNS, FEATURE_COLUMNS_ALL, FEATURE_COLUMNS_LONG, RECOVERY_S, cusum_array, generate_dataset, recovery_flags

ARTIFACT_DIR = Path(__file__).resolve().parents[2] / "research_artifacts"
KEYS = ["flight_id", "aircraft_id"]
SUSTAIN_SAMPLES = 3
REFERENCE_FEATURES = list(engine.EVIDENCE_TEMPLATES)


# --- helpers ---------------------------------------------------------------------------------------------------

def load_tables(dataset_dir: Path) -> pd.DataFrame:
    features = pd.read_csv(dataset_dir / "features.csv")
    labels = pd.read_csv(dataset_dir / "labels_HIDDEN.csv")
    flights = pd.read_csv(dataset_dir / "flights.csv")
    frame = features.merge(labels[KEYS + ["t", "is_attacked", "attack_type"]], on=KEYS + ["t"], how="left")
    frame = frame.merge(flights[["flight_id", "split", "cell", "tier", "hard_case"]].rename(columns={"tier": "magnitude_tier", "attack_type": "x"}), on="flight_id")
    frame["attack_type"] = frame["attack_type"].fillna("")
    frame = frame.sort_values(KEYS + ["t"]).reset_index(drop=True)
    frame["recovery"] = recovery_mask(frame)
    return frame


def recovery_mask(frame: pd.DataFrame) -> np.ndarray:
    """Evaluation-side flag (never a feature): un-attacked rows within RECOVERY_S after an attack ends (see threat_data.recovery_flags)."""
    out = np.zeros(len(frame), dtype=bool)
    attacked = frame["is_attacked"].astype(bool).to_numpy()
    t = frame["t"].to_numpy()
    for _, positions in frame.groupby(KEYS, sort=False).indices.items():
        out[positions] = recovery_flags(t[positions], attacked[positions])
    return out


def cusum_scores(frame: pd.DataFrame, k: float) -> np.ndarray:
    out = np.zeros(len(frame))
    values = frame["innov_ratio"].to_numpy()
    for _, positions in frame.groupby(KEYS, sort=False).indices.items():
        s = 0.0
        for position in positions:
            s = max(0.0, s + values[position] - k)
            out[position] = s
    return out


def best_threshold(score: np.ndarray, label: np.ndarray) -> float:
    precision, recall, thresholds = precision_recall_curve(label, score)
    f1 = 2 * precision[:-1] * recall[:-1] / np.maximum(precision[:-1] + recall[:-1], 1e-12)
    return float(thresholds[int(np.argmax(f1))])


FALSE_ALARM_BUDGET_PER_HOUR = 1.0
SIMPLICITY_MARGIN = 0.02


def tune_threshold(frame: pd.DataFrame, score: np.ndarray, budget: float = FALSE_ALARM_BUDGET_PER_HOUR) -> float:
    """Recall-maximising threshold whose sustained-alert false-alarm rate on `frame` stays within `budget` per hour.

    Candidates come from the score distribution of normal rows (not attacked, not recovering); ties go to the higher threshold.
    """
    fast = fast_eval(frame)
    normal = score[fast.normal]
    candidates = np.unique(np.concatenate([np.quantile(normal, np.linspace(0.60, 0.9995, 70)), [float(normal.max())]]))
    best: Optional[tuple[tuple[float, float], float]] = None
    for threshold in candidates:
        summary = fast.summary(score, float(threshold))
        if summary["false_alarms_per_hour"] <= budget:
            key = (summary["recall"], summary["detection_rate"])
            if best is None or key >= best[0]:
                best = (key, float(threshold))
    return best[1] if best else float(candidates[-1])


def sustained(mask: np.ndarray, samples: int) -> np.ndarray:
    run = 0
    out = np.zeros(len(mask), dtype=bool)
    for index, flag in enumerate(mask):
        run = run + 1 if flag else 0
        out[index] = run >= samples
    return out


def evaluate(frame: pd.DataFrame, score: np.ndarray, threshold: float, sustain: int = SUSTAIN_SAMPLES) -> dict[str, Any]:
    y = frame["is_attacked"].to_numpy().astype(bool)
    recovery = frame["recovery"].to_numpy().astype(bool) if "recovery" in frame else np.zeros(len(frame), dtype=bool)
    predicted = score >= threshold
    tp, fp, fn = int((predicted & y).sum()), int((predicted & ~y & ~recovery).sum()), int((~predicted & y).sum())
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    result: dict[str, Any] = {
        "samples": int(len(frame)), "attacked_samples": int(y.sum()), "precision": round(precision, 4), "recall": round(recall, 4),
        "f1": round(2 * precision * recall / (precision + recall), 4) if precision + recall else 0.0,
        "roc_auc": round(float(roc_auc_score(y[~recovery], score[~recovery])), 4) if y.any() and (~y & ~recovery).any() else None,
    }
    false_alarms, unattacked_s = 0, float((~y & ~recovery).sum() * DT)
    latencies: dict[str, list] = {}
    per_flight: dict[str, dict] = {}
    for (flight_id, aircraft_id), positions in frame.groupby(KEYS, sort=False).indices.items():
        alert = sustained(predicted[positions], sustain)
        t = frame["t"].to_numpy()[positions]
        attacked = y[positions]
        onsets = np.flatnonzero(alert & ~np.concatenate([[False], alert[:-1]]))
        false_alarms += int(sum(1 for i in onsets if not attacked[i] and not recovery[positions][i]))
        if attacked.any():
            start = int(np.argmax(attacked))
            hits = np.flatnonzero(alert[start:])
            latency = float(t[start + hits[0]] - t[start]) if len(hits) else None
            row = frame.iloc[positions[0]]
            per_flight[f"{flight_id}/{aircraft_id}"] = {"type": row["cell"].split(":")[0], "tier": row["magnitude_tier"], "latency_s": latency,
                                                         "hits": int((predicted[positions] & attacked).sum()), "attacked": int(attacked.sum())}
    detected = [v["latency_s"] for v in per_flight.values() if v["latency_s"] is not None]
    result["false_alarms_per_hour"] = round(false_alarms / (unattacked_s / 3600.0), 2) if unattacked_s else None
    result["attacked_flights"] = len(per_flight)
    result["detection_rate"] = round(len(detected) / len(per_flight), 3) if per_flight else None
    result["latency_median_s"] = round(float(np.median(detected)), 1) if detected else None
    result["latency_p90_s"] = round(float(np.percentile(detected, 90)), 1) if detected else None

    def breakdown(field: str) -> dict[str, Any]:
        groups: dict[str, list] = {}
        for value in per_flight.values():
            groups.setdefault(str(value[field]), []).append(value["latency_s"])
        return {name: {"flights": len(items), "detection_rate": round(sum(x is not None for x in items) / len(items), 3),
                       "recall": round(sum(v["hits"] for v in per_flight.values() if str(v[field]) == name) / max(1, sum(v["attacked"] for v in per_flight.values() if str(v[field]) == name)), 3),
                       "latency_median_s": round(float(np.median([x for x in items if x is not None])), 1) if any(x is not None for x in items) else None}
                for name, items in sorted(groups.items())}
    result["by_attack_type"] = breakdown("type")
    result["by_magnitude_tier"] = breakdown("tier")
    return result


def reliability_table(confidence: np.ndarray, label: np.ndarray, bins: int = 10) -> list[dict[str, Any]]:
    edges = np.linspace(0, 1, bins + 1)
    rows = []
    for low, high in zip(edges[:-1], edges[1:]):
        mask = (confidence >= low) & (confidence < high if high < 1 else confidence <= high)
        if mask.any():
            rows.append({"bin": f"{low:.1f}-{high:.1f}", "count": int(mask.sum()), "mean_predicted": round(float(confidence[mask].mean()), 4),
                         "observed_rate": round(float(label[mask].mean()), 4)})
    return rows


def threat_scores_for(frame: pd.DataFrame, confidence: np.ndarray) -> np.ndarray:
    sev = engine.severity(frame["res_pos_w10"], frame["res_rate"], frame["res_vel"])
    impact = engine.mission_impact(frame["role"].to_numpy(), frame["battery_pct"], frame["remaining_fraction"])
    return engine.threat_score(confidence, sev, impact)


def tune_tiers(normal_scores: np.ndarray, attacked_scores: np.ndarray) -> engine.TierThresholds:
    medium = max(float(np.quantile(normal_scores, 0.99)), 8.0)
    above = attacked_scores[attacked_scores > medium]
    high = max(float(np.quantile(above, 0.4)) if len(above) else medium + 15, medium + 8)
    critical = max(float(np.quantile(above, 0.75)) if len(above) else high + 15, high + 8)
    return engine.TierThresholds(round(medium, 2), round(high, 2), round(critical, 2))


# --- pipeline --------------------------------------------------------------------------------------------------

class FastEval:
    """Vectorised alert/false-alarm bookkeeping for one split (rows sorted by flight, aircraft, time). Used for tuning and bootstrap CIs."""

    def __init__(self, frame: pd.DataFrame):
        self.frame = frame  # keeps the frame alive so the id()-keyed cache cannot return a stale entry
        self.n = len(frame)
        self.y = frame["is_attacked"].to_numpy().astype(bool)
        self.rec = frame["recovery"].to_numpy().astype(bool) if "recovery" in frame else np.zeros(self.n, dtype=bool)
        self.gid = frame.groupby(KEYS, sort=False).ngroup().to_numpy()
        self.ng = int(self.gid.max()) + 1
        self.idx = np.arange(self.n)
        first = np.zeros(self.ng, dtype=int)
        first[self.gid[::-1]] = self.idx[::-1]
        self.g0 = first[self.gid]
        att_first = np.full(self.ng, self.n)
        attacked_positions = np.flatnonzero(self.y)
        np.minimum.at(att_first, self.gid[attacked_positions], attacked_positions)
        self.a0 = att_first[self.gid]
        self.attacked_group = att_first < self.n
        flights = frame["flight_id"].to_numpy()
        self.group_flight = flights[first]
        self.normal = ~self.y & ~self.rec
        self.normal_s = np.bincount(self.gid, weights=self.normal.astype(float), minlength=self.ng) * DT

    def group_stats(self, score: np.ndarray, threshold: float, sustain: int = SUSTAIN_SAMPLES) -> dict[str, np.ndarray]:
        flag = score >= threshold
        last_bad = np.maximum(np.maximum.accumulate(np.where(flag, -1, self.idx)), self.g0 - 1)
        alert = (self.idx - last_bad) >= sustain
        previous = np.concatenate([[False], alert[:-1]])
        onset = alert & (~previous | (self.idx == self.g0))
        count = lambda mask: np.bincount(self.gid, weights=mask.astype(float), minlength=self.ng)
        return {
            "tp": count(flag & self.y), "fp": count(flag & ~self.y & ~self.rec), "fn": count(~flag & self.y),
            "fa": count(onset & self.normal), "detected": ((count(alert & (self.idx >= self.a0)) > 0) & self.attacked_group).astype(float),
        }

    def summary(self, score: np.ndarray, threshold: float) -> dict[str, float]:
        stats = self.group_stats(score, threshold)
        tp, fp, fn = stats["tp"].sum(), stats["fp"].sum(), stats["fn"].sum()
        hours = self.normal_s.sum() / 3600.0
        return {"recall": tp / (tp + fn) if tp + fn else 0.0, "precision": tp / (tp + fp) if tp + fp else 0.0,
                "false_alarms_per_hour": stats["fa"].sum() / hours if hours else float("inf"),
                "detection_rate": stats["detected"].sum() / max(1, self.attacked_group.sum())}

    def bootstrap(self, score: np.ndarray, threshold: float, rounds: int = 1000, seed: int = 7) -> dict[str, list[float]]:
        """95% percentile intervals from resampling whole flights (all aircraft of a flight move together)."""
        stats = self.group_stats(score, threshold)
        flights, flight_index = np.unique(self.group_flight, return_inverse=True)
        columns = np.column_stack([stats["tp"], stats["fp"], stats["fn"], stats["fa"], self.normal_s, self.attacked_group.astype(float), stats["detected"]])
        per_flight = np.zeros((len(flights), columns.shape[1]))
        np.add.at(per_flight, flight_index, columns)
        rng = np.random.default_rng(seed)
        picks = rng.integers(0, len(flights), size=(rounds, len(flights)))
        totals = per_flight[picks].sum(axis=1)  # rounds x columns
        tp, fp, fn, fa, normal_s, attacked, detected = totals.T
        precision = np.where(tp + fp > 0, tp / np.maximum(tp + fp, 1), 0.0)
        recall = np.where(tp + fn > 0, tp / np.maximum(tp + fn, 1), 0.0)
        f1 = np.where(precision + recall > 0, 2 * precision * recall / np.maximum(precision + recall, 1e-12), 0.0)
        series = {"precision": precision, "recall": recall, "f1": f1, "false_alarms_per_hour": fa / np.maximum(normal_s / 3600.0, 1e-9),
                  "detection_rate": detected / np.maximum(attacked, 1)}
        return {name: [round(float(np.percentile(values, 2.5)), 3), round(float(np.percentile(values, 97.5)), 3)] for name, values in series.items()}


_FAST: dict[int, FastEval] = {}


def fast_eval(frame: pd.DataFrame) -> FastEval:
    key = id(frame)
    if key not in _FAST or _FAST[key].n != len(frame):
        _FAST[key] = FastEval(frame)
    return _FAST[key]


def _normal_mask(part: pd.DataFrame) -> np.ndarray:
    """Rows that count as 'normal' for calibration, thresholds and false alarms: not attacked and not in the recovery window."""
    return ~part["is_attacked"].to_numpy().astype(bool) & ~part["recovery"].to_numpy().astype(bool)


def _rank(metrics: dict[str, Any]) -> tuple[float, float]:
    return (metrics["recall"], metrics["detection_rate"] or 0.0)


def _crossfit_isotonic(part: pd.DataFrame, score: np.ndarray) -> np.ndarray:
    """Calibrated scores for `part` where each half of the flights is calibrated by an isotonic fit on the OTHER half (no in-sample optimism)."""
    flights = sorted(part["flight_id"].unique())
    half = {flight: index % 2 for index, flight in enumerate(flights)}
    group = part["flight_id"].map(half).to_numpy()
    usable = ~part["recovery"].to_numpy().astype(bool)
    y = part["is_attacked"].to_numpy().astype(int)
    out = np.zeros(len(part))
    for g in (0, 1):
        fit = (group != g) & usable
        iso = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0).fit(score[fit], y[fit])
        out[group == g] = iso.predict(score[group == g])
    return out


def _diagnosis(part: pd.DataFrame, score: np.ndarray, threshold: float) -> dict[str, Any]:
    """Sample-level recall per attack type and per magnitude tier (hidden labels, report only)."""
    attacked = part["is_attacked"].to_numpy().astype(bool)
    predicted = score >= threshold
    out: dict[str, Any] = {"by_attack_type_recall": {}, "by_magnitude_recall": {}}
    for field, name in (("attack_type", "by_attack_type_recall"), ("magnitude_tier", "by_magnitude_recall")):
        for value in sorted(part.loc[attacked, field].unique()):
            mask = attacked & (part[field].to_numpy() == value)
            out[name][str(value)] = round(float(predicted[mask].mean()), 3)
    return out


BOOTSTRAP_ROUNDS = 1000
WEAK_RECALL = 0.5


def _full(part: pd.DataFrame, score: np.ndarray, threshold: float) -> dict[str, Any]:
    """evaluate() plus per-type/tier sample recall and bootstrap confidence intervals over flights."""
    result = evaluate(part, score, threshold)
    result["ci95"] = fast_eval(part).bootstrap(score, threshold, BOOTSTRAP_ROUNDS)
    result.update(_diagnosis(part, score, threshold))
    return result


def _fit_set(columns: list[str], fit: pd.DataFrame, val: pd.DataFrame, test: pd.DataFrame, val_cusum: np.ndarray, test_cusum: np.ndarray,
             budget: float, seed: int, evaluate_test: bool) -> dict[str, Any]:
    """Class-weighted gradient boosting and the combined detector for one feature set; every choice is made on validation."""
    y = lambda part: part["is_attacked"].astype(int).to_numpy()
    usable = ~val["recovery"].astype(bool).to_numpy()
    grid = [
        {"max_depth": 3, "max_iter": 200, "learning_rate": 0.1, "l2_regularization": 0.0},
        {"max_depth": 4, "max_iter": 200, "learning_rate": 0.1, "l2_regularization": 1.0},
        {"max_depth": 5, "max_iter": 300, "learning_rate": 0.05, "l2_regularization": 1.0},
        {"max_depth": 6, "max_iter": 150, "learning_rate": 0.08, "l2_regularization": 5.0},
    ]
    trials, best = [], None
    for params in grid:
        candidate = HistGradientBoostingClassifier(**params, class_weight="balanced", early_stopping=False, random_state=seed).fit(fit[columns], y(fit))
        prob = candidate.predict_proba(val[columns])[:, 1]
        threshold = tune_threshold(val, prob, budget)
        summary = fast_eval(val).summary(prob, threshold)
        trials.append({"params": params, "val_recall": round(summary["recall"], 4), "val_detection_rate": round(summary["detection_rate"], 3),
                       "val_false_alarms_per_hour": round(summary["false_alarms_per_hour"], 2)})
        if best is None or (summary["recall"], summary["detection_rate"]) > best[0]:
            best = ((summary["recall"], summary["detection_rate"]), candidate, params, threshold, prob)
    _, gb, params, gb_threshold, val_prob = best
    test_prob = gb.predict_proba(test[columns])[:, 1]
    gb_model = {
        "description": "scikit-learn HistGradientBoostingClassifier, class_weight=balanced", "parameters": {**params, "threshold": round(gb_threshold, 4), "sustain_samples": SUSTAIN_SAMPLES},
        "hyperparameter_trials_on_validation": trials, "validation": _full(val, val_prob, gb_threshold),
        "test": _full(test, test_prob, gb_threshold) if evaluate_test else None,
    }
    iso_cusum = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0).fit(val_cusum[usable], y(val)[usable])
    iso_gb = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0).fit(val_prob[usable], y(val)[usable])
    combine = lambda cusum, prob: np.maximum(iso_cusum.predict(cusum), iso_gb.predict(prob))
    val_combined_cv = np.maximum(_crossfit_isotonic(val, val_cusum), _crossfit_isotonic(val, val_prob))  # validation view without in-sample calibration optimism
    combined_threshold = tune_threshold(val, val_combined_cv, budget)
    val_combined, test_combined = combine(val_cusum, val_prob), combine(test_cusum, test_prob)
    combined_model = {
        "description": "max(calibrated CUSUM, calibrated gradient boosting); validation numbers use cross-fitted calibration",
        "parameters": {"threshold": round(combined_threshold, 4), "sustain_samples": SUSTAIN_SAMPLES},
        "validation": _full(val, val_combined_cv, combined_threshold),
        "test": _full(test, test_combined, combined_threshold) if evaluate_test else None,
    }
    return {"columns": columns, "gb": gb, "iso_cusum": iso_cusum, "iso_gb": iso_gb, "gb_model": gb_model, "combined_model": combined_model,
            "raw": {"hist_gradient_boosting": (val_prob, test_prob), "combined_max": (val_combined, test_combined)}}


def train_pipeline(dataset_dir: Path, artifact_dir: Path, seed: int = 20261113, evaluate_test: bool = True,
                   budget: float = FALSE_ALARM_BUDGET_PER_HOUR, try_long_windows: bool = True) -> dict[str, Any]:
    """Fit on train, choose everything on validation, report on test once (skip with evaluate_test=False while developing)."""
    frame = load_tables(dataset_dir)
    manifest = json.loads((dataset_dir / "manifest.json").read_text(encoding="utf-8"))
    parts = {name: frame[frame.split == name].reset_index(drop=True) for name in ("train", "val", "test")}
    flights = {name: set(part.flight_id) for name, part in parts.items()}
    assert not (flights["train"] & flights["val"] or flights["train"] & flights["test"] or flights["val"] & flights["test"]), "flight leaked across splits"
    train, val, test = parts["train"], parts["val"], parts["test"]
    fit = train[~train["recovery"].astype(bool)].reset_index(drop=True)  # recovery rows are ambiguous: never used as negatives
    y = lambda part: part["is_attacked"].astype(int).to_numpy()
    usable = ~val["recovery"].astype(bool).to_numpy()

    # (a) CUSUM baseline on the normalised innovation ratio; k and threshold chosen on validation (recall at the false-alarm budget).
    best = None
    for k in (2.0, 4.0, 8.0, 16.0):
        score = cusum_scores(val, k)
        threshold = tune_threshold(val, score, budget)
        summary = fast_eval(val).summary(score, threshold)
        if best is None or (summary["recall"], summary["detection_rate"]) > best[0]:
            best = ((summary["recall"], summary["detection_rate"]), k, threshold)
    _, k, base_threshold = best
    val_cusum, test_cusum = cusum_scores(val, k), cusum_scores(test, k)
    cusum_model = {
        "description": "CUSUM on the normalised innovation ratio", "parameters": {"k": k, "threshold": round(base_threshold, 3), "sustain_samples": SUSTAIN_SAMPLES},
        "validation": _full(val, val_cusum, base_threshold), "test": _full(test, test_cusum, base_threshold) if evaluate_test else None,
    }

    # (b)+(c) gradient boosting and the combined detector on the v2 features; then ONE targeted trial (longer residual windows) if slow drift
    # or low-magnitude attacks are still weak on validation. The feature set is chosen on validation only.
    sets = {"v2": _fit_set(FEATURE_COLUMNS, fit, val, test, val_cusum, test_cusum, budget, seed, evaluate_test)}
    order = ["baseline_cusum", "hist_gradient_boosting", "combined_max"]

    def pick(models: dict[str, Any]) -> str:
        ranking = {name: _rank(models[name]["validation"]) for name in order}
        chosen = order[0]
        for name in order[1:]:  # a more complex detector must beat a simpler one by >= SIMPLICITY_MARGIN recall
            if ranking[name][0] >= ranking[chosen][0] + SIMPLICITY_MARGIN:
                chosen = name
        return chosen

    def models_of(item: dict[str, Any]) -> dict[str, Any]:
        return {"baseline_cusum": cusum_model, "hist_gradient_boosting": item["gb_model"], "combined_max": item["combined_model"]}

    base_models = models_of(sets["v2"])
    base_pick = pick(base_models)
    base_val = base_models[base_pick]["validation"]
    weak = {"drift": base_val["by_attack_type_recall"].get("drift", 0.0), "low": base_val["by_magnitude_recall"].get("low", 0.0)}
    trial: dict[str, Any] = {"triggered": bool(try_long_windows and min(weak.values()) < WEAK_RECALL), "validation_recall_of_selected_v2": weak,
                             "change": "residual windows of 90 s and 120 s plus their early-scale ratios and growth rates (6 columns)"}
    chosen_set = "v2"
    if trial["triggered"]:
        sets["v2_long_windows"] = _fit_set(FEATURE_COLUMNS_ALL, fit, val, test, val_cusum, test_cusum, budget, seed, evaluate_test)
        long_models = models_of(sets["v2_long_windows"])
        long_pick = pick(long_models)
        long_val = long_models[long_pick]["validation"]
        better = _rank(long_val)[0] >= _rank(base_val)[0] + SIMPLICITY_MARGIN
        trial.update({
            "v2": {"selected": base_pick, "validation_recall": base_val["recall"], "validation_detection_rate": base_val["detection_rate"],
                   "validation_drift_recall": weak["drift"], "validation_low_magnitude_recall": weak["low"],
                   "test": base_models[base_pick]["test"] and {k2: v for k2, v in base_models[base_pick]["test"].items() if k2 in ("recall", "precision", "f1", "roc_auc", "false_alarms_per_hour", "detection_rate", "by_attack_type_recall", "by_magnitude_recall")}},
            "v2_long_windows": {"selected": long_pick, "validation_recall": long_val["recall"], "validation_detection_rate": long_val["detection_rate"],
                                "validation_drift_recall": long_val["by_attack_type_recall"].get("drift"), "validation_low_magnitude_recall": long_val["by_magnitude_recall"].get("low"),
                                "test": long_models[long_pick]["test"] and {k2: v for k2, v in long_models[long_pick]["test"].items() if k2 in ("recall", "precision", "f1", "roc_auc", "false_alarms_per_hour", "detection_rate", "by_attack_type_recall", "by_magnitude_recall")}},
            "adopted": bool(better), "rule": f"adopted only if validation recall improves by >= {SIMPLICITY_MARGIN}",
        })
        chosen_set = "v2_long_windows" if better else "v2"
    chosen = sets[chosen_set]
    trial["chosen_feature_set"] = chosen_set
    models = {**models_of(chosen), "lstm_next_state": None}
    try:
        import torch  # noqa: F401
        models["lstm_next_state"] = {"status": "available_but_not_trained", "reason": "torch is installed but a sequence model was not trained in this milestone"}
    except Exception:
        models["lstm_next_state"] = {"status": "skipped", "reason": "torch is not installed and was not installed automatically"}
    selected = pick(models)
    columns = chosen["columns"]
    val_prob, test_prob = chosen["raw"]["hist_gradient_boosting"]
    raw = {"baseline_cusum": (val_cusum, test_cusum), **chosen["raw"]}[selected]
    val_raw, test_raw = raw
    gb, iso_gb, iso_cusum = chosen["gb"], chosen["iso_gb"], chosen["iso_cusum"]
    final_iso = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0).fit(val_raw[usable], y(val)[usable])
    val_conf, test_conf = final_iso.predict(val_raw), final_iso.predict(test_raw)
    calibration = {"method": "isotonic regression fit on the validation split (recovery rows excluded)", "detector": selected}
    if evaluate_test:
        keep = ~test["recovery"].astype(bool).to_numpy()  # recovery rows are neither normal nor attacked
        calibration.update({
            "brier_test_raw": round(float(brier_score_loss(y(test)[keep], np.clip(test_raw[keep], 0, 1))), 5) if selected != "baseline_cusum" else None,
            "brier_test_calibrated": round(float(brier_score_loss(y(test)[keep], test_conf[keep])), 5),
            "reliability_test": reliability_table(test_conf[keep], y(test)[keep]),
        })
        rows = calibration["reliability_test"]
        big = [row for row in rows if row["count"] >= 50]
        calibration["max_abs_gap_bins_50plus_samples"] = round(max((abs(r["mean_predicted"] - r["observed_rate"]) for r in big), default=0.0), 4)
        predicted_rank = pd.Series([r["mean_predicted"] for r in rows]).rank()
        observed_rank = pd.Series([r["observed_rate"] for r in rows]).rank()
        calibration["reliability_rank_correlation"] = round(float(predicted_rank.corr(observed_rank)), 3) if len(rows) > 2 else None
    # Tiers tuned on validation (normal = not attacked and not recovering).
    val_scores = threat_scores_for(val, val_conf)
    attacked = val["is_attacked"].to_numpy().astype(bool)
    thresholds = tune_tiers(val_scores[_normal_mask(val)], val_scores[attacked])

    def distribution(part: pd.DataFrame, conf: np.ndarray) -> dict[str, dict[str, float]]:
        scores = threat_scores_for(part, conf)
        tiers = np.array([thresholds.tier(s) for s in scores])
        is_attacked = part["is_attacked"].to_numpy().astype(bool)
        groups = {"normal": _normal_mask(part), "attacked": is_attacked, "recovery": part["recovery"].to_numpy().astype(bool)}
        return {name: {tier: round(float((tiers[sel] == tier).mean()), 4) for tier in engine.TIERS} for name, sel in groups.items() if sel.any()}

    normal_train = train[_normal_mask(train)]
    reference = {name: round(float(normal_train[name].abs().quantile(0.99)), 4) for name in REFERENCE_FEATURES if name in normal_train}
    artifact_dir.mkdir(parents=True, exist_ok=True)
    model_path = artifact_dir / "model.joblib"
    joblib.dump({"detector": selected, "gb": gb, "iso_gb": iso_gb, "iso_cusum": iso_cusum, "cusum_k": k, "final_iso": final_iso,
                 "feature_columns": columns, "reference": reference, "detector_threshold": models[selected]["parameters"].get("threshold", base_threshold),
                 "sustain_samples": SUSTAIN_SAMPLES, "tiers": thresholds.as_dict()}, model_path)
    sha = hashlib.sha256(model_path.read_bytes()).hexdigest()
    metrics = {
        "model_id": sha[:12], "trained_at": datetime.now(timezone.utc).isoformat(), "artifact_sha256": sha, "selected_model": selected,
        "selection_rule": f"highest validation recall subject to <= {budget:g} sustained false alarm(s) per hour; a more complex detector must beat a simpler one by >= {SIMPLICITY_MARGIN} recall; not precision",
        "false_alarm_budget_per_hour": budget, "test_evaluated": evaluate_test, "bootstrap": {"rounds": BOOTSTRAP_ROUNDS, "unit": "flights", "interval": "95% percentile"},
        "dataset": {key: manifest[key] for key in ("seed", "config_hash", "splits", "total_flights", "total_rows") if key in manifest} | {"profile": manifest.get("profile", "legacy"), "counts": manifest.get("counts")},
        "models": models, "feature_set_trial": trial, "calibration": calibration,
        "tiers": {"thresholds": thresholds.as_dict(), "tuned_on": "validation", "validation_distribution": distribution(val, val_conf),
                  "test_distribution": distribution(test, test_conf) if evaluate_test else None,
                  "rule": "medium = 99th percentile of normal validation scores; high/critical = 40th/75th percentile of attacked validation scores above medium"},
        "threat_score_formula": engine.THREAT_SCORE_FORMULA, "evidence_reference_normal_p99": reference, "feature_columns": columns,
        "leakage_controls": ["features computed from sensor streams only (causal, per-flight early-scale normalisation)", "labels in a separate hidden file",
                             "split by flight (asserted)", "thresholds, calibration, model choice, feature-set choice and tiers fit on validation only",
                             "post-attack recovery rows (30 s) excluded from the normal class and from false-alarm counting (label-side bookkeeping, never a feature)",
                             "test split used only for the final report"],
        "limits": ["synthetic data, not real attacks", "flat-earth ENU frame", "EKF-like residual, not a live PX4 EKF residual",
                   "confidence intervals are bootstrap over simulated flights and do not cover simulator-vs-real differences",
                   "features assume the assessed window starts at the beginning of the flight (early-flight scale)"],
    }
    (artifact_dir / "metrics.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    (artifact_dir / "model_card.md").write_text(model_card(metrics), encoding="utf-8")
    return metrics


def model_card(metrics: dict[str, Any]) -> str:
    def interval(test: dict[str, Any], key: str) -> str:
        low, high = test["ci95"][key]
        return f"{test[key]} [{low}, {high}]"
    rows = []
    for name, label in (("baseline_cusum", "CUSUM baseline"), ("hist_gradient_boosting", "Gradient boosting (class-weighted)"), ("combined_max", "Combined (max of calibrated CUSUM and model)")):
        test = metrics["models"][name].get("test")
        if test:
            rows.append(f"| {label} | {interval(test, 'precision')} | {interval(test, 'recall')} | {interval(test, 'f1')} | {test['roc_auc']} | {interval(test, 'false_alarms_per_hour')} | {interval(test, 'detection_rate')} | {test['latency_median_s']} / {test['latency_p90_s']} |")
    lstm = metrics["models"]["lstm_next_state"]
    counts = (metrics["dataset"].get("counts") or {})
    sizes = ", ".join(f"{name}: {c['flights']} flights, {c['attack_free_hours']} h attack-free" for name, c in counts.items())
    return "\n".join([
        "# GPS inconsistency detector - model card", "",
        f"Model id `{metrics['model_id']}`, trained {metrics['trained_at']}. **Synthetic data only; not validated on real attacks.**",
        f"Dataset profile `{metrics['dataset'].get('profile')}` ({sizes}). Selected detector: **{metrics['selected_model']}** on feature set `{metrics['feature_set_trial']['chosen_feature_set']}` - {metrics['selection_rule']}.", "",
        "| model (test split; 95% bootstrap interval over flights) | precision | recall | F1 | ROC-AUC | false alarms/h | flights detected | median / p90 latency s |", "|---|---|---|---|---|---|---|---|", *rows,
        "", f"Sequence model: {lstm['status']} ({lstm.get('reason', '')}).", "",
        f"Calibration: {metrics['calibration']['method']}; test Brier {metrics['calibration'].get('brier_test_raw')} -> {metrics['calibration'].get('brier_test_calibrated')}.",
        f"Tier thresholds (validation): {metrics['tiers']['thresholds']}.", "", "Known limits: " + "; ".join(metrics["limits"]) + ".", "",
    ])


class ThreatModel:
    """Loaded artifact; verifies the file hash recorded in metrics.json before unpickling."""

    def __init__(self, artifact_dir: Path):
        metrics = json.loads((artifact_dir / "metrics.json").read_text(encoding="utf-8"))
        raw = (artifact_dir / "model.joblib").read_bytes()
        if hashlib.sha256(raw).hexdigest() != metrics["artifact_sha256"]:
            raise ValueError("model artifact hash mismatch; refusing to load")
        bundle = joblib.load(artifact_dir / "model.joblib")
        self.metrics = metrics
        self.model_id = metrics["model_id"]
        self.bundle = bundle
        self.detector = bundle["detector"]
        self.feature_columns, self.reference = bundle["feature_columns"], bundle["reference"]
        self.tiers = engine.TierThresholds(**bundle["tiers"])

    def raw_score(self, features: pd.DataFrame) -> np.ndarray:
        b = self.bundle
        if self.detector == "hist_gradient_boosting":
            return b["gb"].predict_proba(features[self.feature_columns])[:, 1]
        cusum = cusum_array(features["innov_ratio"].to_numpy(), b["cusum_k"])
        if self.detector == "baseline_cusum":
            return cusum
        return np.maximum(b["iso_cusum"].predict(cusum), b["iso_gb"].predict(b["gb"].predict_proba(features[self.feature_columns])[:, 1]))

    def confidence(self, features: pd.DataFrame) -> np.ndarray:
        return self.bundle["final_iso"].predict(self.raw_score(features))


def main(argv: Optional[list[str]] = None) -> int:
    import time
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-dir", type=Path, default=ARTIFACT_DIR / "dataset")
    parser.add_argument("--artifact-dir", type=Path, default=ARTIFACT_DIR)
    parser.add_argument("--profile", choices=["large", "legacy"], default="large", help="large: 1250 flights, 60/20/20 (about 1 minute to generate); legacy: the original 160-flight set")
    parser.add_argument("--flights-per-cell", type=int, default=None)
    parser.add_argument("--seed", type=int, default=20261113)
    parser.add_argument("--no-test", action="store_true", help="development run: never score the test split")
    parser.add_argument("--no-long-windows", action="store_true", help="skip the 90/120 s residual-window feature trial")
    parser.add_argument("--budget", type=float, default=FALSE_ALARM_BUDGET_PER_HOUR, help="false alarms per hour allowed on validation")
    args = parser.parse_args(argv)
    started = time.time()
    manifest = generate_dataset(args.dataset_dir, args.seed, args.flights_per_cell, args.profile)
    generated = time.time()
    print("dataset:", args.profile, json.dumps(manifest["splits"]), "flights", manifest["total_flights"], "rows", manifest["total_rows"], f"generated in {generated - started:.0f} s")
    metrics = train_pipeline(args.dataset_dir, args.artifact_dir, args.seed, evaluate_test=not args.no_test, budget=args.budget, try_long_windows=not args.no_long_windows)
    print(f"trained in {time.time() - generated:.0f} s")
    for name in ("baseline_cusum", "hist_gradient_boosting", "combined_max"):
        for split in ("validation", "test"):
            part = metrics["models"][name].get(split)
            if part:
                print(name, split, json.dumps({k: v for k, v in part.items() if not k.startswith("by_") and k != "hyperparameter_trials"}))
    print("trial", json.dumps({k: v for k, v in metrics["feature_set_trial"].items() if k in ("triggered", "adopted", "chosen_feature_set", "validation_recall_of_selected_v2")}))
    print("selected", metrics["selected_model"])
    print("tiers", metrics["tiers"]["thresholds"], "model_id", metrics["model_id"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
