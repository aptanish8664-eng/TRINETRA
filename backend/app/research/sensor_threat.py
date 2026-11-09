"""Explainable offline threat labels for the synthetic sensor research pipeline."""

from __future__ import annotations

from typing import Any, Mapping

from app.research.model import severity_level


def assess_sensor_sample(
    features: Mapping[str, Any],
    *,
    model_score: float,
    decision_threshold: float,
) -> dict[str, Any]:
    """Combine EKF residual evidence and an ML score without live-control effects."""
    if not 0 <= model_score <= 1 or not 0 <= decision_threshold <= 1:
        raise ValueError("sensor model score and threshold must be between zero and one")
    gps_expected_missing = bool(features["gps_expected"]) and not bool(features["gps_available"])
    gps_age = float(features["gps_source_age_s"])
    gps_innovation = float(features["gps_innovation_norm_m"])
    gps_mahalanobis = float(features["gps_mahalanobis_squared"])
    gps_rejected = bool(features["gps_measurement_rejected"])
    accel_residual = float(features["accelerometer_gravity_residual_m_s2"])
    gyro_norm = float(features["gyroscope_norm_rad_s"])
    imu_missing = bool(features["imu_missing"])

    evidence: list[str] = []
    gps_anomaly = (
        gps_expected_missing
        or gps_age > 0
        or gps_innovation > 3.0
        or gps_mahalanobis > 11.345
        or gps_rejected
    )
    imu_anomaly = (
        imu_missing
        or accel_residual > 0.8
        or gyro_norm > 0.30
    )
    if gps_expected_missing:
        evidence.append("GPS was expected but unavailable")
    if gps_age > 0:
        evidence.append(f"GPS source age {gps_age:.3f}s")
    if gps_innovation > 3.0 or gps_mahalanobis > 11.345:
        evidence.append("GPS innovation exceeded the research gate")
    if gps_rejected:
        evidence.append("EKF rejected the GPS measurement")
    if imu_missing:
        evidence.append("IMU sample missing")
    if accel_residual > 0.8:
        evidence.append(f"accelerometer gravity-norm residual {accel_residual:.3f}m/s^2")
    if gyro_norm > 0.30:
        evidence.append(f"gyroscope norm {gyro_norm:.3f}rad/s")

    detected = model_score >= decision_threshold
    if detected and gps_anomaly and imu_anomaly:
        classification = "COMBINED_SENSOR_ANOMALY"
    elif detected and gps_anomaly:
        classification = "GPS_ANOMALY"
    elif detected and imu_anomaly:
        classification = "IMU_ANOMALY"
    elif detected:
        classification = "ENSEMBLE_SENSOR_ANOMALY"
        evidence.append("random-forest score exceeded its validation threshold")
    else:
        classification = "NORMAL"
    severity = int(round(model_score * 100)) if detected else 0
    return {
        "detected": detected,
        "classification": classification,
        "severity_percent": severity,
        "severity_level": severity_level(severity),
        "model_score": float(model_score),
        "decision_threshold": float(decision_threshold),
        "evidence": evidence,
    }
