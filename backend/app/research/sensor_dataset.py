"""Deterministic, truth-labelled ENU sensor scenarios for software-only research."""

from __future__ import annotations

import csv
import hashlib
import json
import math
import platform
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np

from app.research.sensor_fusion import (
    GRAVITY_M_S2,
    AHRS_CORRECTION_RATE,
    AHRS_GYRO_BIAS_RATE,
    EKF_ACCELERATION_NOISE_M_S2,
    EKF_BIAS_RANDOM_WALK_M_S3,
    EKF_INITIAL_ACCELEROMETER_BIAS_VARIANCE,
    GPS_GATE_CHI_SQUARE_99_DF3,
    GPS_MAX_ABSOLUTE_INNOVATION_M,
    GPS_REACQUIRE_MAX_STEP_M,
    GPS_REACQUIRE_MIN_CONSISTENT_FIXES,
    GPS_REACQUIRE_MIN_GAP_S,
    SimulationSensorFusion,
)


SENSOR_FEATURE_COLUMNS = (
    "gps_available",
    "gps_expected",
    "gps_source_age_s",
    "gps_innovation_norm_m",
    "gps_mahalanobis_squared",
    "accelerometer_gravity_residual_m_s2",
    "gyroscope_norm_rad_s",
    "magnetometer_norm_gauss",
    "imu_missing",
    "ekf_covariance_trace",
    "gps_measurement_rejected",
)
INTEGER_COLUMNS = {
    "seed",
    "fault_active",
    "fault_recovery",
    "gps_available",
    "gps_expected",
    "imu_missing",
    "gps_measurement_rejected",
}

SENSOR_SCENARIO_TYPES = (
    "normal",
    "gps_jump",
    "gps_drift",
    "gps_outage",
    "accelerometer_bias",
    "gyroscope_bias",
    "imu_noise",
    "imu_spikes",
    "missing_measurements",
    "delayed_measurements",
    "gps_imu_disagreement",
    "combined_faults",
    "recovery",
)

IDENTITY_MANIFEST_KEYS = (
    "schema_version",
    "simulation_backend",
    "estimator",
    "coordinate_frame",
    "label_definition",
    "feature_columns",
    "scenario_types",
    "scenario_parameters",
    "generator_version",
    "numpy_version",
    "python_version",
    "seeds",
    "split_seeds",
    "duration_s",
    "sample_rate_hz",
)


def _canonical_hash(manifest: Mapping[str, Any], samples: Sequence[Mapping[str, Any]]) -> str:
    identity = {key: manifest[key] for key in IDENTITY_MANIFEST_KEYS}
    payload = json.dumps(
        {"manifest": identity, "samples": samples},
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _split_seeds(seeds: Sequence[int]) -> dict[str, list[int]]:
    ordered = list(sorted(seeds))
    train_end = max(1, int(len(ordered) * 0.6))
    validation_end = max(train_end + 1, int(len(ordered) * 0.8))
    validation_end = min(validation_end, len(ordered) - 1)
    return {
        "train": ordered[:train_end],
        "validation": ordered[train_end:validation_end],
        "test": ordered[validation_end:],
    }


def _scenario_fault(
    scenario: str,
    timestamp_s: float,
    start_s: float,
    end_s: float,
    sample_index: int,
) -> tuple[bool, bool]:
    active = start_s <= timestamp_s < end_s
    recovered = scenario == "recovery" and timestamp_s >= end_s
    if scenario == "imu_spikes" and active:
        active = sample_index % 10 == 0
    return active, recovered


def _truth(timestamp_s: float) -> tuple[np.ndarray, np.ndarray, np.ndarray, float]:
    x = 0.12 * timestamp_s + 3.0 * math.sin(0.04 * timestamp_s)
    y = 0.12 * timestamp_s + 2.0 * math.sin(0.03 * timestamp_s)
    vx = 0.12 + 0.12 * math.cos(0.04 * timestamp_s)
    vy = 0.12 + 0.06 * math.cos(0.03 * timestamp_s)
    ax = -0.0048 * math.sin(0.04 * timestamp_s)
    ay = -0.0018 * math.sin(0.03 * timestamp_s)
    yaw = math.atan2(vy, vx)
    return np.asarray((x, y, 0.0)), np.asarray((vx, vy, 0.0)), np.asarray((ax, ay, 0.0)), yaw


def _simulate_scenario(
    seed: int,
    split: str,
    scenario: str,
    *,
    duration_s: float,
    sample_rate_hz: float,
) -> list[dict[str, Any]]:
    sample_count = int(round(duration_s * sample_rate_hz)) + 1
    dt = 1.0 / sample_rate_hz
    start_s, end_s = duration_s * 0.25, duration_s * 0.70
    rng = np.random.default_rng(seed * 1009 + SENSOR_SCENARIO_TYPES.index(scenario) * 9176)
    fusion = SimulationSensorFusion()
    truth_positions = [_truth(index * dt)[0] for index in range(sample_count)]
    previous_yaw: float | None = None
    rows: list[dict[str, Any]] = []

    for index in range(sample_count):
        timestamp_s = index * dt
        position, velocity, acceleration, yaw = _truth(timestamp_s)
        fault_active, recovery_active = _scenario_fault(
            scenario, timestamp_s, start_s, end_s, index,
        )
        yaw_rate = 0.0 if previous_yaw is None else math.atan2(
            math.sin(yaw - previous_yaw), math.cos(yaw - previous_yaw),
        ) / dt
        previous_yaw = yaw

        measured_acceleration = np.asarray((
            math.cos(yaw) * acceleration[0] + math.sin(yaw) * acceleration[1],
            -math.sin(yaw) * acceleration[0] + math.cos(yaw) * acceleration[1],
            GRAVITY_M_S2,
        ))
        measured_gyro = np.asarray((0.0, 0.0, yaw_rate))
        measured_magnetic = np.asarray((math.cos(yaw), -math.sin(yaw), 0.0))

        if scenario in ("accelerometer_bias", "combined_faults") and fault_active:
            measured_acceleration += np.asarray((0.22, -0.14, 0.08))
        if scenario in ("gyroscope_bias", "combined_faults") and fault_active:
            measured_gyro += np.asarray((0.01, -0.015, 0.035))
        noise_scale = 1.0
        if scenario in ("imu_noise", "combined_faults") and fault_active:
            noise_scale = 5.0
        measured_acceleration += rng.normal(0.0, 0.025 * noise_scale, 3)
        measured_gyro += rng.normal(0.0, 0.002 * noise_scale, 3)
        measured_magnetic += rng.normal(0.0, 0.004 * noise_scale, 3)
        if scenario == "imu_spikes" and fault_active:
            measured_acceleration += np.asarray((2.5, -2.0, 1.5))
            measured_gyro += np.asarray((0.3, -0.2, 0.4))

        imu_missing = scenario == "missing_measurements" and fault_active and index % 8 == 0
        if imu_missing:
            acceleration_sample = gyro_sample = magnetic_sample = None
        else:
            acceleration_sample = measured_acceleration.tolist()
            gyro_sample = measured_gyro.tolist()
            magnetic_sample = measured_magnetic.tolist()

        gps_available = index % max(1, int(round(sample_rate_hz))) == 0
        gps_expected = gps_available
        gps_source_time = timestamp_s
        gps_measurement = None
        gps_position_error_m = None
        if scenario == "gps_outage" and fault_active:
            gps_available = False
        if scenario == "missing_measurements" and fault_active and index % 11 == 0:
            gps_available = False
        if scenario == "delayed_measurements" and gps_available and index > 0:
            source_index = max(0, index - int(round(sample_rate_hz)))
            gps_source_time = source_index * dt
            gps_truth = truth_positions[source_index]
        else:
            gps_truth = position
        if gps_available:
            gps_measurement = gps_truth + rng.normal(0.0, 0.45, 3)
            if scenario in ("gps_jump", "gps_imu_disagreement") and fault_active:
                gps_measurement += np.asarray((8.0, -5.0, 0.0))
            if scenario == "gps_drift" and fault_active:
                fraction = (timestamp_s - start_s) / max(end_s - start_s, dt)
                gps_measurement += np.asarray((4.0, -2.0, 0.0)) * fraction
            if scenario in ("combined_faults", "recovery") and fault_active:
                gps_measurement += np.asarray((6.0, 2.5, 0.0))
            if scenario == "recovery" and recovery_active:
                gps_measurement = position + rng.normal(0.0, 0.45, 3)
            gps_position_error_m = float(np.linalg.norm(gps_measurement - position))

        estimate = fusion.update(
            timestamp_s=timestamp_s,
            acceleration_body_m_s2=acceleration_sample,
            gyro_body_rad_s=gyro_sample,
            magnetic_body_gauss=magnetic_sample,
            gps_position_enu_m=None if gps_measurement is None else gps_measurement.tolist(),
            gps_source_timestamp_s=None if gps_measurement is None else gps_source_time,
        )
        navigation = estimate.navigation
        position_estimate = None if navigation is None else navigation["position_enu_m"]
        position_error = None if position_estimate is None else float(
            np.linalg.norm(np.asarray(position_estimate) - position)
        )
        gps_innovation = None if navigation is None else navigation["innovation_m"]
        gps_mahalanobis = None if navigation is None else navigation["innovation_mahalanobis_squared"]
        gps_accepted = False if navigation is None else bool(navigation["gps_measurement_used"])
        accel_norm = 0.0 if acceleration_sample is None else float(np.linalg.norm(acceleration_sample))
        gyro_norm = 0.0 if gyro_sample is None else float(np.linalg.norm(gyro_sample))
        magnetic_norm = 0.0 if magnetic_sample is None else float(np.linalg.norm(magnetic_sample))
        covariance_trace = 0.0 if navigation is None else float(navigation["covariance_trace"])
        innovation_norm = 0.0 if not gps_available or gps_innovation is None else float(
            np.linalg.norm(gps_innovation)
        )
        mahalanobis_feature = 0.0 if not gps_available or gps_mahalanobis is None else float(gps_mahalanobis)
        rejected = int(gps_available and not gps_accepted)

        row: dict[str, Any] = {
            "seed": seed,
            "split": split,
            "scenario_id": f"seed-{seed:06d}-{scenario}",
            "scenario_type": scenario,
            "uav_id": "SIM-UAV-01",
            "timestamp_s": round(timestamp_s, 6),
            "fault_active": int(fault_active),
            "fault_type": scenario if fault_active else "none",
            "fault_recovery": int(recovery_active),
            "truth_x_m": float(position[0]),
            "truth_y_m": float(position[1]),
            "truth_z_m": float(position[2]),
            "truth_vx_m_s": float(velocity[0]),
            "truth_vy_m_s": float(velocity[1]),
            "truth_yaw_rad": float(yaw),
            "gps_x_m": None if gps_measurement is None else float(gps_measurement[0]),
            "gps_y_m": None if gps_measurement is None else float(gps_measurement[1]),
            "gps_z_m": None if gps_measurement is None else float(gps_measurement[2]),
            "gps_source_timestamp_s": None if gps_measurement is None else float(gps_source_time),
            "gps_position_error_m": gps_position_error_m,
            "estimated_position_error_m": position_error,
            "estimated_x_m": None if position_estimate is None else float(position_estimate[0]),
            "estimated_y_m": None if position_estimate is None else float(position_estimate[1]),
            "estimated_z_m": None if position_estimate is None else float(position_estimate[2]),
            "estimated_roll_rad": float(estimate.attitude["euler_rad"][0]),
            "estimated_pitch_rad": float(estimate.attitude["euler_rad"][1]),
            "estimated_yaw_rad": float(estimate.attitude["euler_rad"][2]),
            "estimated_quaternion_w": float(estimate.attitude["quaternion_body_to_world"][0]),
            "estimated_quaternion_x": float(estimate.attitude["quaternion_body_to_world"][1]),
            "estimated_quaternion_y": float(estimate.attitude["quaternion_body_to_world"][2]),
            "estimated_quaternion_z": float(estimate.attitude["quaternion_body_to_world"][3]),
            "estimated_accelerometer_bias_x_m_s2": 0.0 if navigation is None else float(navigation["accelerometer_bias_enu_m_s2"][0]),
            "estimated_accelerometer_bias_y_m_s2": 0.0 if navigation is None else float(navigation["accelerometer_bias_enu_m_s2"][1]),
            "estimated_accelerometer_bias_z_m_s2": 0.0 if navigation is None else float(navigation["accelerometer_bias_enu_m_s2"][2]),
            "acceleration_body_x_m_s2": None if acceleration_sample is None else float(acceleration_sample[0]),
            "acceleration_body_y_m_s2": None if acceleration_sample is None else float(acceleration_sample[1]),
            "acceleration_body_z_m_s2": None if acceleration_sample is None else float(acceleration_sample[2]),
            "gyro_body_x_rad_s": None if gyro_sample is None else float(gyro_sample[0]),
            "gyro_body_y_rad_s": None if gyro_sample is None else float(gyro_sample[1]),
            "gyro_body_z_rad_s": None if gyro_sample is None else float(gyro_sample[2]),
            "magnetic_body_x_gauss": None if magnetic_sample is None else float(magnetic_sample[0]),
            "magnetic_body_y_gauss": None if magnetic_sample is None else float(magnetic_sample[1]),
            "magnetic_body_z_gauss": None if magnetic_sample is None else float(magnetic_sample[2]),
            "gps_available": int(gps_available),
            "gps_expected": int(gps_expected),
            "gps_source_age_s": round(timestamp_s - gps_source_time, 6) if gps_available else 0.0,
            "gps_innovation_norm_m": innovation_norm,
            "gps_mahalanobis_squared": mahalanobis_feature,
            "accelerometer_gravity_residual_m_s2": abs(accel_norm - GRAVITY_M_S2) if acceleration_sample is not None else 0.0,
            "gyroscope_norm_rad_s": gyro_norm,
            "magnetometer_norm_gauss": magnetic_norm,
            "imu_missing": int(imu_missing),
            "ekf_covariance_trace": covariance_trace,
            "gps_measurement_rejected": rejected,
        }
        rows.append(row)
    return rows


def generate_sensor_dataset(
    seeds: Sequence[int],
    *,
    duration_s: float = 20.0,
    sample_rate_hz: float = 10.0,
) -> dict[str, Any]:
    """Generate repeatable multi-fault runs with seed-group splits and truth labels."""
    normalized_seeds = [int(seed) for seed in seeds]
    if len(normalized_seeds) < 5 or len(set(normalized_seeds)) != len(normalized_seeds):
        raise ValueError("at least five distinct seeds are required for grouped train/validation/test splits")
    if any(seed < 0 for seed in normalized_seeds):
        raise ValueError("seeds must be non-negative integers")
    if not math.isfinite(duration_s) or duration_s < 4:
        raise ValueError("duration_s must be finite and at least four seconds")
    if not math.isfinite(sample_rate_hz) or not 1 <= sample_rate_hz <= 100:
        raise ValueError("sample_rate_hz must be between 1 and 100")
    split_seeds = _split_seeds(normalized_seeds)
    seed_split = {seed: split for split, group in split_seeds.items() for seed in group}
    samples = [
        row
        for seed in sorted(normalized_seeds)
        for scenario in SENSOR_SCENARIO_TYPES
        for row in _simulate_scenario(
            seed, seed_split[seed], scenario,
            duration_s=duration_s, sample_rate_hz=sample_rate_hz,
        )
    ]
    manifest: dict[str, Any] = {
        "schema_version": 1,
        "simulation_backend": "deterministic_software_only_sensor_simulator",
        "estimator": {
            "attitude": "gyro-integrated quaternion complementary filter with accelerometer and magnetometer correction",
            "navigation": "three-dimensional ENU position-velocity-accelerometer-bias EKF",
            "gps_outlier_gate": (
                "99% chi-square innovation gate, 3 degrees of freedom, and "
                f"{GPS_MAX_ABSOLUTE_INNOVATION_M:g} metre absolute residual limit; "
                f"after a GPS gap of {GPS_REACQUIRE_MIN_GAP_S:g} seconds, reinitialize "
                f"after {GPS_REACQUIRE_MIN_CONSISTENT_FIXES} consistent fixes"
            ),
            "parameters": {
                "attitude_correction_rate": AHRS_CORRECTION_RATE,
                "attitude_gyro_bias_rate": AHRS_GYRO_BIAS_RATE,
                "ekf_acceleration_noise_m_s2": EKF_ACCELERATION_NOISE_M_S2,
                "ekf_bias_random_walk_m_s3": EKF_BIAS_RANDOM_WALK_M_S3,
                "ekf_initial_accelerometer_bias_variance": EKF_INITIAL_ACCELEROMETER_BIAS_VARIANCE,
                "gps_gate_chi_square_99_df3": GPS_GATE_CHI_SQUARE_99_DF3,
                "gps_max_absolute_innovation_m": GPS_MAX_ABSOLUTE_INNOVATION_M,
                "gps_reacquire_min_gap_s": GPS_REACQUIRE_MIN_GAP_S,
                "gps_reacquire_min_consistent_fixes": GPS_REACQUIRE_MIN_CONSISTENT_FIXES,
                "gps_reacquire_max_step_m": GPS_REACQUIRE_MAX_STEP_M,
            },
        },
        "coordinate_frame": {
            "world": "synthetic local ENU metres; not geographic coordinates",
            "body": "synthetic co-aligned ENU at zero yaw; quaternion maps body to world",
            "gravity_m_s2": GRAVITY_M_S2,
        },
        "label_definition": "fault_active is the known generated-fault interval; fault_type is none outside it; recovery rows are clean sensor input after the fault interval",
        "feature_columns": list(SENSOR_FEATURE_COLUMNS),
        "scenario_types": list(SENSOR_SCENARIO_TYPES),
        "scenario_parameters": {
            "fault_interval_fraction": [0.25, 0.70],
            "gps_sample_period_s": 1.0,
            "gps_noise_sigma_m": 0.45,
            "gps_jump_offset_m": [8.0, -5.0, 0.0],
            "gps_imu_disagreement_offset_m": [8.0, -5.0, 0.0],
            "gps_drift_terminal_offset_m": [4.0, -2.0, 0.0],
            "combined_and_recovery_gps_offset_m": [6.0, 2.5, 0.0],
            "accelerometer_bias_body_m_s2": [0.22, -0.14, 0.08],
            "gyroscope_bias_body_rad_s": [0.01, -0.015, 0.035],
            "imu_noise_sigma": {
                "acceleration_m_s2": 0.025,
                "gyro_rad_s": 0.002,
                "magnetic_gauss": 0.004,
                "fault_multiplier": 5.0,
            },
            "imu_spike_stride_samples": 10,
            "delayed_measurement_age_s": 1.0,
            "random_generator": "NumPy default_rng with seed*1009 + scenario_index*9176",
        },
        "generator_version": "sensor-fusion-dataset-v1",
        "numpy_version": np.__version__,
        "python_version": platform.python_version(),
        "seeds": sorted(normalized_seeds),
        "split_seeds": split_seeds,
        "duration_s": float(duration_s),
        "sample_rate_hz": float(sample_rate_hz),
    }
    dataset_id = _canonical_hash(manifest, samples)
    manifest.update({
        "dataset_id": dataset_id,
        "content_sha256": dataset_id,
        "sample_count": len(samples),
    })
    bundle = {"manifest": manifest, "samples": samples}
    validate_sensor_dataset(bundle)
    return bundle


def validate_sensor_dataset(bundle: Mapping[str, Any]) -> None:
    manifest = bundle.get("manifest")
    samples = bundle.get("samples")
    if not isinstance(manifest, Mapping) or not isinstance(samples, list) or not samples:
        raise ValueError("sensor dataset manifest or samples are missing")
    if manifest.get("schema_version") != 1 or tuple(manifest.get("feature_columns", ())) != SENSOR_FEATURE_COLUMNS:
        raise ValueError("sensor dataset schema is unsupported")
    if manifest.get("sample_count") != len(samples):
        raise ValueError("sensor dataset sample count does not match manifest")
    seeds = set(manifest.get("seeds", ()))
    split_seeds = manifest.get("split_seeds", {})
    groups = [set(split_seeds.get(name, ())) for name in ("train", "validation", "test")]
    if not seeds or set.union(*groups) != seeds or any(
        groups[left] & groups[right]
        for left, right in ((0, 1), (0, 2), (1, 2))
    ):
        raise ValueError("sensor dataset seed splits overlap or do not cover the data")
    scenario_splits: dict[str, set[str]] = {}
    for sample in samples:
        if sample.get("seed") not in seeds or sample.get("uav_id") != "SIM-UAV-01":
            raise ValueError("sensor sample has an invalid seed or UAV identity")
        scenario_splits.setdefault(sample["scenario_id"], set()).add(sample["split"])
        if sample["split"] not in ("train", "validation", "test"):
            raise ValueError("sensor sample split is invalid")
        if sample.get("fault_active") not in (0, 1) or sample.get("fault_recovery") not in (0, 1):
            raise ValueError("sensor sample labels are invalid")
        for feature in SENSOR_FEATURE_COLUMNS:
            value = sample.get(feature)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError(f"sensor feature {feature} must be finite")
    if any(len(splits) != 1 for splits in scenario_splits.values()):
        raise ValueError("sensor scenario appears in multiple data splits")
    for split in ("train", "validation", "test"):
        split_rows = [sample for sample in samples if sample["split"] == split]
        if not split_rows or {sample["fault_active"] for sample in split_rows} != {0, 1}:
            raise ValueError(f"sensor {split} split must contain normal and fault samples")
    if manifest.get("dataset_id") != _canonical_hash(manifest, samples):
        raise ValueError("sensor dataset content hash does not match")


def write_sensor_dataset(bundle: Mapping[str, Any], output_dir: Path) -> tuple[Path, Path]:
    validate_sensor_dataset(bundle)
    target = Path(output_dir)
    if target.exists():
        raise FileExistsError(f"refusing to overwrite existing sensor dataset: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.mkdir()
    samples_path = target / "samples.csv"
    manifest_path = target / "manifest.json"
    columns = list(bundle["samples"][0])
    with samples_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(bundle["samples"])
    manifest_path.write_text(
        json.dumps(bundle["manifest"], indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    return samples_path, manifest_path


def load_sensor_dataset(dataset_dir: Path) -> dict[str, Any]:
    root = Path(dataset_dir)
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    with (root / "samples.csv").open("r", newline="", encoding="utf-8") as handle:
        samples: list[dict[str, Any]] = []
        for row in csv.DictReader(handle):
            sample: dict[str, Any] = {}
            for key, value in row.items():
                if key in ("scenario_id", "scenario_type", "uav_id", "fault_type", "split"):
                    sample[key] = value
                elif value == "":
                    sample[key] = None
                else:
                    number = float(value)
                    sample[key] = int(number) if number.is_integer() and key in INTEGER_COLUMNS else number
            samples.append(sample)
    bundle = {"manifest": manifest, "samples": samples}
    validate_sensor_dataset(bundle)
    if root.name != manifest["dataset_id"]:
        raise ValueError("sensor dataset directory name does not match its content hash")
    return bundle
