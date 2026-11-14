"""Leakage-conscious feature extraction for normalized recorded GPS/IMU imports."""
from __future__ import annotations

from typing import Any

import numpy as np

from app.research.sensor_fusion import geodetic_to_enu


def extract_recorded_features(bundle: dict[str, Any]) -> dict[str, Any]:
    """Derive ENU kinematics and inertial magnitudes; return labels separately.

    Each session has its own first-fix local origin. No truth/attack label is read
    while constructing features. GPS velocity and acceleration are finite-
    difference estimates and are unavailable at the first one/two epochs.
    """
    samples = bundle.get("samples")
    if not isinstance(samples, list) or not samples:
        raise ValueError("bundle must contain aligned recorded samples")
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in samples:
        session = str(row.get("session_id", ""))
        if not session or "timestamp_s" not in row or "gps" not in row or "imu" not in row:
            raise ValueError("each sample requires session_id, timestamp_s, gps, and imu")
        grouped.setdefault(session, []).append(row)
    features = []
    origins: dict[str, dict[str, float]] = {}
    for session, rows in sorted(grouped.items()):
        rows.sort(key=lambda row: float(row["timestamp_s"]))
        first = rows[0]
        gps0 = first["gps"]
        origin = {"latitude_deg": float(gps0["latitude_deg"]),
                  "longitude_deg": float(gps0["longitude_deg"]), "altitude_m": float(gps0["altitude_m"])}
        origins[session] = origin
        previous_position: np.ndarray | None = None
        previous_velocity: np.ndarray | None = None
        previous_time: float | None = None
        for row in rows:
            timestamp = float(row["timestamp_s"])
            gps, imu = row["gps"], row["imu"]
            position = geodetic_to_enu(float(gps["latitude_deg"]), float(gps["longitude_deg"]),
                                       float(gps["altitude_m"]), origin_latitude_deg=origin["latitude_deg"],
                                       origin_longitude_deg=origin["longitude_deg"], origin_altitude_m=origin["altitude_m"])
            if previous_time is not None and timestamp <= previous_time:
                raise ValueError("sample timestamps must increase within each session")
            velocity = None if previous_position is None else (position - previous_position) / (timestamp - previous_time)
            gps_acceleration = None if velocity is None or previous_velocity is None else (velocity - previous_velocity) / (timestamp - previous_time)
            acceleration = np.asarray([imu[key] for key in ("accel_x_m_s2", "accel_y_m_s2", "accel_z_m_s2")], dtype=float)
            gyro = np.asarray([imu[key] for key in ("gyro_x_rad_s", "gyro_y_rad_s", "gyro_z_rad_s")], dtype=float)
            if not np.isfinite(position).all() or not np.isfinite(acceleration).all() or not np.isfinite(gyro).all():
                raise ValueError("sample features must be finite")
            features.append({"session_id": session, "timestamp_s": timestamp,
                             "gps_position_enu_m": position.tolist(),
                             "gps_velocity_enu_m_s": None if velocity is None else velocity.tolist(),
                             "gps_speed_m_s": None if velocity is None else float(np.linalg.norm(velocity)),
                             "gps_acceleration_enu_m_s2": None if gps_acceleration is None else gps_acceleration.tolist(),
                             "imu_acceleration_body_m_s2": acceleration.tolist(),
                             "imu_acceleration_norm_m_s2": float(np.linalg.norm(acceleration)),
                             "imu_gyro_body_rad_s": gyro.tolist(),
                             "imu_gyro_norm_rad_s": float(np.linalg.norm(gyro))})
            previous_position, previous_velocity, previous_time = position, velocity, timestamp
    targets = [{"session_id": row["session_id"], "timestamp_s": row["timestamp_s"], "label": row["label"]}
               for row in bundle.get("labels", [])]
    return {"features": features, "targets": targets, "origins": origins,
            "feature_schema": "recorded-gps-imu-enu-v1",
            "label_separation": "targets returned separately; labels are not consulted during feature extraction"}
