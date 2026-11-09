"""Seeded fixed-timestep multi-UAV simulation."""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, asdict
from typing import Any, Mapping, Optional

from app.research.features import GpsFeatureExtractor
from app.research.model import predict, validate_model
from .detector import TrajectoryResidualDetector
from .response import apply_response, reassign_tasks


SIMULATION_VERSION = "deterministic-abstract-2d-v2"


@dataclass
class SimulationConfig:
    uav_count: int = 4
    task_count: int = 10
    seed: int = 21
    duration: int = 180
    spoofing_type: str = "sudden"  # none, sudden, gradual, intermittent, jump, freeze, noise, degradation, velocity_inconsistency
    target_uav: str = "UAV-02"
    spoof_start: int = 15
    spoof_duration: int = 35
    spoof_magnitude: float = 3.0
    response_policy: str = "adaptive"  # adaptive, monitor, baseline
    detector_threshold: float | None = None
    dt: float = 1.0

    def validate(self) -> None:
        if not 2 <= self.uav_count <= 12:
            raise ValueError("uav_count must be between 2 and 12")
        if not 1 <= self.task_count <= 40:
            raise ValueError("task_count must be between 1 and 40")
        if not 1 <= self.duration <= 3600:
            raise ValueError("duration must be between 1 and 3600 ticks")
        if self.spoofing_type not in {"none", "sudden", "gradual", "intermittent", "jump", "freeze", "noise", "degradation", "velocity_inconsistency"}:
            raise ValueError("unsupported spoofing_type")
        if self.response_policy not in {"adaptive", "monitor", "baseline"}:
            raise ValueError("unsupported response_policy")
        if self.spoof_start < 0 or self.spoof_duration < 1 or self.spoof_magnitude < 0:
            raise ValueError("spoof duration must be positive and magnitude nonnegative")
        if self.spoofing_type in {"sudden", "gradual", "intermittent", "jump", "velocity_inconsistency"} and self.spoof_magnitude == 0:
            raise ValueError("the selected fault profile requires a positive spoof_magnitude")
        if self.detector_threshold is not None and self.detector_threshold <= 0:
            raise ValueError("detector_threshold must be positive when configured")
        valid_targets = {f"UAV-{index + 1:02d}" for index in range(self.uav_count)}
        if self.target_uav not in valid_targets:
            raise ValueError("target_uav must identify an aircraft in the configured fleet")


class Simulation:
    def __init__(self, config: SimulationConfig, detector_model: Optional[Mapping[str, Any]] = None):
        config.validate()
        if detector_model is not None:
            validate_model(detector_model)
        self.config = config
        self.detector_model = dict(detector_model) if detector_model is not None else None
        self.feature_extractor = GpsFeatureExtractor() if detector_model is not None else None
        self.rng = random.Random(config.seed)
        self.sensor_rngs = {f"UAV-{index + 1:02d}": random.Random(config.seed * 1009 + index * 7919 + 1) for index in range(config.uav_count)}
        self.detector = TrajectoryResidualDetector(config.detector_threshold)
        self.last_goal: dict[str, str | None] = {}
        self._previous_gps: dict[str, tuple[float, float]] = {}
        self._frozen_gps: dict[str, tuple[float, float]] = {}
        self.tick_count = 0
        self.status = "ready"
        self.events: list[dict[str, Any]] = []
        self.history: list[dict[str, Any]] = []
        self.state = self._initial_state()
        self._emit("mission_created", reason="Seeded mission initialized; coordinate plane is abstract simulation units.")
        self._snapshot()

    def _initial_state(self) -> dict[str, Any]:
        uavs: dict[str, Any] = {}
        capabilities_by_uav: dict[str, list[str]] = {}
        available_capabilities = ("survey", "relay", "inspection")
        for index in range(self.config.uav_count):
            uid = f"UAV-{index + 1:02d}"
            capabilities = [available_capabilities[index % 3], available_capabilities[(index + 1) % 3]]
            capabilities_by_uav[uid] = capabilities
            angle = index * math.tau / max(1, self.config.uav_count)
            pos = {"x": round(5 + math.cos(angle) * 1.6, 4), "y": round(5 + math.sin(angle) * 1.6, 4)}
            endurance_total = max(120, min(3600, self.config.duration * 2))
            uavs[uid] = {"id": uid, "position": pos, "gps": dict(pos), "heading": 0.0, "speed": 0.42, "task_id": None,
                         "status": "active", "capacity": max(2, math.ceil(self.config.task_count / self.config.uav_count)),
                         "workload_capacity": max(2, math.ceil(self.config.task_count / self.config.uav_count)),
                         "readiness": "available", "communications_available": True,
                         "capabilities": capabilities, "payload_capacity_kg": round(1.0 + 0.5 * (index % 4), 2),
                         "endurance_total_ticks": endurance_total, "endurance_remaining_ticks": endurance_total,
                         "endurance_reserve_ticks": int(math.ceil(endurance_total * 0.2)),
                         "battery_pct": 100.0, "battery_reserve_pct": 20.0,
                         "anomaly_score": 0.0, "threat_level": "low", "anomaly_streak": 0, "clear_streak": 0,
                         "ml_detection": None,
                         "alerted": False, "alert_acknowledged": False,
                         "velocity": {"x": 0.0, "y": 0.0}, "residual": 0.0}
            uavs[uid]["gps_velocity"] = {"x": 0.0, "y": 0.0}
        tasks: dict[str, Any] = {}
        for index in range(self.config.task_count):
            tid = f"T-{index + 1:02d}"
            owner = list(uavs)[index % len(uavs)]
            payload_capacity = uavs[owner]["payload_capacity_kg"]
            tasks[tid] = {"id": tid, "position": {"x": round(self.rng.uniform(0.6, 9.4), 3), "y": round(self.rng.uniform(0.6, 9.4), 3)},
                          "priority": 1 + self.rng.randrange(3), "status": "pending", "uav_id": owner,
                          "required_capability": self.rng.choice(capabilities_by_uav[owner]),
                          "payload_required_kg": round(self.rng.uniform(0.1, payload_capacity), 2),
                          "created_tick": 0, "completed_tick": None}
        for uid, uav in uavs.items():
            next_task = next((t for t in tasks.values() if t["uav_id"] == uid), None)
            uav["task_id"] = next_task["id"] if next_task else None
        return {"uavs": uavs, "tasks": tasks, "dt": self.config.dt}

    def _emit(self, event_type: str, **fields: Any) -> None:
        self.events.append({"id": f"E-{len(self.events) + 1:04d}", "tick": self.tick_count, "time": round(self.tick_count * self.config.dt, 2), "type": event_type, **fields})

    def start(self) -> dict[str, Any]:
        if self.status in {"ready", "paused"}:
            self.status = "running"
            self._emit("mission_started", reason="Simulation clock started.")
        return self.view()

    def pause(self) -> dict[str, Any]:
        if self.status == "running":
            self.status = "paused"
            self._emit("mission_paused", reason="Simulation time is frozen.")
        return self.view()

    def stop(self) -> dict[str, Any]:
        if self.status not in {"completed", "stopped"}:
            self.status = "stopped"
            self._emit("mission_stopped", reason="Simulation stopped by operator.")
        return self.view()

    def reset(self) -> dict[str, Any]:
        self.rng = random.Random(self.config.seed)
        self.sensor_rngs = {f"UAV-{index + 1:02d}": random.Random(self.config.seed * 1009 + index * 7919 + 1) for index in range(self.config.uav_count)}
        self.detector = TrajectoryResidualDetector(self.config.detector_threshold)
        self.feature_extractor = GpsFeatureExtractor() if self.detector_model is not None else None
        self.last_goal = {}
        self._previous_gps = {}
        self._frozen_gps = {}
        self.tick_count = 0
        self.status = "ready"
        self.events = []
        self.history = []
        self.state = self._initial_state()
        self._emit("mission_reset", reason="Configuration and random seed restored to initial state.")
        self._snapshot()
        return self.view()

    def step(self) -> dict[str, Any]:
        if self.status not in {"running", "ready", "paused"}:
            return self.view()
        prior_status = self.status
        self.tick_count += 1
        newly_completed = []
        for uid, uav in self.state["uavs"].items():
            if uav["status"] in {"isolated", "complete"} or uav.get("readiness") != "available":
                continue
            task = self.state["tasks"].get(uav["task_id"])
            if task is None or task["status"] != "pending" or task["uav_id"] != uid:
                task = next((t for t in self.state["tasks"].values() if t["uav_id"] == uid and t["status"] == "pending"), None)
                uav["task_id"] = task["id"] if task else None
            if task:
                px, py = uav["position"]["x"], uav["position"]["y"]
                dx, dy = task["position"]["x"] - px, task["position"]["y"] - py
                distance = math.hypot(dx, dy)
                travel = min(uav["speed"] * self.config.dt, distance)
                vx, vy = ((dx / distance * travel, dy / distance * travel) if distance else (0.0, 0.0))
                uav["position"] = {"x": round(px + vx, 4), "y": round(py + vy, 4)}
                uav["velocity"] = {"x": round(vx / self.config.dt, 4), "y": round(vy / self.config.dt, 4)}
                uav["heading"] = round(math.degrees(math.atan2(vy, vx)) % 360, 2) if travel else uav["heading"]
                if travel > 0:
                    uav["endurance_remaining_ticks"] = max(0, uav["endurance_remaining_ticks"] - 1)
                    uav["battery_pct"] = round(
                        100.0 * uav["endurance_remaining_ticks"] / uav["endurance_total_ticks"], 2,
                    )
                if distance <= travel + 0.01:
                    task["status"] = "complete"
                    task["completed_tick"] = self.tick_count
                    newly_completed.append(task["id"])
                    self._emit("task_completed", uav_id=uid, task_id=task["id"], severity="info", reason="UAV reached the assigned task location.")
                    next_task = next((t for t in self.state["tasks"].values() if t["uav_id"] == uid and t["status"] == "pending"), None)
                    uav["task_id"] = next_task["id"] if next_task else None
                    if not next_task and all(t["status"] == "complete" for t in self.state["tasks"].values()):
                        uav["status"] = "complete"
                reserve_reached = (
                    uav["endurance_remaining_ticks"] <= uav["endurance_reserve_ticks"]
                    or uav["battery_pct"] <= uav["battery_reserve_pct"]
                )
                if reserve_reached and uav["readiness"] == "available":
                    uav["readiness"] = "low_endurance"
                    self._emit(
                        "uav_low_endurance", uav_id=uid, severity="warning",
                        reason="Abstract endurance reserve reached; remaining tasks are reassigned or left unassigned.",
                    )
                    for event in reassign_tasks(self.state, uid):
                        self._emit(event.pop("type"), **event)
                    if uav["task_id"] and self.state["tasks"][uav["task_id"]]["uav_id"] != uid:
                        uav["task_id"] = next((
                            item["id"] for item in self.state["tasks"].values()
                            if item["uav_id"] == uid and item["status"] == "pending"
                        ), None)
            else:
                uav["velocity"] = {"x": 0.0, "y": 0.0}
                if not any(t["status"] == "pending" for t in self.state["tasks"].values()):
                    uav["status"] = "complete"

        for uid, uav in self.state["uavs"].items():
            if uav["status"] == "isolated":
                continue
            truth = uav["position"]
            offset = self._spoof_offset(uid)
            # All fault effects remain in reported software GPS; truth is unchanged.
            sensor_rng = self.sensor_rngs[uid]
            sigma = self._gps_noise_sigma(uid)
            gps = {"x": truth["x"] + offset[0] + sensor_rng.gauss(0, sigma), "y": truth["y"] + offset[1] + sensor_rng.gauss(0, sigma)}
            if self.config.spoofing_type == "freeze" and self._fault_active(uid):
                if uid not in self._frozen_gps:
                    self._frozen_gps[uid] = (gps["x"], gps["y"])
                gps = {"x": self._frozen_gps[uid][0], "y": self._frozen_gps[uid][1]}
            previous_gps = self._previous_gps.get(uid)
            gps_velocity = {
                "x": (gps["x"] - previous_gps[0]) / self.config.dt if previous_gps is not None else 0.0,
                "y": (gps["y"] - previous_gps[1]) / self.config.dt if previous_gps is not None else 0.0,
            }
            self._previous_gps[uid] = (gps["x"], gps["y"])
            if self.config.spoofing_type == "velocity_inconsistency" and self._fault_active(uid):
                gps_velocity["x"] += self.config.spoof_magnitude
                gps_velocity["y"] += self.config.spoof_magnitude * 0.22
            destination = None
            task = self.state["tasks"].get(uav["task_id"])
            if task and task["status"] == "pending":
                destination = (task["position"]["x"], task["position"]["y"])
            goal_id = task["id"] if task and task["status"] == "pending" else None
            if uid in self.last_goal and self.last_goal[uid] != goal_id:
                destination = None
            self.last_goal[uid] = goal_id
            detection = self.detector.update(uid, (gps["x"], gps["y"]), destination, uav["speed"], self.config.dt)
            uav["gps"] = {"x": round(gps["x"], 4), "y": round(gps["y"], 4)}
            uav["gps_velocity"] = {"x": round(gps_velocity["x"], 4), "y": round(gps_velocity["y"], 4)}
            uav["residual"] = round(detection.residual, 4)
            policy = "baseline" if self.config.response_policy == "baseline" else self.config.response_policy
            response_score = detection.score
            response_anomalous = detection.anomalous
            if self.detector_model is not None and self.feature_extractor is not None:
                threshold = float(self.detector.threshold)
                features = self.feature_extractor.update(
                    uid, (gps["x"], gps["y"]), detection.residual, threshold, self.config.dt,
                    (gps_velocity["x"], gps_velocity["y"]),
                )
                prediction = predict(self.detector_model, features)
                uav["ml_detection"] = prediction
                response_score = float(prediction["attack_probability"])
                response_anomalous = bool(prediction["detected"])
            else:
                uav["ml_detection"] = None
            detector_mode = "trained_ml" if self.detector_model is not None else "heuristic"
            for event in apply_response(self.state, uid, response_score, response_anomalous, policy, detector_mode):
                event_type = event.pop("type")
                self._emit(event_type, **event)

        self.status = "paused" if prior_status in {"ready", "paused"} else "running"
        if self.tick_count >= self.config.duration or all(t["status"] == "complete" for t in self.state["tasks"].values()):
            self.status = "completed"
            self._emit("mission_completed", reason="Mission duration reached or all tasks completed.")
        self._snapshot()
        return self.view()

    def _spoof_offset(self, uid: str) -> tuple[float, float]:
        c = self.config
        elapsed = self.tick_count - c.spoof_start
        if not self._fault_active(uid) or c.spoofing_type in {"freeze", "noise", "degradation", "velocity_inconsistency"}:
            return 0.0, 0.0
        if c.spoofing_type == "jump" and elapsed > 0:
            return 0.0, 0.0
        if c.spoofing_type == "intermittent" and (elapsed // 5) % 2:
            return 0.0, 0.0
        if c.spoofing_type == "gradual":
            magnitude = c.spoof_magnitude * min(1.0, (elapsed + 1) / c.spoof_duration)
        else:
            magnitude = c.spoof_magnitude
        return magnitude, magnitude * 0.22

    def _fault_active(self, uid: str) -> bool:
        c = self.config
        elapsed = self.tick_count - c.spoof_start
        if uid != c.target_uav or c.spoofing_type == "none" or elapsed < 0 or elapsed >= c.spoof_duration:
            return False
        if c.spoofing_type == "intermittent" and (elapsed // 5) % 2:
            return False
        return c.spoofing_type != "jump" or elapsed == 0

    def _gps_noise_sigma(self, uid: str) -> float:
        if not self._fault_active(uid):
            return 0.01
        if self.config.spoofing_type == "noise":
            return 0.12
        if self.config.spoofing_type == "degradation":
            elapsed = self.tick_count - self.config.spoof_start
            progress = min(1.0, (elapsed + 1) / self.config.spoof_duration)
            return 0.01 + 0.24 * progress
        return 0.01

    def fault_active_at_current_tick(self, uid: str) -> bool:
        """Expose the applied software-fault label to offline research tooling."""
        return self._fault_active(uid)

    def _snapshot(self) -> None:
        self.history.append(self.view())

    def view(self) -> dict[str, Any]:
        uavs = {}
        for uid, raw in self.state["uavs"].items():
            uavs[uid] = {k: (list(v) if isinstance(v, list) else dict(v) if isinstance(v, dict) else v) for k, v in raw.items() if k not in {"anomaly_streak", "clear_streak", "alerted"}}
            uavs[uid]["workload"] = sum(
                item["status"] == "pending" and item["uav_id"] == uid
                for item in self.state["tasks"].values()
            )
        return {"status": self.status, "tick": self.tick_count, "time": round(self.tick_count * self.config.dt, 2), "seed": self.config.seed,
                "detector": {"mode": "trained_ml" if self.detector_model is not None else "heuristic",
                             "model_id": self.detector_model["model_id"] if self.detector_model is not None else None},
                "coordinate_system": "abstract simulation units; not geographic coordinates", "config": asdict(self.config),
                "uavs": uavs, "tasks": {key: {**value, "position": dict(value["position"])} for key, value in self.state["tasks"].items()},
                "progress": {"completed": sum(t["status"] == "complete" for t in self.state["tasks"].values()), "total": len(self.state["tasks"]),
                             "remaining": sum(t["status"] == "pending" for t in self.state["tasks"].values())},
                "events": self.events[-100:], "calibration": {"threshold": self.detector.threshold, **self.detector.calibration}}
