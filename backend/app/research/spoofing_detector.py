"""Read-only, evidence-based GNSS consistency detector for offline research.

Input coordinates are local ENU metres; accelerations are world ENU m/s^2.
"""
from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass
from typing import Any

import numpy as np

DETECTOR_VERSION = "enu-gps-imu-consistency-v1"


@dataclass(frozen=True)
class SensorObservation:
    timestamp_s: float
    gps_position_enu_m: tuple[float, float, float] | None
    imu_acceleration_enu_m_s2: tuple[float, float, float] | None
    gps_velocity_enu_m_s: tuple[float, float, float] | None = None
    gps_accuracy_m: float | None = None
    satellite_count: int | None = None
    fix_quality: str | None = None
    gps_course_rad: float | None = None
    imu_heading_rad: float | None = None


class GpsImuConsistencyDetector:
    """Uses kinematic residuals plus persistence; it does not estimate probability."""

    def __init__(self, *, history_size: int = 8, max_gap_s: float = 2.0,
                 position_jump_m: float = 8.0, velocity_residual_m_s: float = 3.0,
                 acceleration_residual_m_s2: float = 4.0, poor_accuracy_m: float = 8.0,
                 heading_residual_rad: float = 0.7):
        if history_size < 3 or max_gap_s <= 0:
            raise ValueError("history_size must be >= 3 and max_gap_s positive")
        thresholds = (position_jump_m, velocity_residual_m_s, acceleration_residual_m_s2,
                      poor_accuracy_m, heading_residual_rad)
        if not all(math.isfinite(value) and value > 0 for value in thresholds):
            raise ValueError("detector thresholds must be positive and finite")
        self.history_size = history_size
        self.max_gap_s = max_gap_s
        self.position_jump_m = position_jump_m
        self.velocity_residual_m_s = velocity_residual_m_s
        self.acceleration_residual_m_s2 = acceleration_residual_m_s2
        self.poor_accuracy_m = poor_accuracy_m
        self.heading_residual_rad = heading_residual_rad
        self._history: deque[SensorObservation] = deque(maxlen=history_size)
        self._persistence = 0

    def update(self, observation: SensorObservation) -> dict[str, Any]:
        timestamp = float(observation.timestamp_s)
        if not math.isfinite(timestamp):
            raise ValueError("timestamp_s must be finite")
        if self._history and timestamp <= self._history[-1].timestamp_s:
            return self._result(observation, "INSUFFICIENT_DATA", [], ["out_of_order_timestamp"], 0)
        for name, vector in (("gps_position_enu_m", observation.gps_position_enu_m),
                             ("imu_acceleration_enu_m_s2", observation.imu_acceleration_enu_m_s2),
                             ("gps_velocity_enu_m_s", observation.gps_velocity_enu_m_s)):
            if vector is not None and (len(vector) != 3 or not np.isfinite(np.asarray(vector, dtype=float)).all()):
                raise ValueError(f"{name} must be three finite values")
        if observation.gps_accuracy_m is not None and (not math.isfinite(observation.gps_accuracy_m) or observation.gps_accuracy_m < 0):
            raise ValueError("gps_accuracy_m must be finite and non-negative")
        for angle in (observation.gps_course_rad, observation.imu_heading_rad):
            if angle is not None and not math.isfinite(angle):
                raise ValueError("course and heading must be finite radians")

        evidence: list[dict[str, Any]] = []
        alternatives: list[str] = []
        prev = self._history[-1] if self._history else None
        dt = timestamp - prev.timestamp_s if prev else None
        if observation.gps_position_enu_m is None:
            alternatives.append("gps_unavailable")
        elif prev and prev.gps_position_enu_m is not None and dt is not None and dt <= self.max_gap_s:
            displacement = np.asarray(observation.gps_position_enu_m) - np.asarray(prev.gps_position_enu_m)
            derived_velocity = displacement / dt
            distance = float(np.linalg.norm(displacement))
            if distance > self.position_jump_m:
                evidence.append({"code": "position_discontinuity", "value": distance, "threshold": self.position_jump_m, "unit": "m"})
            if observation.gps_velocity_enu_m_s is not None:
                residual = float(np.linalg.norm(np.asarray(observation.gps_velocity_enu_m_s) - derived_velocity))
                if residual > self.velocity_residual_m_s:
                    evidence.append({"code": "gps_velocity_position_residual", "value": residual, "threshold": self.velocity_residual_m_s, "unit": "m/s"})
            if (prev.gps_velocity_enu_m_s is not None and observation.gps_velocity_enu_m_s is not None
                    and prev.imu_acceleration_enu_m_s2 is not None):
                gps_acceleration = (np.asarray(observation.gps_velocity_enu_m_s)
                                    - np.asarray(prev.gps_velocity_enu_m_s)) / dt
                residual = float(np.linalg.norm(gps_acceleration - np.asarray(prev.imu_acceleration_enu_m_s2)))
                if residual > self.acceleration_residual_m_s2:
                    evidence.append({"code": "gps_imu_acceleration_residual", "value": residual, "threshold": self.acceleration_residual_m_s2, "unit": "m/s^2"})
            elif prev.imu_acceleration_enu_m_s2 is not None and observation.gps_velocity_enu_m_s is not None:
                # First velocity observation has no GPS-derived acceleration yet.
                pass
                if residual > self.velocity_residual_m_s:
                    evidence.append({"code": "gps_imu_velocity_disagreement", "value": residual, "threshold": self.velocity_residual_m_s, "unit": "m/s"})
        elif prev and dt is not None and dt > self.max_gap_s:
            alternatives.append("observation_gap_exceeded")
        if observation.gps_accuracy_m is not None and observation.gps_accuracy_m > self.poor_accuracy_m:
            alternatives.append("poor_gnss_accuracy_possible_degradation_or_multipath")
        if observation.satellite_count is not None and observation.satellite_count < 5:
            alternatives.append("low_satellite_count_possible_degradation")
        if observation.fix_quality is not None and observation.fix_quality.lower() in ("none", "invalid", "no_fix"):
            alternatives.append("invalid_gnss_fix")
        if observation.gps_course_rad is not None and observation.imu_heading_rad is not None:
            heading_error = abs((observation.gps_course_rad - observation.imu_heading_rad + math.pi) % (2 * math.pi) - math.pi)
            if heading_error > self.heading_residual_rad:
                evidence.append({"code": "gps_course_imu_heading_residual", "value": heading_error,
                                 "threshold": self.heading_residual_rad, "unit": "rad"})

        self._persistence = self._persistence + 1 if len(evidence) >= 2 else (self._persistence + 1 if evidence else 0)
        if observation.gps_position_enu_m is not None or observation.imu_acceleration_enu_m_s2 is not None:
            self._history.append(observation)
        if not self._history or len(self._history) < 2:
            status = "INSUFFICIENT_DATA"
        elif not evidence and alternatives:
            status = "INSUFFICIENT_DATA"
        elif self._persistence >= 3 and len(evidence) >= 2 and not alternatives:
            status = "LIKELY_GNSS_SPOOFING"
        elif evidence:
            status = "ANOMALOUS" if len(evidence) >= 2 else "SUSPICIOUS"
        else:
            status = "NORMAL"
        return self._result(observation, status, evidence, alternatives, self._persistence)

    @staticmethod
    def _result(observation: SensorObservation, status: str, evidence: list[dict[str, Any]],
                alternatives: list[str], persistence: int) -> dict[str, Any]:
        return {"status": status, "anomaly_score": len(evidence), "confidence": None,
                "timestamp_s": float(observation.timestamp_s), "evidence": evidence,
                "alternative_explanations": alternatives, "measurements": {
                    "gps_position_enu_m": observation.gps_position_enu_m,
                    "gps_velocity_enu_m_s": observation.gps_velocity_enu_m_s,
                    "imu_acceleration_enu_m_s2": observation.imu_acceleration_enu_m_s2,
                    "gps_accuracy_m": observation.gps_accuracy_m,
                    "satellite_count": observation.satellite_count,
                    "fix_quality": observation.fix_quality,
                    "gps_course_rad": observation.gps_course_rad,
                    "imu_heading_rad": observation.imu_heading_rad},
                "persistence_samples": persistence, "detector_version": DETECTOR_VERSION,
                "model_version": None,
                "limitations": "Research consistency evidence only; not calibrated, not a spoof probability, and not a flight-control signal."}
