"""Live detector inputs: put raw PX4 streams on the detector's sample grid.

No feature code lives here. The aligned window is handed to `threat_data.compute_features`, the same function the
offline pipeline uses. What this module adds is only what the recorded-flight trial showed a live feed needs:

* PX4-clock timestamps (the SITL clock does not run at wall-clock speed), never receive times;
* interval-averaged velocity (a sample's velocity is the mean over the sample interval, matching the position step);
* the GPS delay: GPS lags the estimator velocity by `GPS_DELAY_S` (measured, see below), so the estimator velocity is
  averaged over the interval shifted back by that delay.

A missing or late input is reported by name (`InputUnavailable`); nothing is filled with zeros.
"""
from __future__ import annotations

import math
from typing import Any, Optional, Sequence

import numpy as np

from .threat_data import HZ

# Lag between GPS_RAW_INT and the estimator velocity. Measured on a live SITL baseline (GPS at 10 Hz, real GPS speed/course):
# the lag fit is 0.03-0.06 s for position and velocity, and the baseline stays Low for 0.00-0.08 s. The 0.11 s used in the
# earlier recorded-flight trial (PX4's EKF2_GPS_DELAY default) put this baseline at Medium during hard acceleration.
GPS_DELAY_S = 0.04
MAX_SAMPLES = 600           # the same window the offline evaluation uses (300 s at 2 Hz)
MIN_SAMPLES = 20            # 10 s of aligned data before the first assessment
MAX_GAP_S = {"gps": 1.5, "velocity": 1.0}   # a longer hole restarts the window after the hole
EARTH_RADIUS_M = 6_378_137.0

INPUT_LABELS = {
    "gps_position": "GPS position (GPS_RAW_INT)",
    "gps_velocity": "GPS speed and course (GPS_RAW_INT vel/cog)",
    "estimator_velocity": "Estimator velocity (LOCAL_POSITION_NED)",
    "imu": "IMU (HIGHRES_IMU)",
    "estimator_status": "Estimator status (ESTIMATOR_STATUS)",
    "vibration": "Vibration (VIBRATION)",
    "battery": "Battery (SYS_STATUS / BATTERY_STATUS)",
}
INPUT_MEANINGS = {
    "gps_position": "No fresh GPS fix is arriving, so there is no position to check. No score is shown.",
    "gps_velocity": "The receiver is not reporting speed or course, so GPS velocity cannot be compared with the estimator. No score is shown.",
    "estimator_velocity": "PX4's velocity estimate is not arriving, so GPS cannot be compared with an inertial reference. No score is shown.",
    "imu": "Raw IMU samples are not arriving. The score is still computed, but IMU data quality cannot be reported.",
    "estimator_status": "PX4's estimator health report is not arriving. The score is still computed, without the estimator's own test ratios.",
    "vibration": "PX4's vibration report is not arriving. The score is still computed, without the vibration check.",
    "battery": "No battery reading. The score is still computed; mission impact assumes a full battery.",
}
REQUIRED_INPUTS = ("gps_position", "gps_velocity", "estimator_velocity")
OPTIONAL_INPUTS = ("imu", "estimator_status", "vibration", "battery")


class InputUnavailable(Exception):
    def __init__(self, name: str, detail: str):
        super().__init__(f"{name}: {detail}")
        self.name, self.detail = name, detail


def _clean(t: np.ndarray, *columns: np.ndarray) -> tuple[np.ndarray, ...]:
    """Strictly increasing, finite samples only."""
    keep = np.isfinite(t)
    for column in columns:
        keep &= np.isfinite(column)
    t, columns = t[keep], tuple(column[keep] for column in columns)
    if len(t) > 1:
        order = np.concatenate(([True], np.diff(t) > 0))
        while not order.all():                     # drop repeats/out-of-order stamps until the clock is monotonic
            t, columns = t[order], tuple(column[order] for column in columns)
            order = np.concatenate(([True], np.diff(t) > 0)) if len(t) > 1 else np.array([True])
    return (t, *columns)


def _tail_start(t: np.ndarray, max_gap_s: float) -> tuple[float, bool]:
    """Start time of the last stretch without a hole longer than max_gap_s."""
    holes = np.flatnonzero(np.diff(t) > max_gap_s)
    return (float(t[holes[-1] + 1]), True) if len(holes) else (float(t[0]), False)


def _interval_mean(t: np.ndarray, values: np.ndarray, start: np.ndarray, end: np.ndarray) -> np.ndarray:
    """Mean of a sampled signal over [start, end] from its trapezoid integral (interval-averaged velocity)."""
    integral = np.concatenate(([0.0], np.cumsum(0.5 * (values[1:] + values[:-1]) * np.diff(t))))
    return (np.interp(end, t, integral) - np.interp(start, t, integral)) / (end - start)


def align_window(gps: Sequence[Sequence[float]], velocity: Sequence[Sequence[float]], *, flight_start_t: float,
                 origin: tuple[float, float, float], hz: float = HZ, delay_s: float = GPS_DELAY_S,
                 max_samples: int = MAX_SAMPLES, min_samples: int = MIN_SAMPLES) -> dict[str, Any]:
    """Aligned detector window from raw streams.

    gps rows: (px4_time_s, latitude, longitude, altitude_m, north_m_s, east_m_s) from GPS_RAW_INT.
    velocity rows: (px4_time_s, north_m_s, east_m_s, down_m_s) from LOCAL_POSITION_NED.
    Returns t (seconds since flight_start_t), gps_position / gps_velocity / imu_velocity as ENU arrays, and notes.
    """
    if len(gps) < 3:
        raise InputUnavailable("gps_position", "fewer than three GPS samples since arming")
    if len(velocity) < 3:
        raise InputUnavailable("estimator_velocity", "fewer than three estimator velocity samples since arming")
    g = np.asarray(gps, dtype=float)
    v = np.asarray(velocity, dtype=float)
    if not np.isfinite(g[:, 4:6]).any():
        raise InputUnavailable("gps_velocity", "the receiver reports no speed or course")
    gt, lat, lon, alt, gvn, gve = _clean(g[:, 0], g[:, 1], g[:, 2], g[:, 3], g[:, 4], g[:, 5])
    vt, vn, ve, vd = _clean(v[:, 0], v[:, 1], v[:, 2], v[:, 3])
    if len(gt) < 3:
        raise InputUnavailable("gps_velocity", "too few GPS samples carry speed and course")
    if len(vt) < 3:
        raise InputUnavailable("estimator_velocity", "too few valid estimator velocity samples")
    dt = 1.0 / hz
    gps_start, gps_hole = _tail_start(gt, MAX_GAP_S["gps"])
    vel_start, vel_hole = _tail_start(vt, MAX_GAP_S["velocity"])
    first = max(gps_start + dt, vel_start + delay_s + dt)      # every sample needs one full interval behind it
    last = min(float(gt[-1]), float(vt[-1]) + delay_s)
    k_first = int(math.ceil((first - flight_start_t) / dt - 1e-9))
    k_last = int(math.floor((last - flight_start_t) / dt + 1e-9))
    count = k_last - k_first + 1
    if count < min_samples:
        return {"ready": False, "samples": max(count, 0), "needed": min_samples, "seconds_collected": max(count, 0) * dt,
                "seconds_needed": min_samples * dt, "restarted_after_gap": [name for name, hole in (("gps_position", gps_hole), ("estimator_velocity", vel_hole)) if hole]}
    k = np.arange(max(k_first, k_last - max_samples + 1), k_last + 1)
    grid = flight_start_t + k * dt
    lat0, lon0, alt0 = origin
    east = np.radians(lon - lon0) * EARTH_RADIUS_M * math.cos(math.radians(lat0))
    north = np.radians(lat - lat0) * EARTH_RADIUS_M
    gps_position = np.column_stack([np.interp(grid, gt, east), np.interp(grid, gt, north), np.interp(grid, gt, alt - alt0)])
    gps_velocity = np.column_stack([_interval_mean(gt, gve, grid - dt, grid), _interval_mean(gt, gvn, grid - dt, grid), np.zeros(len(grid))])
    shifted = grid - delay_s
    imu_velocity = np.column_stack([_interval_mean(vt, ve, shifted - dt, shifted), _interval_mean(vt, vn, shifted - dt, shifted),
                                    -_interval_mean(vt, vd, shifted - dt, shifted)])
    return {"ready": True, "t": grid - flight_start_t, "gps_position": gps_position, "gps_velocity": gps_velocity, "imu_velocity": imu_velocity,
            "px4_time_s": float(grid[-1]), "samples": int(len(grid)), "hz": hz, "gps_delay_s": delay_s,
            "restarted_after_gap": [name for name, hole in (("gps_position", gps_hole), ("estimator_velocity", vel_hole)) if hole],
            "window_starts_at_flight_start": bool(k[0] * dt <= 5.0)}


def input_message(name: str, detail: Optional[str] = None) -> dict[str, str]:
    return {"input": name, "title": INPUT_LABELS[name], "meaning": INPUT_MEANINGS[name], "detail": detail or "no recent message",
            "required": name in REQUIRED_INPUTS}
