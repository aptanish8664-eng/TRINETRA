"""Simulation-only quaternion attitude and local ENU position/velocity fusion."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import TypedDict
from numpy.typing import ArrayLike

import numpy as np


GRAVITY_M_S2 = 9.80665
GPS_GATE_CHI_SQUARE_99_DF3 = 11.345
GPS_MAX_ABSOLUTE_INNOVATION_M = 3.0
GPS_REACQUIRE_MIN_GAP_S = 3.0
GPS_REACQUIRE_MIN_CONSISTENT_FIXES = 3
GPS_REACQUIRE_MAX_STEP_M = 1.5
EKF_ACCELERATION_NOISE_M_S2 = 0.12
EKF_BIAS_RANDOM_WALK_M_S3 = 0.008
EKF_INITIAL_ACCELEROMETER_BIAS_VARIANCE = 0.001
AHRS_CORRECTION_RATE = 1.8
AHRS_GYRO_BIAS_RATE = 0.025


class AttitudeEstimate(TypedDict):
    quaternion_body_to_world: list[float]
    euler_rad: tuple[float, float, float]
    gyro_bias_rad_s: list[float]
    correction_available: bool


class NavigationEstimate(TypedDict):
    position_enu_m: list[float]
    velocity_enu_m_s: list[float]
    accelerometer_bias_enu_m_s2: list[float]
    covariance_diagonal: list[float]
    covariance_trace: float
    innovation_m: list[float] | None
    innovation_mahalanobis_squared: float | None
    gps_measurement_used: bool
    gps_rejection_reason: str | None
    timestamp_s: float
    coordinate_frame: str


def _vector(values: ArrayLike | None, size: int, name: str) -> np.ndarray | None:
    if values is None:
        return None
    result = np.asarray(values, dtype=float)
    if result.shape != (size,) or not np.isfinite(result).all():
        raise ValueError(f"{name} must contain {size} finite values")
    return result


def _unit(values: np.ndarray) -> np.ndarray | None:
    norm = float(np.linalg.norm(values))
    return None if norm < 1e-9 else values / norm


def _quaternion_multiply(left: np.ndarray, right: np.ndarray) -> np.ndarray:
    w1, x1, y1, z1 = left
    w2, x2, y2, z2 = right
    return np.asarray((
        w1 * w2 - x1 * x2 - y1 * y2 - z1 * z2,
        w1 * x2 + x1 * w2 + y1 * z2 - z1 * y2,
        w1 * y2 - x1 * z2 + y1 * w2 + z1 * x2,
        w1 * z2 + x1 * y2 - y1 * x2 + z1 * w2,
    ))


def _quaternion_conjugate(quaternion: np.ndarray) -> np.ndarray:
    return quaternion * np.asarray((1.0, -1.0, -1.0, -1.0))


def _normalize_quaternion(quaternion: np.ndarray) -> np.ndarray:
    norm = float(np.linalg.norm(quaternion))
    if norm < 1e-12 or not math.isfinite(norm):
        raise ValueError("quaternion norm is invalid")
    return quaternion / norm


def quaternion_from_euler(roll: float, pitch: float, yaw: float) -> np.ndarray:
    """Return a body-to-world quaternion for intrinsic roll, pitch, yaw."""
    cr, sr = math.cos(roll / 2), math.sin(roll / 2)
    cp, sp = math.cos(pitch / 2), math.sin(pitch / 2)
    cy, sy = math.cos(yaw / 2), math.sin(yaw / 2)
    return _normalize_quaternion(np.asarray((
        cr * cp * cy + sr * sp * sy,
        sr * cp * cy - cr * sp * sy,
        cr * sp * cy + sr * cp * sy,
        cr * cp * sy - sr * sp * cy,
    )))


def euler_from_quaternion(quaternion: ArrayLike) -> tuple[float, float, float]:
    w, x, y, z = _normalize_quaternion(np.asarray(quaternion, dtype=float))
    roll = math.atan2(2 * (w * x + y * z), 1 - 2 * (x * x + y * y))
    pitch_sine = max(-1.0, min(1.0, 2 * (w * y - z * x)))
    pitch = math.asin(pitch_sine)
    yaw = math.atan2(2 * (w * z + x * y), 1 - 2 * (y * y + z * z))
    return roll, pitch, yaw


def rotate_body_to_world(quaternion: ArrayLike, vector: ArrayLike) -> np.ndarray:
    q = _normalize_quaternion(np.asarray(quaternion, dtype=float))
    v = np.asarray(vector, dtype=float)
    if v.shape != (3,) or not np.isfinite(v).all():
        raise ValueError("vector must contain three finite values")
    rotated = _quaternion_multiply(_quaternion_multiply(q, np.r_[0.0, v]), _quaternion_conjugate(q))
    return rotated[1:]


def _slerp(start: np.ndarray, end: np.ndarray, amount: float) -> np.ndarray:
    target = end.copy()
    dot = float(np.dot(start, target))
    if dot < 0:
        target = -target
        dot = -dot
    dot = max(-1.0, min(1.0, dot))
    if dot > 0.9995:
        return _normalize_quaternion(start + amount * (target - start))
    angle = math.acos(dot)
    scale_start = math.sin((1 - amount) * angle) / math.sin(angle)
    scale_end = math.sin(amount * angle) / math.sin(angle)
    return _normalize_quaternion(scale_start * start + scale_end * target)


class QuaternionComplementaryFilter:
    """Gyro-integrated quaternion corrected by gravity and magnetic heading."""

    def __init__(
        self,
        *,
        correction_rate: float = AHRS_CORRECTION_RATE,
        bias_rate: float = AHRS_GYRO_BIAS_RATE,
    ):
        if correction_rate <= 0 or bias_rate < 0:
            raise ValueError("attitude filter gains are invalid")
        self.correction_rate = correction_rate
        self.bias_rate = bias_rate
        self.quaternion = np.asarray((1.0, 0.0, 0.0, 0.0))
        self.gyro_bias_rad_s = np.zeros(3)

    def update(
        self,
        gyro_rad_s: ArrayLike | None,
        acceleration_m_s2: ArrayLike | None,
        magnetic_field: ArrayLike | None,
        dt: float,
    ) -> AttitudeEstimate:
        if not math.isfinite(dt) or dt <= 0 or dt > 1:
            raise ValueError("attitude timestep must be finite and in (0, 1] seconds")
        gyro = _vector(gyro_rad_s, 3, "gyro_rad_s")
        acceleration = _vector(acceleration_m_s2, 3, "acceleration_m_s2")
        magnetic = _vector(magnetic_field, 3, "magnetic_field")
        if gyro is not None:
            corrected_gyro = gyro - self.gyro_bias_rad_s
            derivative = 0.5 * _quaternion_multiply(
                self.quaternion, np.r_[0.0, corrected_gyro],
            )
            predicted = _normalize_quaternion(self.quaternion + derivative * dt)
        else:
            predicted = self.quaternion.copy()

        measured_euler = self._measured_euler(acceleration, magnetic)
        if measured_euler is not None:
            measured = quaternion_from_euler(*measured_euler)
            error = _quaternion_multiply(measured, _quaternion_conjugate(predicted))
            if error[0] < 0:
                error = -error
            correction = np.asarray((
                2 * error[1], 2 * error[2], 2 * error[3],
            ))
            self.gyro_bias_rad_s -= self.bias_rate * correction * dt
            weight = min(1.0, self.correction_rate * dt)
            self.quaternion = _slerp(predicted, measured, weight)
        else:
            self.quaternion = predicted
        return {
            "quaternion_body_to_world": self.quaternion.tolist(),
            "euler_rad": euler_from_quaternion(self.quaternion),
            "gyro_bias_rad_s": self.gyro_bias_rad_s.tolist(),
            "correction_available": measured_euler is not None,
        }

    @staticmethod
    def _measured_euler(
        acceleration: np.ndarray | None,
        magnetic: np.ndarray | None,
    ) -> tuple[float, float, float] | None:
        gravity = _unit(acceleration) if acceleration is not None else None
        if gravity is None:
            return None
        roll = math.atan2(float(gravity[1]), float(gravity[2]))
        pitch = math.atan2(
            -float(gravity[0]),
            math.hypot(float(gravity[1]), float(gravity[2])),
        )
        yaw = 0.0
        if magnetic is not None:
            field = _unit(magnetic)
            if field is not None:
                mx, my, mz = field
                cos_roll, sin_roll = math.cos(roll), math.sin(roll)
                cos_pitch, sin_pitch = math.cos(pitch), math.sin(pitch)
                horizontal_x = mx * cos_pitch + mz * sin_pitch
                horizontal_y = mx * sin_roll * sin_pitch + my * cos_roll - mz * sin_roll * cos_pitch
                yaw = math.atan2(-float(horizontal_y), float(horizontal_x))
        else:
            return None
        return roll, pitch, yaw


class PositionVelocityBiasEkf:
    """Three-dimensional ENU EKF with accelerometer bias and GPS innovation gate."""

    def __init__(
        self,
        initial_position_m: ArrayLike,
        *,
        initial_time_s: float,
        acceleration_noise_m_s2: float = EKF_ACCELERATION_NOISE_M_S2,
        bias_random_walk_m_s3: float = EKF_BIAS_RANDOM_WALK_M_S3,
        gps_gate_chi_square: float = GPS_GATE_CHI_SQUARE_99_DF3,
        gps_max_absolute_innovation_m: float = GPS_MAX_ABSOLUTE_INNOVATION_M,
    ):
        position = _vector(initial_position_m, 3, "initial_position_m")
        if position is None or not math.isfinite(initial_time_s):
            raise ValueError("EKF initial state is invalid")
        if (
            acceleration_noise_m_s2 <= 0
            or bias_random_walk_m_s3 <= 0
            or gps_gate_chi_square <= 0
            or gps_max_absolute_innovation_m <= 0
        ):
            raise ValueError("EKF noise and gate parameters must be positive")
        self.state = np.r_[position, np.zeros(3), np.zeros(3)]
        self.covariance = np.diag((
            1.0, 1.0, 1.0, 4.0, 4.0, 4.0,
            EKF_INITIAL_ACCELEROMETER_BIAS_VARIANCE,
            EKF_INITIAL_ACCELEROMETER_BIAS_VARIANCE,
            EKF_INITIAL_ACCELEROMETER_BIAS_VARIANCE,
        ))
        self.timestamp_s = float(initial_time_s)
        self.acceleration_noise_m_s2 = acceleration_noise_m_s2
        self.bias_random_walk_m_s3 = bias_random_walk_m_s3
        self.gps_gate_chi_square = gps_gate_chi_square
        self.gps_max_absolute_innovation_m = gps_max_absolute_innovation_m
        self.last_innovation_m: list[float] | None = None
        self.last_mahalanobis_squared: float | None = None
        self.last_gps_used = False
        self.last_gps_rejection_reason: str | None = None

    def predict(self, timestamp_s: float, acceleration_world_m_s2: ArrayLike | None) -> None:
        if not math.isfinite(timestamp_s) or timestamp_s <= self.timestamp_s:
            raise ValueError("EKF prediction timestamps must increase")
        dt = timestamp_s - self.timestamp_s
        if dt > 1.0:
            raise ValueError("EKF prediction gap exceeds one second")
        acceleration = _vector(acceleration_world_m_s2, 3, "acceleration_world_m_s2")
        if acceleration is None:
            acceleration = np.zeros(3)

        transition = np.eye(9)
        transition[:3, 3:6] = np.eye(3) * dt
        transition[:3, 6:9] = -np.eye(3) * (0.5 * dt * dt)
        transition[3:6, 6:9] = -np.eye(3) * dt
        self.state[:3] += self.state[3:6] * dt + 0.5 * (acceleration - self.state[6:9]) * dt * dt
        self.state[3:6] += (acceleration - self.state[6:9]) * dt

        noise_map = np.zeros((9, 6))
        noise_map[:3, :3] = np.eye(3) * (0.5 * dt * dt)
        noise_map[3:6, :3] = np.eye(3) * dt
        noise_map[6:9, 3:6] = np.eye(3) * dt
        process_noise = np.diag(
            [self.acceleration_noise_m_s2 ** 2] * 3
            + [self.bias_random_walk_m_s3 ** 2] * 3
        )
        self.covariance = (
            transition @ self.covariance @ transition.T
            + noise_map @ process_noise @ noise_map.T
        )
        self.covariance = (self.covariance + self.covariance.T) * 0.5
        self.timestamp_s = float(timestamp_s)
        self.last_gps_used = False
        self.last_gps_rejection_reason = None

    def update_gps(
        self,
        source_timestamp_s: float,
        current_timestamp_s: float,
        position_m: ArrayLike,
        *,
        sigma_m: float = 0.8,
    ) -> bool:
        measurement = _vector(position_m, 3, "GPS position")
        if measurement is None or not math.isfinite(source_timestamp_s) or not math.isfinite(current_timestamp_s):
            raise ValueError("GPS timestamps and measurements must be finite")
        if sigma_m <= 0 or not math.isfinite(sigma_m):
            raise ValueError("GPS standard deviation must be finite and positive")
        if source_timestamp_s > current_timestamp_s + 1e-9:
            self.last_gps_used = False
            self.last_gps_rejection_reason = "future_measurement"
            return False
        if source_timestamp_s < current_timestamp_s - 1e-9 or current_timestamp_s < self.timestamp_s - 1e-9:
            self.last_gps_used = False
            self.last_gps_rejection_reason = "out_of_sequence_measurement"
            return False
        if abs(current_timestamp_s - self.timestamp_s) > 1e-9:
            raise ValueError("GPS update time must match the current EKF state")

        observation = np.zeros((3, 9))
        observation[:, :3] = np.eye(3)
        innovation = measurement - observation @ self.state
        innovation_covariance = observation @ self.covariance @ observation.T + np.eye(3) * sigma_m ** 2
        mahalanobis_squared = float(innovation @ np.linalg.solve(innovation_covariance, innovation))
        self.last_innovation_m = innovation.tolist()
        self.last_mahalanobis_squared = mahalanobis_squared
        if (
            mahalanobis_squared > self.gps_gate_chi_square
            or float(np.linalg.norm(innovation)) > self.gps_max_absolute_innovation_m
        ):
            self.last_gps_used = False
            self.last_gps_rejection_reason = "innovation_gate"
            return False

        gain = np.linalg.solve(innovation_covariance, observation @ self.covariance).T
        self.state += gain @ innovation
        identity = np.eye(9)
        residual_projection = identity - gain @ observation
        self.covariance = (
            residual_projection @ self.covariance @ residual_projection.T
            + gain @ (np.eye(3) * sigma_m ** 2) @ gain.T
        )
        self.covariance = (self.covariance + self.covariance.T) * 0.5
        self.last_gps_used = True
        self.last_gps_rejection_reason = None
        return True

    def view(self) -> NavigationEstimate:
        return {
            "position_enu_m": self.state[:3].tolist(),
            "velocity_enu_m_s": self.state[3:6].tolist(),
            "accelerometer_bias_enu_m_s2": self.state[6:9].tolist(),
            "covariance_diagonal": np.diag(self.covariance).tolist(),
            "covariance_trace": float(np.trace(self.covariance)),
            "innovation_m": self.last_innovation_m,
            "innovation_mahalanobis_squared": self.last_mahalanobis_squared,
            "gps_measurement_used": self.last_gps_used,
            "gps_rejection_reason": self.last_gps_rejection_reason,
            "timestamp_s": self.timestamp_s,
            "coordinate_frame": "synthetic_local_ENU",
        }

    def reinitialize_position_after_gps_gap(
        self,
        position_m: ArrayLike,
        timestamp_s: float,
        *,
        sigma_m: float,
    ) -> None:
        position = _vector(position_m, 3, "GPS re-acquisition position")
        if position is None or not math.isfinite(timestamp_s) or sigma_m <= 0:
            raise ValueError("GPS re-acquisition state is invalid")
        innovation = position - self.state[:3]
        innovation_covariance = self.covariance[:3, :3] + np.eye(3) * sigma_m ** 2
        self.last_innovation_m = innovation.tolist()
        self.last_mahalanobis_squared = float(
            innovation @ np.linalg.solve(innovation_covariance, innovation)
        )
        bias = self.state[6:9].copy()
        bias_covariance = self.covariance[6:9, 6:9].copy()
        self.state = np.r_[position, np.zeros(3), bias]
        self.covariance = np.diag((sigma_m ** 2,) * 3 + (4.0,) * 3 + (1.0,) * 3)
        self.covariance[6:9, 6:9] = bias_covariance
        self.timestamp_s = float(timestamp_s)
        self.last_gps_used = True
        self.last_gps_rejection_reason = None


@dataclass(frozen=True)
class SensorEstimate:
    attitude: AttitudeEstimate
    navigation: NavigationEstimate | None


class SimulationSensorFusion:
    """Fuse synchronized synthetic ENU IMU/GPS; not a PX4 telemetry estimator."""

    def __init__(self):
        self.attitude_filter = QuaternionComplementaryFilter()
        self.navigation: PositionVelocityBiasEkf | None = None
        self._last_timestamp_s: float | None = None
        self._last_gps_source_timestamp_s: float | None = None
        self._gps_reacquiring = False
        self._gps_reacquisition_candidates: list[np.ndarray] = []

    def update(
        self,
        *,
        timestamp_s: float,
        acceleration_body_m_s2: ArrayLike | None,
        gyro_body_rad_s: ArrayLike | None,
        magnetic_body_gauss: ArrayLike | None,
        gps_position_enu_m: ArrayLike | None,
        gps_source_timestamp_s: float | None,
        gps_sigma_m: float = 0.8,
    ) -> SensorEstimate:
        if not math.isfinite(timestamp_s):
            raise ValueError("sample timestamp must be finite")
        if self._last_timestamp_s is None:
            dt = 0.1
        else:
            dt = timestamp_s - self._last_timestamp_s
            if dt <= 0 or dt > 1:
                raise ValueError("sensor sample timestamps must increase by at most one second")

        attitude = self.attitude_filter.update(
            gyro_body_rad_s, acceleration_body_m_s2, magnetic_body_gauss, dt,
        )
        if self.navigation is None:
            if gps_position_enu_m is not None and gps_source_timestamp_s is not None:
                self.navigation = PositionVelocityBiasEkf(
                    gps_position_enu_m, initial_time_s=timestamp_s,
                )
                self.navigation.last_gps_used = True
                self.navigation.last_innovation_m = [0.0, 0.0, 0.0]
                self.navigation.last_mahalanobis_squared = 0.0
                self._last_gps_source_timestamp_s = gps_source_timestamp_s
        else:
            specific_force_body = _vector(
                acceleration_body_m_s2, 3, "acceleration_body_m_s2",
            )
            if specific_force_body is None:
                acceleration_world = np.zeros(3)
            else:
                acceleration_world = rotate_body_to_world(
                    attitude["quaternion_body_to_world"], specific_force_body,
                ) + np.asarray((0.0, 0.0, -GRAVITY_M_S2))
            self.navigation.predict(timestamp_s, acceleration_world)
            if gps_position_enu_m is not None and gps_source_timestamp_s is not None:
                gps_position = _vector(gps_position_enu_m, 3, "GPS position")
                if gps_position is None:
                    raise ValueError("GPS position is required with its timestamp")
                is_synchronized = abs(gps_source_timestamp_s - timestamp_s) <= 1e-9
                if is_synchronized:
                    if (
                        self._last_gps_source_timestamp_s is not None
                        and gps_source_timestamp_s - self._last_gps_source_timestamp_s
                        >= GPS_REACQUIRE_MIN_GAP_S
                    ):
                        self._gps_reacquiring = True
                        self._gps_reacquisition_candidates.clear()
                    if self._gps_reacquiring:
                        innovation = gps_position - self.navigation.state[:3]
                        innovation_covariance = (
                            self.navigation.covariance[:3, :3]
                            + np.eye(3) * gps_sigma_m ** 2
                        )
                        self.navigation.last_innovation_m = innovation.tolist()
                        self.navigation.last_mahalanobis_squared = float(
                            innovation @ np.linalg.solve(innovation_covariance, innovation)
                        )
                        if (
                            self._gps_reacquisition_candidates
                            and float(np.linalg.norm(
                                gps_position - self._gps_reacquisition_candidates[-1]
                            )) > GPS_REACQUIRE_MAX_STEP_M
                        ):
                            self._gps_reacquisition_candidates = [gps_position]
                        else:
                            self._gps_reacquisition_candidates.append(gps_position)
                        if len(self._gps_reacquisition_candidates) >= GPS_REACQUIRE_MIN_CONSISTENT_FIXES:
                            reinitialized_position = np.mean(
                                self._gps_reacquisition_candidates[-GPS_REACQUIRE_MIN_CONSISTENT_FIXES:],
                                axis=0,
                            )
                            self.navigation.reinitialize_position_after_gps_gap(
                                reinitialized_position,
                                timestamp_s,
                                sigma_m=gps_sigma_m,
                            )
                            self._gps_reacquiring = False
                            self._gps_reacquisition_candidates.clear()
                        else:
                            self.navigation.last_gps_used = False
                            self.navigation.last_gps_rejection_reason = "reacquiring_after_gps_gap"
                    else:
                        self.navigation.update_gps(
                            gps_source_timestamp_s, timestamp_s, gps_position,
                            sigma_m=gps_sigma_m,
                        )
                    self._last_gps_source_timestamp_s = gps_source_timestamp_s
                else:
                    self.navigation.update_gps(
                        gps_source_timestamp_s, timestamp_s, gps_position,
                        sigma_m=gps_sigma_m,
                    )
        self._last_timestamp_s = timestamp_s
        return SensorEstimate(
            attitude=attitude,
            navigation=None if self.navigation is None else self.navigation.view(),
        )
