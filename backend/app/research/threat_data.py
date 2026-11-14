"""Synthetic flights, runtime features and the dataset generator for the offline GPS-spoofing research pipeline.

Everything here is synthetic and software-only. Flights follow patterns from the existing schema-2 route
generators (`app.mission_planning.generators`); GPS/IMU/battery are simulated with seeded noise; attacks come from
the shared schema (`app.research.attacks`). Ground truth is produced separately (`labels`) and never enters features.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Sequence

import numpy as np
import pandas as pd

from ..mission_planning import generators as route_gen
from .attacks import ATTACK_SPECS, ATTACK_TYPES, AttackConfig, AttackTruth, GpsStream, assert_no_ground_truth, inject

HZ = 2.0
DT = 1.0 / HZ
CRUISE_ALTITUDE_M = 30.0
INNOV_S = 9.0            # nominal innovation variance (m^2) used for the normalised innovation ratio
EKF_GAIN = 0.1           # position gain of the EKF-like filter
SCENARIO_TIERS = ("low", "mid", "high")


# --- flights -----------------------------------------------------------------------------------------------

@dataclass
class SimAircraft:
    aircraft_id: str
    role: str
    t: np.ndarray
    true_position: np.ndarray
    gps: GpsStream
    imu_velocity: np.ndarray
    battery_pct: np.ndarray
    truth: AttackTruth
    total_s: float


@dataclass
class SimFlight:
    flight_id: str
    seed: int
    aircraft: list[SimAircraft]
    meta: dict[str, Any] = field(default_factory=dict)


def _pattern(rng: np.random.Generator) -> tuple[str, list[tuple[float, float]]]:
    kind = str(rng.choice(["waypoint_route", "coverage_survey", "expanding_square_search", "perimeter_patrol"]))
    cap = 200
    if kind == "expanding_square_search":
        pts = route_gen.expanding_square((120.0, 120.0), float(rng.uniform(25, 45)), float(rng.uniform(100, 200)), float(rng.uniform(0, 359)), cap)
    elif kind == "coverage_survey":
        w, h = float(rng.uniform(150, 300)), float(rng.uniform(120, 250))
        pts = route_gen.lawnmower([(0.0, 0.0), (w, 0.0), (w, h), (0.0, h)], float(rng.uniform(30, 55)), float(rng.choice([0.0, 90.0])), cap)
    elif kind == "perimeter_patrol":
        r = float(rng.uniform(80, 160))
        pts = route_gen.perimeter(route_gen.orbit((r, r), r, int(rng.integers(5, 9))), 2, closed=True)
    else:
        count = int(rng.integers(3, 7))
        pts = [(float(rng.uniform(-250, 250)), float(rng.uniform(40, 350))) for _ in range(count)]
    return kind, [(float(e), float(n)) for e, n in pts]


def _along(path: np.ndarray, speed: float, total_s: float) -> np.ndarray:
    seg = np.linalg.norm(np.diff(path, axis=0), axis=1)
    cumulative = np.concatenate([[0.0], np.cumsum(seg)])
    length = cumulative[-1]
    t = np.arange(0.0, total_s, DT)
    distance = (t * speed) % (2 * length) if length > 0 else np.zeros_like(t)
    distance = np.where(distance > length, 2 * length - distance, distance)  # out-and-back if the route is short
    return np.column_stack([np.interp(distance, cumulative, path[:, 0]), np.interp(distance, cumulative, path[:, 1])])


def simulate_aircraft(rng: np.random.Generator, aircraft_id: str, role: str, path_xy: np.ndarray, attacks: Sequence[AttackConfig],
                      seed: int, total_s: float, offset: tuple[float, float] = (0.0, 0.0), speed: Optional[float] = None) -> SimAircraft:
    speed = float(rng.uniform(3.0, 10.0)) if speed is None else float(speed)
    xy = _along(path_xy, speed, total_s) + np.array(offset)
    n = len(xy)
    gust = float(rng.uniform(0.0, 1.2))
    wind = np.zeros((n, 2))
    for i in range(1, n):  # Ornstein-Uhlenbeck gust velocity
        wind[i] = wind[i - 1] * 0.97 + rng.normal(0, gust * 0.25, 2)
    true_pos = np.column_stack([xy + np.cumsum(wind * DT, axis=0), np.full(n, CRUISE_ALTITUDE_M)])
    true_vel = np.gradient(true_pos, DT, axis=0)
    sigma_gps = float(rng.uniform(0.6, 2.0))
    gps_pos = true_pos + rng.normal(0, sigma_gps, true_pos.shape) * np.array([1, 1, 0.5])
    gps_vel = true_vel + rng.normal(0, float(rng.uniform(0.08, 0.25)), true_vel.shape)
    bias = np.cumsum(rng.normal(0, 0.004, (n, 3)), axis=0)
    imu_vel = true_vel + bias + rng.normal(0, float(rng.uniform(0.05, 0.2)), true_vel.shape)
    t = np.arange(n) * DT
    battery = np.clip(float(rng.uniform(65, 100)) - float(rng.uniform(0.015, 0.04)) * t + rng.normal(0, 0.05, n).cumsum() * 0.02, 0, 100)
    injected = inject(GpsStream(t, gps_pos, gps_vel), attacks, seed)
    return SimAircraft(aircraft_id, role, t, true_pos, injected.stream, imu_vel, battery, injected.truth, float(t[-1]))


def simulate_flight(flight_id: str, seed: int, attacks: Sequence[AttackConfig], n_aircraft: int = 1,
                    total_s: Optional[float] = None) -> SimFlight:
    rng = np.random.default_rng(seed)
    kind, pts = _pattern(rng)
    path = np.array([(0.0, 0.0), *pts, (0.0, 0.0)])
    duration = float(total_s or rng.uniform(110, 200))
    aircraft = []
    for index in range(n_aircraft):
        aircraft.append(simulate_aircraft(
            rng, f"px4-{index + 1}", "primary" if index == 0 else "standby", path,
            attacks if index == 0 else [], seed + 101 * index, duration, offset=(25.0 * index, 8.0 * index),
        ))
    return SimFlight(flight_id, seed, aircraft, {"pattern": kind, "duration_s": duration, "n_aircraft": n_aircraft})


# --- runtime features (no ground truth) ----------------------------------------------------------------------

def _sample_period(t: np.ndarray) -> float:
    return float(np.median(np.diff(t))) if len(t) > 1 else DT


def _horizontal(values: np.ndarray) -> np.ndarray:
    return np.linalg.norm(values[:, :2], axis=1)


def residual_series(t: np.ndarray, gps_position: np.ndarray, imu_velocity: np.ndarray, window_s: float) -> np.ndarray:
    """Horizontal distance between GPS and the IMU dead-reckoned position anchored `window_s` seconds earlier."""
    dt = _sample_period(t)
    w = max(1, int(round(window_s / dt)))
    cumulative = np.cumsum(imu_velocity * dt, axis=0)
    index = np.arange(len(t))
    anchor = np.maximum(index - w, 0)
    predicted = gps_position[anchor] + (cumulative - cumulative[anchor])
    return _horizontal(gps_position - predicted)


REF_START_S, REF_END_S, MIN_REF_SAMPLES = 5.0, 30.0, 12
CUSUM_K = 4.0

# Original columns first (the API, severity and evidence use them), then the v2 features.
FEATURE_COLUMNS_V1 = [
    "res_pos_w10", "res_pos_w30", "res_vel", "heading_res_deg", "innov_ratio", "res_rate",
    "res10_mean", "res10_std", "res10_max", "resvel_max5", "speed", "cross_res10", "n_aircraft",
]
FEATURE_COLUMNS = FEATURE_COLUMNS_V1 + [
    "res_pos_w60", "res10_z", "res30_z", "res60_z", "resvel_z", "innov_z", "res30_rate10", "res60_rate10", "res10_accel", "res30_accel",
    "noise_std10", "noise_ratio", "var_ratio", "velpos_inconsist", "velpos_z", "gps_travel_3s", "imu_travel_3s", "freeze_ratio",
    "frozen_run", "cross_res30", "cross_z10", "cusum_innov",
]


# Optional longer-window features (tried as the one targeted improvement for slow drift); not part of the default set.
FEATURE_COLUMNS_LONG = ["res_pos_w90", "res_pos_w120", "res90_z", "res120_z", "res90_rate10", "res120_rate10"]
FEATURE_COLUMNS_ALL = FEATURE_COLUMNS + FEATURE_COLUMNS_LONG
RECOVERY_S = 30.0  # after an attack ends the residual windows still see the GPS return; those rows are neither normal nor false alarms


def recovery_flags(t: np.ndarray, attacked: np.ndarray) -> np.ndarray:
    """Un-attacked samples within RECOVERY_S after the last attacked sample of one aircraft (label-side bookkeeping, never a feature)."""
    attacked = np.asarray(attacked, dtype=bool)
    if not attacked.any():
        return np.zeros(len(t), dtype=bool)
    last = t[np.flatnonzero(attacked)[-1]]
    return (~attacked) & (t > last) & (t <= last + RECOVERY_S)


def cusum_array(values: np.ndarray, k: float) -> np.ndarray:
    out = np.zeros(len(values))
    s = 0.0
    for i, value in enumerate(values):
        s = max(0.0, s + float(value) - k)
        out[i] = s
    return out


def _reference_scale(t: np.ndarray, series: np.ndarray, floor: float) -> np.ndarray:
    """Causal per-flight scale: median of `series` over the flight's own early period (5-30 s); nan until enough samples exist."""
    out = np.full(len(t), np.nan)
    in_ref = (t >= REF_START_S) & (t <= REF_END_S)
    frozen = None
    for i in range(len(t)):
        if t[i] > REF_END_S:
            if frozen is None:
                values = series[in_ref]
                frozen = max(float(np.median(values)), floor) if len(values) >= MIN_REF_SAMPLES else np.nan
            out[i] = frozen
        else:
            values = series[:i + 1][in_ref[:i + 1]]
            out[i] = max(float(np.median(values)), floor) if len(values) >= MIN_REF_SAMPLES else np.nan
    return out


def _ratio(series: np.ndarray, scale: np.ndarray) -> np.ndarray:
    """series / own-flight scale; 1.0 (neutral) while the flight has no reference yet."""
    return np.where(np.isnan(scale), 1.0, series / np.where(np.isnan(scale), 1.0, scale))


def _lagged(values: np.ndarray, lag: int) -> np.ndarray:
    out = np.zeros(len(values))
    if lag < len(values):
        out[lag:] = values[lag:] - values[:-lag]
    return out


def compute_features(t: np.ndarray, gps_position: np.ndarray, gps_velocity: np.ndarray, imu_velocity: np.ndarray,
                     others_res10: Optional[np.ndarray] = None, n_aircraft: int = 1, others_res30: Optional[np.ndarray] = None,
                     include_long: bool = False) -> pd.DataFrame:
    """Per-sample, causal features from raw sensor streams only (sample i uses samples <= i).

    `others_res10/30` are the median 10 s / 30 s residuals of the other aircraft. Features normalised by a flight's
    own early residual scale assume the window starts at the beginning of the flight.
    """
    assert len(t) == len(gps_position) == len(gps_velocity) == len(imu_velocity)
    dt = _sample_period(t)
    n = len(t)
    res10 = residual_series(t, gps_position, imu_velocity, 10.0)
    res30 = residual_series(t, gps_position, imu_velocity, 30.0)
    res60 = residual_series(t, gps_position, imu_velocity, 60.0)
    res_vel = _horizontal(gps_velocity - imu_velocity)
    gv, iv = gps_velocity[:, :2], imu_velocity[:, :2]
    denominator = np.linalg.norm(gv, axis=1) * np.linalg.norm(iv, axis=1)
    cosine = np.where(denominator > 0.25, np.sum(gv * iv, axis=1) / np.maximum(denominator, 1e-9), 1.0)
    heading = np.degrees(np.arccos(np.clip(cosine, -1, 1)))
    estimate = gps_position[0].copy()
    innovation = np.zeros(n)
    for i in range(n):
        predicted = estimate + imu_velocity[i] * dt if i else estimate
        diff = gps_position[i] - predicted
        innovation[i] = math.hypot(diff[0], diff[1])
        estimate = predicted + EKF_GAIN * diff
    innov_ratio = innovation ** 2 / INNOV_S
    lag = max(1, int(round(2.0 / dt)))
    rate = np.zeros(n)
    rate[lag:] = (res10[lag:] - res10[:-lag]) / (lag * dt)
    series = pd.Series(res10)
    w10 = max(2, int(round(10.0 / dt)))

    # step innovation: per-step GPS displacement minus the IMU-predicted displacement (mean-removed rolling spread = noise level)
    step = np.zeros((n, 2))
    step[1:] = (gps_position[1:, :2] - gps_position[:-1, :2]) - imu_velocity[1:, :2] * dt
    sx, sy = pd.Series(step[:, 0]), pd.Series(step[:, 1])
    noise_std10 = np.sqrt(sx.rolling(w10, min_periods=4).var().fillna(0.0) + sy.rolling(w10, min_periods=4).var().fillna(0.0)).to_numpy()
    w_short, w_long = max(3, int(round(6.0 / dt))), max(6, int(round(20.0 / dt)))
    var_short = sx.rolling(w_short, min_periods=3).var().fillna(0.0) + sy.rolling(w_short, min_periods=3).var().fillna(0.0)
    var_long = (sx.rolling(w_long, min_periods=w_short).var() + sy.rolling(w_long, min_periods=w_short).var()).shift(w_short)
    var_ratio = (var_short / (var_long + 1e-3)).where(var_long.notna(), 1.0).clip(upper=1e4).to_numpy()

    # velocity reported by the receiver vs the derivative of its own position
    k4 = max(2, int(round(4.0 / dt)))
    pos_derivative = np.zeros((n, 2))
    pos_derivative[k4:] = (gps_position[k4:, :2] - gps_position[:-k4, :2]) / (k4 * dt)
    mean_reported = pd.DataFrame(gps_velocity[:, :2]).rolling(k4, min_periods=1).mean().to_numpy()
    velpos = np.linalg.norm(mean_reported - pos_derivative, axis=1)
    velpos[:k4] = 0.0

    # freeze: the position barely changes while the IMU says the aircraft is moving
    k3 = max(2, int(round(3.0 / dt)))
    gps_travel = np.zeros(n)
    gps_travel[k3:] = np.linalg.norm(gps_position[k3:, :2] - gps_position[:-k3, :2], axis=1)
    cumulative = np.cumsum(imu_velocity[:, :2] * dt, axis=0)
    imu_travel = np.zeros(n)
    imu_travel[k3:] = np.linalg.norm(cumulative[k3:] - cumulative[:-k3], axis=1)
    freeze_ratio = np.where(imu_travel > 1.0, imu_travel / (gps_travel + 0.5), 1.0)
    unchanged = np.zeros(n)
    unchanged[1:] = np.linalg.norm(gps_position[1:, :2] - gps_position[:-1, :2], axis=1) < 1e-9
    frozen_run = np.zeros(n)
    run = 0.0
    for i in range(n):
        run = min(run + 1.0, 40.0) if unchanged[i] else 0.0
        frozen_run[i] = run

    # growth rate and acceleration of the residuals
    lag10 = max(1, int(round(10.0 / dt)))
    res30_rate10 = _lagged(res30, lag10) / 10.0
    res60_rate10 = _lagged(res60, lag10) / 10.0
    res10_rate10 = _lagged(res10, lag10) / 10.0
    res10_accel = _lagged(res10_rate10, lag10) / 10.0
    res30_accel = _lagged(res30_rate10, lag10) / 10.0

    # per-flight early scales
    res10_scale = _reference_scale(t, res10, 0.5)
    res30_scale = _reference_scale(t, res30, 0.8)
    res60_scale = _reference_scale(t, res60, 1.0)
    vel_scale = _reference_scale(t, res_vel, 0.05)
    innov_scale = _reference_scale(t, innov_ratio, 0.2)
    noise_scale = _reference_scale(t, noise_std10, 0.3)
    velpos_scale = _reference_scale(t, velpos, 0.1)

    r10 = res10 - others_res10 if others_res10 is not None else np.zeros(n)
    r30 = res30 - others_res30 if others_res30 is not None else np.zeros(n)
    long_columns: dict[str, np.ndarray] = {}
    if include_long:
        res90 = residual_series(t, gps_position, imu_velocity, 90.0)
        res120 = residual_series(t, gps_position, imu_velocity, 120.0)
        long_columns = {
            "res_pos_w90": res90, "res_pos_w120": res120,
            "res90_z": _ratio(res90, _reference_scale(t, res90, 1.2)), "res120_z": _ratio(res120, _reference_scale(t, res120, 1.5)),
            "res90_rate10": _lagged(res90, lag10) / 10.0, "res120_rate10": _lagged(res120, lag10) / 10.0,
        }
    frame = pd.DataFrame({
        **long_columns,
        "res_pos_w10": res10, "res_pos_w30": res30, "res_vel": res_vel, "heading_res_deg": heading,
        "innov_ratio": innov_ratio, "res_rate": rate,
        "res10_mean": series.rolling(w10, min_periods=1).mean().to_numpy(),
        "res10_std": series.rolling(w10, min_periods=2).std().fillna(0.0).to_numpy(),
        "res10_max": series.rolling(w10, min_periods=1).max().to_numpy(),
        "resvel_max5": pd.Series(res_vel).rolling(max(1, int(round(5.0 / dt))), min_periods=1).max().to_numpy(),
        "speed": _horizontal(gps_velocity),
        "cross_res10": r10,
        "n_aircraft": np.full(n, float(n_aircraft)),
        "res_pos_w60": res60,
        "res10_z": _ratio(res10, res10_scale), "res30_z": _ratio(res30, res30_scale), "res60_z": _ratio(res60, res60_scale),
        "resvel_z": _ratio(res_vel, vel_scale), "innov_z": _ratio(innov_ratio, innov_scale),
        "res30_rate10": res30_rate10, "res60_rate10": res60_rate10, "res10_accel": res10_accel, "res30_accel": res30_accel,
        "noise_std10": noise_std10, "noise_ratio": _ratio(noise_std10, noise_scale), "var_ratio": var_ratio,
        "velpos_inconsist": velpos, "velpos_z": _ratio(velpos, velpos_scale),
        "gps_travel_3s": gps_travel, "imu_travel_3s": imu_travel, "freeze_ratio": freeze_ratio, "frozen_run": frozen_run,
        "cross_res30": r30, "cross_z10": np.where(np.isnan(res10_scale), 0.0, r10 / np.where(np.isnan(res10_scale), 1.0, res10_scale)),
        "cusum_innov": cusum_array(innov_ratio, CUSUM_K),
    })
    assert_no_ground_truth(frame.columns, "feature frame")
    return frame[FEATURE_COLUMNS_ALL if include_long else FEATURE_COLUMNS].replace([np.inf, -np.inf], 0.0).fillna(0.0)


def flight_features(flight: SimFlight) -> list[pd.DataFrame]:
    """Features for every aircraft in a flight; cross-aircraft consistency uses the other aircraft's residual."""
    res10 = [residual_series(a.t, a.gps.position, a.imu_velocity, 10.0) for a in flight.aircraft]
    res30 = [residual_series(a.t, a.gps.position, a.imu_velocity, 30.0) for a in flight.aircraft]
    frames = []
    for index, aircraft in enumerate(flight.aircraft):
        others = [r for j, r in enumerate(res10) if j != index]
        others30 = [r for j, r in enumerate(res30) if j != index]
        frames.append(compute_features(aircraft.t, aircraft.gps.position, aircraft.gps.velocity, aircraft.imu_velocity,
                                       np.median(np.vstack(others), axis=0) if others else None, len(flight.aircraft),
                                       np.median(np.vstack(others30), axis=0) if others30 else None, include_long=True))
    return frames


# --- dataset ---------------------------------------------------------------------------------------------------

def _tier_magnitude(attack_type: str, tier: str) -> float:
    spec = ATTACK_SPECS[attack_type].magnitude
    if attack_type == "freeze":
        return 0.0
    lo, hi = spec.minimum, spec.maximum
    # Log-spaced tiers inside the configured range; "low" stays near the minimum (hard cases live there).
    fraction = {"low": 0.0, "mid": 0.25, "high": 0.7}[tier]
    return float(round(lo * (hi / lo) ** fraction, 3))


def scenario_cells() -> list[dict[str, Any]]:
    cells: list[dict[str, Any]] = [{"cell": "normal", "kind": "normal", "attack_type": None, "tier": None, "hard": False}]
    for attack_type in ATTACK_TYPES:
        for tier in (("low",) if attack_type == "freeze" else SCENARIO_TIERS):
            cells.append({"cell": f"{attack_type}:{tier}", "kind": "attack", "attack_type": attack_type, "tier": tier, "hard": False})
    cells += [
        {"cell": "hard:small_drift", "kind": "attack", "attack_type": "drift", "tier": "hard", "hard": True},
        {"cell": "hard:noise_burst", "kind": "attack", "attack_type": "noise", "tier": "hard", "hard": True},
        {"cell": "hard:brief_jump", "kind": "attack", "attack_type": "jump", "tier": "hard", "hard": True},
    ]
    return cells


def _attack_for_cell(cell: dict[str, Any], rng: np.random.Generator, duration: float) -> list[AttackConfig]:
    if cell["kind"] == "normal":
        return []
    attack_type, tier = cell["attack_type"], cell["tier"]
    start = float(rng.uniform(30, max(31.0, duration * 0.55)))
    length = float(rng.uniform(20, 50))
    magnitude = _tier_magnitude(attack_type, tier) if tier != "hard" else {"drift": 0.15, "noise": 6.0, "jump": 12.0}[attack_type]
    if tier == "hard":
        length = {"drift": float(rng.uniform(30, 60)), "noise": float(rng.uniform(5, 10)), "jump": float(rng.uniform(2, 4))}[attack_type]
        magnitude *= float(rng.uniform(0.9, 1.4))
    spec = ATTACK_SPECS[attack_type].magnitude
    if attack_type != "freeze":
        magnitude = min(max(magnitude, spec.minimum), spec.maximum)
    return [AttackConfig(type=attack_type, start_s=round(start, 1), duration_s=round(length, 1), magnitude=round(magnitude, 3),
                         direction_deg=None)]


SPLIT_PATTERN = ("train", "train", "train", "train", "val", "val", "test", "test")  # legacy 160-flight profile (4/2/2)
LARGE_SPLIT_PATTERN = ("train",) * 6 + ("val",) * 2 + ("test",) * 2  # 60/20/20
PROFILES: dict[str, dict[str, Any]] = {
    "legacy": {"flights_per_cell": 8, "pattern": SPLIT_PATTERN, "weights": {}},
    # Extra repetitions for the single-tier freeze cell and the attack-free cell so every attack type has >= 25 attacked flights
    # and each of validation/test has >= 8 h of attack-free flight time.
    "large": {"flights_per_cell": 50, "pattern": LARGE_SPLIT_PATTERN, "weights": {"normal": 4, "freeze:low": 3}},
}
MIN_ATTACKED_FLIGHTS_PER_TYPE = 25
MIN_NORMAL_HOURS = 8.0


def _manifest_counts(flights_frame: pd.DataFrame, features: pd.DataFrame, labels: pd.DataFrame) -> dict[str, Any]:
    """Counts per split, attack type and magnitude bin, plus attack-free flight hours (not attacked and not recovering)."""
    recovery = np.zeros(len(labels), dtype=bool)
    t = labels["t"].to_numpy()
    attacked = labels["is_attacked"].to_numpy().astype(bool)
    for _, positions in labels.groupby(["flight_id", "aircraft_id"], sort=False).indices.items():
        recovery[positions] = recovery_flags(t[positions], attacked[positions])
    normal_rows = ~attacked & ~recovery
    split_of = flights_frame.set_index("flight_id")["split"]
    row_split = labels["flight_id"].map(split_of).to_numpy()
    out: dict[str, Any] = {}
    for split in ("train", "val", "test"):
        in_split = flights_frame[flights_frame.split == split]
        attacked_flights = in_split[in_split.attack_type.notna()]
        out[split] = {
            "flights": int(len(in_split)), "attacked_flights": int(len(attacked_flights)), "attack_free_flights": int(len(in_split) - len(attacked_flights)),
            "attack_free_hours": round(float(normal_rows[row_split == split].sum() * DT / 3600.0), 2),
            "by_attack_type": {str(k): int(v) for k, v in attacked_flights.groupby("attack_type").size().items()},
            "by_magnitude_bin": {str(k): int(v) for k, v in attacked_flights.groupby("tier").size().items()},
            "by_attack_type_and_bin": {f"{k[0]}:{k[1]}": int(v) for k, v in attacked_flights.groupby(["attack_type", "tier"]).size().items()},
        }
    return out


def _assert_minimums(counts: dict[str, Any]) -> None:
    for split in ("val", "test"):
        short = {k: v for k, v in counts[split]["by_attack_type"].items() if v < MIN_ATTACKED_FLIGHTS_PER_TYPE}
        if short or len(counts[split]["by_attack_type"]) < len(ATTACK_TYPES):
            raise ValueError(f"{split}: fewer than {MIN_ATTACKED_FLIGHTS_PER_TYPE} attacked flights for {short or 'a missing attack type'}")
        if counts[split]["attack_free_hours"] < MIN_NORMAL_HOURS:
            raise ValueError(f"{split}: only {counts[split]['attack_free_hours']} h of attack-free flight time (need {MIN_NORMAL_HOURS})")


def generate_dataset(out_dir: Path, seed: int = 20261113, flights_per_cell: Optional[int] = None, profile: str = "legacy") -> dict[str, Any]:
    """One command: simulate flights, write features and hidden labels to separate CSV files plus a manifest.

    Splits are by FLIGHT: every flight (and its seed) belongs to exactly one split; each split sees every scenario
    cell. With 8 flights per cell, 4/2/2 flights go to train/val/test.
    """
    spec = PROFILES[profile]
    flights_per_cell = int(flights_per_cell or spec["flights_per_cell"])
    pattern, weights = spec["pattern"], spec["weights"]
    out_dir.mkdir(parents=True, exist_ok=True)
    cells = scenario_cells()
    feature_rows, label_rows, flights = [], [], []
    flight_number = 0
    for cell_index, cell in enumerate(cells):
        for rep in range(flights_per_cell * int(weights.get(cell['cell'], 1))):
            flight_seed = int(np.random.default_rng([seed, cell_index, rep]).integers(1, 2**31 - 1))
            rng = np.random.default_rng([seed, cell_index, rep, 5])
            duration = float(rng.uniform(110, 200))
            attacks = _attack_for_cell(cell, rng, duration)
            n_aircraft = 2 if rep % 3 == 0 else 1
            flight_id = f"F{flight_number:04d}"
            flight_number += 1
            split = pattern[rep % len(pattern)]
            flight = simulate_flight(flight_id, flight_seed, attacks, n_aircraft, duration)
            for aircraft, frame in zip(flight.aircraft, flight_features(flight)):
                frame = frame.copy()
                frame.insert(0, "t", aircraft.t)
                frame.insert(0, "aircraft_id", aircraft.aircraft_id)
                frame.insert(0, "flight_id", flight_id)
                frame["battery_pct"] = aircraft.battery_pct
                frame["role"] = aircraft.role
                frame["remaining_fraction"] = 1.0 - aircraft.t / max(aircraft.total_s, 1.0)
                frame["position_error_m"] = np.linalg.norm((aircraft.gps.position - aircraft.true_position)[:, :2], axis=1)
                feature_rows.append(frame)
                label_rows.append(pd.DataFrame({
                    "flight_id": flight_id, "aircraft_id": aircraft.aircraft_id, "t": aircraft.t,
                    "is_attacked": aircraft.truth.is_attacked, "attack_type": aircraft.truth.attack_type,
                }))
            flights.append({
                "flight_id": flight_id, "seed": flight_seed, "split": split, "cell": cell["cell"], "attack_type": cell["attack_type"],
                "tier": cell["tier"], "hard_case": cell["hard"], "n_aircraft": n_aircraft, "duration_s": round(duration, 1),
                "attack": json.loads(attacks[0].model_dump_json()) if attacks else None,
            })
    features = pd.concat(feature_rows, ignore_index=True)
    labels = pd.concat(label_rows, ignore_index=True)
    features.to_csv(out_dir / "features.csv", index=False)
    labels.to_csv(out_dir / "labels_HIDDEN.csv", index=False)
    flights_frame = pd.DataFrame(flights)
    flights_frame.to_csv(out_dir / "flights.csv", index=False)
    config = {"seed": seed, "flights_per_cell": flights_per_cell, "hz": HZ, "feature_columns": FEATURE_COLUMNS_ALL, "cells": [c["cell"] for c in cells]}
    if profile != "legacy":
        config["profile"] = profile  # legacy keeps its original config (and hash) shape
    manifest = {
        "seed": seed, "config": config, "config_hash": hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest(),
        "files": {"features": "features.csv", "labels": "labels_HIDDEN.csv", "flights": "flights.csv"},
        "splits": {split: {"flights": int((flights_frame.split == split).sum()),
                           "rows": int(features.flight_id.isin(flights_frame[flights_frame.split == split].flight_id).sum()),
                           "attacked_rows": int(labels[labels.flight_id.isin(flights_frame[flights_frame.split == split].flight_id)].is_attacked.sum())}
                   for split in ("train", "val", "test")},
        "total_flights": len(flights), "total_rows": int(len(features)),
        "profile": profile, "counts": _manifest_counts(flights_frame, features, labels),
        "note": "Synthetic data. Labels live only in labels_HIDDEN.csv and are never features.",
    }
    if profile != "legacy":
        _assert_minimums(manifest["counts"])
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest
