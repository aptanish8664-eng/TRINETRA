"""Grouped-split model comparison, research figures, and critical analysis."""

from __future__ import annotations

import html
import csv
import json
import math
import platform
from pathlib import Path
from typing import Any, Mapping, Sequence

import joblib
import numpy as np
import pandas as pd
import sklearn
from sklearn.ensemble import GradientBoostingClassifier, IsolationForest, RandomForestClassifier
from sklearn.metrics import confusion_matrix, f1_score, precision_score, recall_score, roc_auc_score

from app.research.sensor_dataset import SENSOR_FEATURE_COLUMNS, validate_sensor_dataset
from app.research.sensor_threat import assess_sensor_sample


MODEL_PARAMETERS = {
    "isolation_forest": {
        "n_estimators": 160,
        "max_samples": "auto",
        "contamination": "auto",
        "random_state": 0,
        "n_jobs": 1,
        "fit_rows": "fault-free training rows only",
    },
    "random_forest": {
        "n_estimators": 160,
        "max_depth": 12,
        "min_samples_leaf": 3,
        "class_weight": "balanced_subsample",
        "random_state": 0,
        "n_jobs": 1,
    },
    "gradient_boosting": {
        "n_estimators": 120,
        "learning_rate": 0.06,
        "max_depth": 2,
        "min_samples_leaf": 6,
        "random_state": 0,
    },
}
VALIDATION_FALSE_POSITIVE_RATE_LIMIT = 0.05


def _matrix(rows: Sequence[Mapping[str, Any]]) -> np.ndarray:
    matrix = np.asarray([[float(row[name]) for name in SENSOR_FEATURE_COLUMNS] for row in rows], dtype=float)
    if not np.isfinite(matrix).all():
        raise ValueError("sensor model input contains a non-finite value")
    return matrix


def _labels(rows: Sequence[Mapping[str, Any]]) -> np.ndarray:
    return np.asarray([int(row["fault_active"]) for row in rows], dtype=int)


def _select_threshold(actual: np.ndarray, scores: np.ndarray) -> float:
    candidates = np.unique(np.r_[
        0.0,
        scores,
        1.0,
        np.nextafter(float(np.max(scores)), math.inf),
    ])
    best: tuple[float, float, float, float, float] | None = None
    for threshold in candidates:
        predicted = (scores >= threshold).astype(int)
        tn, fp, _, _ = confusion_matrix(actual, predicted, labels=[0, 1]).ravel()
        false_positive_rate = float(fp / (fp + tn)) if fp + tn else 0.0
        if false_positive_rate > VALIDATION_FALSE_POSITIVE_RATE_LIMIT:
            continue
        candidate = (
            float(recall_score(actual, predicted, zero_division=0)),
            float(f1_score(actual, predicted, zero_division=0)),
            float(precision_score(actual, predicted, zero_division=0)),
            -abs(float(threshold) - 0.5),
            float(threshold),
        )
        if best is None or candidate > best:
            best = candidate
    if best is None:
        raise ValueError("validation threshold could not be selected")
    return best[4]


def _detection_latency(rows: Sequence[Mapping[str, Any]], predicted: np.ndarray) -> dict[str, Any]:
    grouped: dict[str, list[tuple[int, Mapping[str, Any]]]] = {}
    for index, row in enumerate(rows):
        grouped.setdefault(str(row["scenario_id"]), []).append((index, row))
    delays: list[float] = []
    eligible = 0
    for scenario_rows in grouped.values():
        active = [row for _, row in scenario_rows if row["fault_active"]]
        if not active:
            continue
        eligible += 1
        onset = min(float(row["timestamp_s"]) for row in active)
        detections = [
            (index, row) for index, row in scenario_rows
            if float(row["timestamp_s"]) >= onset and predicted[index] == 1
        ]
        if detections:
            detected_at = min(float(row["timestamp_s"]) for _, row in detections)
            delays.append(max(0.0, detected_at - onset))
    return {
        "fault_scenarios": eligible,
        "detected_scenarios": len(delays),
        "scenario_detection_rate": len(delays) / eligible if eligible else None,
        "mean_detection_latency_s": float(np.mean(delays)) if delays else None,
        "median_detection_latency_s": float(np.median(delays)) if delays else None,
        "latency_scope": "first predicted-positive sample at or after generated fault onset",
    }


def _metrics(
    actual: np.ndarray,
    scores: np.ndarray,
    threshold: float,
    rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    predicted = (scores >= threshold).astype(int)
    tn, fp, fn, tp = confusion_matrix(actual, predicted, labels=[0, 1]).ravel()
    return {
        "sample_count": int(len(actual)),
        "fault_count": int(actual.sum()),
        "threshold": float(threshold),
        "precision": float(precision_score(actual, predicted, zero_division=0)),
        "recall": float(recall_score(actual, predicted, zero_division=0)),
        "f1": float(f1_score(actual, predicted, zero_division=0)),
        "false_positive_rate": float(fp / (fp + tn)) if fp + tn else 0.0,
        "roc_auc": float(roc_auc_score(actual, scores)),
        "confusion_matrix_labels_0_1": [[int(tn), int(fp)], [int(fn), int(tp)]],
        "detection_latency": _detection_latency(rows, predicted),
    }


def _estimation_metrics(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    def summarize(group: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        estimated = [row for row in group if row["estimated_position_error_m"] is not None]
        gps_rows = [row for row in group if row["gps_position_error_m"] is not None]
        position_errors = [float(row["estimated_position_error_m"]) for row in estimated]
        horizontal_errors = [
            math.hypot(
                float(row["estimated_x_m"]) - float(row["truth_x_m"]),
                float(row["estimated_y_m"]) - float(row["truth_y_m"]),
            )
            for row in estimated
        ]
        yaw_errors = [
            math.atan2(
                math.sin(float(row["estimated_yaw_rad"]) - float(row["truth_yaw_rad"])),
                math.cos(float(row["estimated_yaw_rad"]) - float(row["truth_yaw_rad"])),
            )
            for row in estimated
        ]
        return {
            "sample_count": len(estimated),
            "gps_sample_count": len(gps_rows),
            "ekf_position_rmse_3d_m": float(np.sqrt(np.mean(np.square(position_errors)))),
            "ekf_position_rmse_horizontal_m": float(np.sqrt(np.mean(np.square(horizontal_errors)))),
            "raw_gps_position_rmse_3d_m": float(np.sqrt(np.mean(np.square([
                float(row["gps_position_error_m"]) for row in gps_rows
            ])))) if gps_rows else None,
            "attitude_yaw_rmse_rad": float(np.sqrt(np.mean(np.square(yaw_errors)))),
        }

    scenarios = sorted({str(row["scenario_type"]) for row in rows})
    return {
        **summarize(rows),
        "per_scenario_type": {
            scenario: summarize([row for row in rows if row["scenario_type"] == scenario])
            for scenario in scenarios
        },
        "truth_source": "generated simulator ground truth; excluded from model input features",
    }


def _per_scenario_metrics(
    rows: Sequence[Mapping[str, Any]],
    scores: np.ndarray,
    threshold: float,
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    scenario_types = sorted({str(row["scenario_type"]) for row in rows} - {"normal"})
    for scenario_type in scenario_types:
        indices = [index for index, row in enumerate(rows) if row["scenario_type"] == scenario_type]
        scenario_rows = [rows[index] for index in indices]
        labels = _labels(scenario_rows)
        if set(labels.tolist()) != {0, 1}:
            continue
        result[scenario_type] = _metrics(
            labels,
            scores[indices],
            threshold,
            scenario_rows,
        )
    return result


def _threshold_score(rows: Sequence[Mapping[str, Any]]) -> np.ndarray:
    result = []
    for row in rows:
        gps_score = max(0.0, float(row["gps_mahalanobis_squared"])) / 11.345
        acceleration_score = max(0.0, float(row["accelerometer_gravity_residual_m_s2"])) / 0.8
        gyro_score = max(0.0, float(row["gyroscope_norm_rad_s"])) / 0.30
        expected_but_missing = int(row["gps_expected"]) and not int(row["gps_available"])
        outage_score = 2.0 if expected_but_missing else 0.0
        delay_score = max(0.0, float(row["gps_source_age_s"])) / 0.25
        missing_imu_score = 2.0 if int(row["imu_missing"]) else 0.0
        result.append(max(
            gps_score,
            acceleration_score,
            gyro_score,
            outage_score,
            delay_score,
            missing_imu_score,
        ))
    return np.asarray(result, dtype=float)


def _svg_correlation(correlation: pd.DataFrame, output_path: Path) -> None:
    labels = list(correlation.columns)
    cell = 54
    margin = 180
    width = margin + len(labels) * cell + 24
    height = margin + len(labels) * cell + 24
    fragments = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#ffffff"/>',
        '<text x="12" y="24" font-family="sans-serif" font-size="16">Pearson correlation of model input features</text>',
    ]
    correlation_values = correlation.to_numpy(dtype=np.float64)
    for row_index, row_name in enumerate(labels):
        fragments.append(
            f'<text x="{margin - 8}" y="{margin + row_index * cell + 30}" '
            'text-anchor="end" font-family="sans-serif" font-size="10">'
            f'{html.escape(row_name)}</text>'
        )
        for column_index, _ in enumerate(labels):
            value = float(correlation_values[row_index, column_index])
            red = int(255 * max(0.0, value))
            blue = int(255 * max(0.0, -value))
            green = int(245 - 150 * abs(value))
            x = margin + column_index * cell
            y = margin + row_index * cell
            fragments.append(
                f'<rect x="{x}" y="{y}" width="{cell - 2}" height="{cell - 2}" '
                f'fill="rgb({red},{green},{blue})"/>'
            )
            fragments.append(
                f'<text x="{x + cell / 2}" y="{y + 31}" text-anchor="middle" '
                f'font-family="sans-serif" font-size="11">{value:.2f}</text>'
            )
    for column_index, label in enumerate(labels):
        x = margin + column_index * cell + 27
        y = margin - 10
        fragments.append(
            f'<text x="{x}" y="{y}" transform="rotate(-55 {x} {y})" '
            'font-family="sans-serif" font-size="10">'
            f'{html.escape(label)}</text>'
        )
    fragments.append("</svg>")
    output_path.write_text("\n".join(fragments) + "\n", encoding="utf-8")


def _svg_timeseries(rows: Sequence[Mapping[str, Any]], output_path: Path) -> None:
    width, height = 1000, 480
    left, top, plot_width, plot_height = 72, 54, 880, 350
    series = (
        ("gps_position_error_m", "GPS position error", "#d04a35"),
        ("estimated_position_error_m", "EKF position error", "#255bb5"),
        ("gps_innovation_norm_m", "GPS innovation norm", "#23824a"),
    )
    usable = [row for row in rows if row["timestamp_s"] is not None]
    if not usable:
        raise ValueError("selected EDA scenario has no timestamped samples")
    times = [float(row["timestamp_s"]) for row in usable]
    t_min, t_max = min(times), max(times)
    fragments = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="white"/>',
        '<text x="72" y="26" font-family="sans-serif" font-size="17">Generated scenario: sensor and estimator residuals</text>',
    ]
    values_by_series: dict[str, list[float]] = {}
    maximum = 1.0
    for key, _, _ in series:
        values = [max(0.0, float(row[key] or 0.0)) for row in usable]
        values_by_series[key] = values
        maximum = max(maximum, max(values, default=0.0))
    maximum *= 1.08
    for grid in range(6):
        y = top + grid * plot_height / 5
        value = maximum * (1 - grid / 5)
        fragments.append(f'<line x1="{left}" y1="{y:.1f}" x2="{left + plot_width}" y2="{y:.1f}" stroke="#dddddd"/>')
        fragments.append(
            f'<text x="{left - 8}" y="{y + 4:.1f}" text-anchor="end" '
            f'font-family="sans-serif" font-size="11">{value:.1f}</text>'
        )
    fragments.append(f'<line x1="{left}" y1="{top + plot_height}" x2="{left + plot_width}" y2="{top + plot_height}" stroke="#444"/>')
    for key, label, color in series:
        points = []
        values = values_by_series[key]
        for time, value in zip(times, values):
            x = left + (time - t_min) / max(t_max - t_min, 1e-9) * plot_width
            y = top + (1 - value / maximum) * plot_height
            points.append(f"{x:.1f},{y:.1f}")
        fragments.append(f'<polyline points="{" ".join(points)}" fill="none" stroke="{color}" stroke-width="2"/>')
    for index, (key, label, color) in enumerate(series):
        x = left + index * 250
        fragments.append(f'<line x1="{x}" y1="444" x2="{x + 24}" y2="444" stroke="{color}" stroke-width="3"/>')
        fragments.append(
            f'<text x="{x + 30}" y="448" font-family="sans-serif" font-size="12">'
            f'{html.escape(label)}</text>'
        )
    fragments.append("</svg>")
    output_path.write_text("\n".join(fragments) + "\n", encoding="utf-8")


def _critical_analysis(
    metrics: Mapping[str, Any],
    dataset_id: str,
    state_estimation_metrics: Mapping[str, Any],
) -> str:
    lines = [
        "# Synthetic Sensor Fusion and Fault-Detection Research Analysis",
        "",
        f"- Dataset: `{dataset_id}`.",
        "- Evidence source: deterministic software-generated local ENU trajectories and injected sensor faults.",
        "- This report is not flight evidence and does not validate PX4, Gazebo sensor frames, hardware noise, or field generalization.",
        "",
        "## Held-out results",
        "",
        "| Model | Precision | Recall | F1 | False-positive rate | ROC-AUC | Mean detection latency (s) | Scenario detection rate |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for name, result in metrics.items():
        test = result["test_metrics"]
        latency = test["detection_latency"]
        mean_latency = latency["mean_detection_latency_s"]
        rate = latency["scenario_detection_rate"]
        lines.append(
            f"| {name} | {test['precision']:.3f} | {test['recall']:.3f} | "
            f"{test['f1']:.3f} | {test['false_positive_rate']:.3f} | "
            f"{test['roc_auc']:.3f} | "
            f"{'n/a' if mean_latency is None else f'{mean_latency:.3f}'} | "
            f"{'n/a' if rate is None else f'{rate:.3f}'} |"
        )
    lines.extend([
        "",
        "## Fault-class results",
        "",
        "Per-class test metrics by generated scenario family are stored in `model-comparison.json`. The table below lists each classifier's held-out F1 by scenario; the normal profile has no positive labels and is reported through false-positive rate instead.",
        "",
        "| Model | " + " | ".join(sorted(next(iter(metrics.values()))["per_scenario_test_metrics"])) + " |",
        "|---|" + "---:|" * len(next(iter(metrics.values()))["per_scenario_test_metrics"]),
    ])
    scenario_names = sorted(next(iter(metrics.values()))["per_scenario_test_metrics"])
    for name, result in metrics.items():
        per_scenario = result["per_scenario_test_metrics"]
        lines.append(
            "| " + name + " | "
            + " | ".join(
                f'{per_scenario[scenario]["f1"]:.3f}'
                for scenario in scenario_names
            )
            + " |"
        )
    lines.extend([
        "",
        "## Estimator error against generated ground truth",
        "",
        "| Metric | Held-out test result |",
        "|---|---:|",
    ])
    for name, value in state_estimation_metrics.items():
        if name == "per_scenario_type":
            continue
        rendered = (
            str(value) if isinstance(value, int)
            else f"{value:.4f}" if isinstance(value, float)
            else str(value)
        )
        lines.append(f"| {name} | {rendered} |")
    lines.extend([
        "",
        "### Estimator error by generated profile",
        "",
        "| Scenario | EKF 3D RMSE (m) | Horizontal RMSE (m) | Raw GPS RMSE (m) | Yaw RMSE (rad) |",
        "|---|---:|---:|---:|---:|",
    ])
    for scenario, values in state_estimation_metrics["per_scenario_type"].items():
        gps_rmse = values["raw_gps_position_rmse_3d_m"]
        lines.append(
            f"| {scenario} | {values['ekf_position_rmse_3d_m']:.3f} | "
            f"{values['ekf_position_rmse_horizontal_m']:.3f} | "
            f"{'n/a' if gps_rmse is None else f'{gps_rmse:.3f}'} | "
            f"{values['attitude_yaw_rmse_rad']:.4f} |"
        )
    lines.extend([
        "",
        "All model decision thresholds were chosen on the validation seed group to maximize recall subject to a 5% validation false-positive-rate cap; a held-out test false-positive rate above 5% indicates seed-to-seed instability rather than a threshold adjustment using test labels.",
    ])
    lines.extend([
        "",
        "Thresholds were selected on the validation seed group, not on the held-out test group. The isolation forest was fitted only on fault-free training rows. Random forest and gradient boosting used all training rows and their binary generated-fault labels.",
        "",
        "## Critical interpretation and limitations",
        "",
        "- The simulated truth and fault labels are exact by construction. Real sensor timing, vibration, temperature, magnetic interference, GPS multipath, airframe dynamics, and human labeling uncertainty are not represented.",
        "- Seed groups are disjoint, preventing the same generated run from appearing across splits. However, each split contains the same scripted scenario families and the same trajectory/noise generator; this measures within-generator generalization, not unseen attack or environmental generalization.",
        "- GPS jumps/outages and obvious IMU spikes may be easier than slowly varying bias. Report per-class results and inspect the generated time series before drawing aggregate conclusions.",
        "- The threshold detector is a transparent composite rule. Tree ensembles can exploit simulator-specific correlations; high scores would not establish operational reliability.",
        "- Detection latency is the first positive sample at or after the programmed fault onset. It excludes transport, buffering, operator response, and physical recovery time.",
        "- The orientation filter and ENU EKF are research implementations using synthetic synchronized measurements. Delayed GPS samples are rejected as out-of-sequence; no fixed-lag reprocessing is implemented.",
        "- GPS reacquisition after a prolonged outage waits for three mutually consistent fixes before resetting local position; this is a simulator policy, not a validated anti-spoofing decision.",
        "- Position state, covariance, and estimated accelerometer bias are exposed for analysis. The estimator is not connected to the live PX4 or threat-response paths, and no autonomous vehicle command is generated.",
        "- `threat-assessments.csv` combines the random-forest score with EKF/GPS/IMU evidence for offline review. Its score-to-severity mapping is an uncalibrated research heuristic and is not the authenticated live threat pipeline.",
        "- Before PX4 integration, validate MAVLink/Gazebo axes, units, timestamps, lever arms, sensor bias/noise, GPS altitude semantics, and estimator output against recorded SITL ground truth. Never treat abstract ENU values as latitude/longitude.",
        "",
        "## Artifacts",
        "",
        "- `model-comparison.json`: model parameters, validation thresholds, seed groups, and held-out metrics.",
        "- `correlation-matrix.csv` and `correlation-matrix.svg`: feature relationships; correlations are not causal evidence.",
        "- `sensor-timeseries.svg`: one generated test fault scenario with GPS, EKF, and innovation residuals.",
        "- `threat-assessments.csv`: explainable offline fusion of test-set sensor residuals and random-forest scores.",
        "- Per-model joblib artifacts contain the fitted estimator and its training metadata; keep them inside the research artifact directory.",
    ])
    return "\n".join(lines) + "\n"


def evaluate_sensor_dataset(
    bundle: Mapping[str, Any],
    output_dir: Path,
) -> dict[str, Any]:
    validate_sensor_dataset(bundle)
    target = Path(output_dir)
    if target.exists():
        raise FileExistsError(f"refusing to overwrite existing sensor evaluation: {target}")
    rows = bundle["samples"]
    split_rows = {
        name: [row for row in rows if row["split"] == name]
        for name in ("train", "validation", "test")
    }
    for split, samples in split_rows.items():
        if not samples or {int(row["fault_active"]) for row in samples} != {0, 1}:
            raise ValueError(f"{split} split must contain both generated-fault classes")
    train_x = _matrix(split_rows["train"])
    validation_x = _matrix(split_rows["validation"])
    test_x = _matrix(split_rows["test"])
    validation_y = _labels(split_rows["validation"])
    test_y = _labels(split_rows["test"])
    target.mkdir(parents=True, exist_ok=False)

    results: dict[str, Any] = {}
    validation_threshold_scores = _threshold_score(split_rows["validation"])
    test_threshold_scores = _threshold_score(split_rows["test"])
    threshold = _select_threshold(validation_y, validation_threshold_scores)
    results["threshold_based"] = {
        "parameters": {
            "score": "maximum of GPS normalized innovation, accelerometer gravity residual, gyro norm, expected-GPS outage, delayed GPS age, and missing-IMU component scores",
            "threshold_selection": "maximize validation recall subject to validation false-positive rate <= 0.05",
        },
        "validation_threshold": threshold,
        "validation_metrics": _metrics(
            validation_y, validation_threshold_scores, threshold, split_rows["validation"],
        ),
        "test_metrics": _metrics(test_y, test_threshold_scores, threshold, split_rows["test"]),
        "per_scenario_test_metrics": _per_scenario_metrics(
            split_rows["test"], test_threshold_scores, threshold,
        ),
    }

    training_clean = np.asarray([row["fault_active"] == 0 for row in split_rows["train"]])
    isolation = IsolationForest(**{
        key: value for key, value in MODEL_PARAMETERS["isolation_forest"].items()
        if key != "fit_rows"
    })
    isolation.fit(train_x[training_clean])
    isolation_scores = {
        "validation": -isolation.score_samples(validation_x),
        "test": -isolation.score_samples(test_x),
    }
    threshold = _select_threshold(validation_y, isolation_scores["validation"])
    results["isolation_forest"] = {
        "parameters": MODEL_PARAMETERS["isolation_forest"],
        "validation_threshold": threshold,
        "validation_metrics": _metrics(
            validation_y, isolation_scores["validation"], threshold, split_rows["validation"],
        ),
        "test_metrics": _metrics(test_y, isolation_scores["test"], threshold, split_rows["test"]),
        "per_scenario_test_metrics": _per_scenario_metrics(
            split_rows["test"], isolation_scores["test"], threshold,
        ),
    }

    random_forest_scores: np.ndarray | None = None
    supervised_estimators = {
        "random_forest": RandomForestClassifier(**MODEL_PARAMETERS["random_forest"]),
        "gradient_boosting": GradientBoostingClassifier(**MODEL_PARAMETERS["gradient_boosting"]),
    }
    for name, estimator in supervised_estimators.items():
        estimator.fit(train_x, _labels(split_rows["train"]))
        validation_scores = estimator.predict_proba(validation_x)[:, 1]
        test_scores = estimator.predict_proba(test_x)[:, 1]
        if name == "random_forest":
            random_forest_scores = test_scores
        threshold = _select_threshold(validation_y, validation_scores)
        results[name] = {
            "parameters": MODEL_PARAMETERS[name],
            "validation_threshold": threshold,
            "validation_metrics": _metrics(
                validation_y, validation_scores, threshold, split_rows["validation"],
            ),
            "test_metrics": _metrics(test_y, test_scores, threshold, split_rows["test"]),
            "per_scenario_test_metrics": _per_scenario_metrics(
                split_rows["test"], test_scores, threshold,
            ),
        }
        joblib.dump(
            {
                "estimator": estimator,
                "feature_columns": list(SENSOR_FEATURE_COLUMNS),
                "dataset_id": bundle["manifest"]["dataset_id"],
                "training_seed_groups": bundle["manifest"]["split_seeds"],
                "parameters": MODEL_PARAMETERS[name],
                "validation_threshold": threshold,
                "test_metrics": results[name]["test_metrics"],
                "python": platform.python_version(),
                "scikit_learn": sklearn.__version__,
            },
            target / f"{name}.joblib",
        )
    joblib.dump(
        {
            "estimator": isolation,
            "feature_columns": list(SENSOR_FEATURE_COLUMNS),
            "dataset_id": bundle["manifest"]["dataset_id"],
            "training_seed_groups": bundle["manifest"]["split_seeds"],
            "parameters": MODEL_PARAMETERS["isolation_forest"],
            "validation_threshold": results["isolation_forest"]["validation_threshold"],
            "test_metrics": results["isolation_forest"]["test_metrics"],
            "python": platform.python_version(),
            "scikit_learn": sklearn.__version__,
        },
        target / "isolation_forest.joblib",
    )
    if random_forest_scores is None:
        raise RuntimeError("random-forest threat score was not produced")
    threat_path = target / "threat-assessments.csv"
    with threat_path.open("w", newline="", encoding="utf-8") as handle:
        columns = (
            "scenario_id", "uav_id", "timestamp_s", "generated_fault_label",
            "model_detected", "classification", "severity_percent",
            "severity_level", "model_score", "decision_threshold", "evidence",
        )
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        threshold = results["random_forest"]["validation_threshold"]
        for row, score in zip(split_rows["test"], random_forest_scores):
            assessment = assess_sensor_sample(
                row,
                model_score=float(score),
                decision_threshold=threshold,
            )
            writer.writerow({
                "scenario_id": row["scenario_id"],
                "uav_id": row["uav_id"],
                "timestamp_s": row["timestamp_s"],
                "generated_fault_label": row["fault_active"],
                "model_detected": int(assessment["detected"]),
                "classification": assessment["classification"],
                "severity_percent": assessment["severity_percent"],
                "severity_level": assessment["severity_level"],
                "model_score": assessment["model_score"],
                "decision_threshold": assessment["decision_threshold"],
                "evidence": "; ".join(assessment["evidence"]),
            })

    frame = pd.DataFrame(rows)
    correlation = frame[list(SENSOR_FEATURE_COLUMNS)].corr(method="pearson")
    correlation.to_csv(target / "correlation-matrix.csv", float_format="%.8f")
    _svg_correlation(correlation, target / "correlation-matrix.svg")
    test_scenario = next(
        row["scenario_id"] for row in split_rows["test"]
        if row["scenario_type"] in ("combined_faults", "gps_jump")
    )
    plot_rows = [row for row in split_rows["test"] if row["scenario_id"] == test_scenario]
    _svg_timeseries(plot_rows, target / "sensor-timeseries.svg")

    estimation_metrics = _estimation_metrics(split_rows["test"])
    artifact = {
        "schema_version": 1,
        "dataset_id": bundle["manifest"]["dataset_id"],
        "simulation_backend": bundle["manifest"]["simulation_backend"],
        "feature_columns": list(SENSOR_FEATURE_COLUMNS),
        "split_seeds": bundle["manifest"]["split_seeds"],
        "training_library": {
            "scikit_learn": sklearn.__version__,
            "numpy": np.__version__,
            "pandas": pd.__version__,
            "python": platform.python_version(),
        },
        "model_parameters": MODEL_PARAMETERS,
        "threshold_selection": {
            "objective": "maximize validation recall subject to a validation false-positive-rate limit",
            "validation_false_positive_rate_limit": VALIDATION_FALSE_POSITIVE_RATE_LIMIT,
        },
        "state_estimation_metrics": estimation_metrics,
        "results": results,
    }
    (target / "model-comparison.json").write_text(
        json.dumps(artifact, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    (target / "critical-analysis.md").write_text(
        _critical_analysis(
            results, str(bundle["manifest"]["dataset_id"]), estimation_metrics,
        ),
        encoding="utf-8",
    )
    return artifact
