"""Reported-GPS-only feature extraction shared by training and runtime."""

from __future__ import annotations

import math
from typing import Dict, Tuple


FEATURE_COLUMNS_V1 = (
    "gps_step_speed",
    "gps_speed_change",
    "gps_turn_change_radians",
    "trajectory_residual",
    "trajectory_score",
)
FEATURE_COLUMNS = (*FEATURE_COLUMNS_V1, "gps_velocity_disagreement")


def _angle_change(previous: Tuple[float, float], current: Tuple[float, float]) -> float:
    previous_length = math.hypot(*previous)
    current_length = math.hypot(*current)
    if previous_length < 1e-9 or current_length < 1e-9:
        return 0.0
    cross = previous[0] * current[1] - previous[1] * current[0]
    dot = previous[0] * current[0] + previous[1] * current[1]
    return abs(math.atan2(cross, dot))


class GpsFeatureExtractor:
    """Compute the exact feature contract from successive reported GPS fixes."""

    def __init__(self) -> None:
        self._previous: Dict[str, Tuple[Tuple[float, float], float, Tuple[float, float]]] = {}

    def reset(self) -> None:
        self._previous.clear()

    def update(
        self,
        uav_id: str,
        gps: Tuple[float, float],
        trajectory_residual: float,
        threshold: float,
        dt: float,
        reported_gps_velocity: Tuple[float, float] | None = None,
    ) -> Dict[str, float]:
        velocity = reported_gps_velocity
        velocity_values = velocity or (0.0, 0.0)
        if not all(math.isfinite(float(value)) for value in (*gps, *velocity_values, trajectory_residual, threshold, dt)):
            raise ValueError("GPS feature inputs must be finite")
        if threshold <= 0 or dt <= 0:
            raise ValueError("GPS feature threshold and timestep must be positive")
        current = (float(gps[0]), float(gps[1]))
        previous = self._previous.get(uav_id)
        if previous is None:
            delta = (0.0, 0.0)
            speed = 0.0
            speed_change = 0.0
            turn_change = 0.0
        else:
            old_gps, old_speed, old_delta = previous
            delta = (current[0] - old_gps[0], current[1] - old_gps[1])
            speed = math.hypot(*delta) / dt
            speed_change = (speed - old_speed) / dt
            turn_change = _angle_change(old_delta, delta)
        self._previous[uav_id] = (current, speed, delta)
        positional_velocity = (delta[0] / dt, delta[1] / dt)
        velocity_disagreement = 0.0 if velocity is None else math.hypot(
            float(velocity[0]) - positional_velocity[0],
            float(velocity[1]) - positional_velocity[1],
        )
        return {
            "gps_step_speed": speed,
            "gps_speed_change": speed_change,
            "gps_turn_change_radians": turn_change,
            "trajectory_residual": float(trajectory_residual),
            "trajectory_score": float(trajectory_residual) / max(float(threshold), 1e-9),
            "gps_velocity_disagreement": velocity_disagreement,
        }
