"""Interpretable GPS-trajectory residual detector.

Only reported coordinates and the currently assigned destination enter the
detector. Ground-truth positions and scenario attack labels are never passed.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from statistics import mean, pstdev


DETECTOR_VERSION = "trajectory-residual-v0.1"


def calibrate_threshold(seed: int = 1729, samples: int = 512) -> tuple[float, dict[str, float]]:
    """Fit a clean-run residual limit from independent synthetic sensor noise.

    This is an explicit synthetic calibration distribution, not a trained ML
    model. It models independent 2D coordinate measurement noise (sigma 0.0125)
    and derives a conservative limit from its empirical 99.5th percentile.
    """
    rng = random.Random(seed)
    residuals = [((rng.gauss(0, 0.0125) ** 2 + rng.gauss(0, 0.0125) ** 2) ** 0.5) for _ in range(samples)]
    ordered = sorted(residuals)
    threshold = ordered[min(len(ordered) - 1, int(len(ordered) * 0.995))]
    return threshold, {"samples": float(samples), "mean": mean(residuals), "std": pstdev(residuals), "quantile": 0.995}


@dataclass
class Detection:
    score: float = 0.0
    anomalous: bool = False
    residual: float = 0.0


class TrajectoryResidualDetector:
    def __init__(self, threshold: float | None = None):
        self.threshold, self.calibration = calibrate_threshold() if threshold is None else (threshold, {"source": "configured"})
        self.predicted: dict[str, tuple[float, float]] = {}

    def update(self, uav_id: str, observed: tuple[float, float], destination: tuple[float, float] | None, speed: float, dt: float) -> Detection:
        previous_prediction = self.predicted.get(uav_id)
        if destination is None:
            self.predicted[uav_id] = observed
            return Detection()
        if previous_prediction is None:
            self.predicted[uav_id] = observed
            return Detection()
        dx, dy = destination[0] - previous_prediction[0], destination[1] - previous_prediction[1]
        length = (dx * dx + dy * dy) ** 0.5
        step = min(speed * dt, length)
        expected = (previous_prediction[0] + (dx / length * step if length else 0), previous_prediction[1] + (dy / length * step if length else 0))
        self.predicted[uav_id] = expected
        residual = ((observed[0] - expected[0]) ** 2 + (observed[1] - expected[1]) ** 2) ** 0.5
        score = residual / max(self.threshold, 1e-9)
        return Detection(score=score, anomalous=residual > self.threshold, residual=residual)


