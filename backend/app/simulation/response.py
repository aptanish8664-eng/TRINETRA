"""Threat persistence, isolation and capacity-aware task reassignment."""

from __future__ import annotations

import math
from typing import Any, Optional


def apply_response(
    state: dict[str, Any], uav_id: str, score: float, anomalous: bool, policy: str,
    detector_mode: str = "heuristic",
) -> list[dict[str, Any]]:
    """Update threat state and perform an adaptive response when warranted."""
    uav = state["uavs"][uav_id]
    events: list[dict[str, Any]] = []
    if anomalous:
        uav["anomaly_streak"] += 1
        uav["clear_streak"] = 0
        uav["anomaly_score"] = score
        uav["threat_level"] = "high" if uav["anomaly_streak"] >= 3 else "elevated"
        if uav["anomaly_streak"] == 2:
            uav["status"] = "degraded"
            if not uav["alerted"]:
                reason = (
                    "The trained research classifier crossed its recorded threshold for two consecutive steps; confidence is uncalibrated."
                    if detector_mode == "trained_ml"
                    else "Trajectory residual exceeded the calibrated clean-run threshold for two consecutive steps."
                )
                events.append({"type": "anomaly_detected", "uav_id": uav_id, "severity": "elevated", "reason": reason})
                uav["alerted"] = True
                uav["alert_acknowledged"] = False
        if policy == "adaptive" and uav["anomaly_streak"] >= 3 and uav["status"] != "isolated":
            uav["status"] = "isolated"
            events.append({"type": "uav_isolated", "uav_id": uav_id, "severity": "high", "reason": "Persistent telemetry anomaly reached the three-step isolation rule."})
            events.extend(reassign_tasks(state, uav_id))
    else:
        uav["anomaly_score"] = score
        uav["clear_streak"] += 1
        if uav["clear_streak"] >= 4 and uav["status"] == "degraded":
            uav["status"] = "active"
            uav["threat_level"] = "low"
            uav["anomaly_streak"] = 0
            uav["alerted"] = False
            events.append({"type": "threat_cleared", "uav_id": uav_id, "severity": "info", "reason": "Four consecutive clean telemetry steps cleared the degraded state."})
    return events


def reassign_tasks(state: dict[str, Any], source_id: str) -> list[dict[str, Any]]:
    source = state["uavs"][source_id]
    events: list[dict[str, Any]] = []
    pending = [task for task in state["tasks"].values() if task["uav_id"] == source_id and task["status"] == "pending"]
    for task in sorted(pending, key=lambda item: (-item["priority"], item["id"])):
        candidates = []
        rejected: list[str] = []
        for candidate in state["uavs"].values():
            reason = reassignment_block_reason(state, source_id, task, candidate)
            if reason is not None:
                if candidate["id"] != source_id:
                    rejected.append(f"{candidate['id']}: {reason}")
                continue
            dx = candidate["position"]["x"] - task["position"]["x"]
            dy = candidate["position"]["y"] - task["position"]["y"]
            candidates.append((dx * dx + dy * dy, candidate["id"]))
        if candidates:
            _, target_id = min(candidates)
            task["uav_id"] = target_id
            target = state["uavs"][target_id]
            current_task = state["tasks"].get(target.get("task_id"))
            if current_task is None or current_task["status"] != "pending" or current_task["uav_id"] != target_id:
                target["task_id"] = task["id"]
            events.append({"type": "task_reassigned", "uav_id": target_id, "task_id": task["id"], "severity": "info", "reason": f"Task {task['id']} moved from unavailable {source_id} to the nearest eligible abstract-simulator UAV."})
        else:
            task["uav_id"] = None
            details = "; ".join(rejected) if rejected else "no other UAVs are configured"
            events.append({"type": "task_unassigned", "uav_id": source_id, "task_id": task["id"], "severity": "warning", "reason": f"No eligible abstract-simulator UAV could accept task {task['id']}: {details}."})
    current_task = state["tasks"].get(source.get("task_id"))
    if current_task is None or current_task["status"] != "pending" or current_task["uav_id"] != source_id:
        source["task_id"] = next((
            item["id"] for item in state["tasks"].values()
            if item["uav_id"] == source_id and item["status"] == "pending"
        ), None)
    return events


def reassignment_block_reason(
    state: dict[str, Any], source_id: str, task: dict[str, Any], candidate: dict[str, Any],
) -> Optional[str]:
    """Return why a simulated UAV cannot take a task, or None when eligible.

    Resource values are deterministic abstract mission parameters. They are not
    flight-controller measurements or operational safety guarantees.
    """
    if candidate["id"] == source_id:
        return "same as task source"
    if candidate.get("readiness", "available") != "available":
        return f"resource readiness is {candidate['readiness']}"
    if candidate["status"] in {"isolated", "complete", "low_endurance", "communication_lost"}:
        return f"unavailable status ({candidate['status']})"
    if candidate.get("communications_available") is False:
        return "communications unavailable"
    required = task.get("required_capability")
    if required and required not in candidate.get("capabilities", []):
        return f"missing capability {required}"
    required_payload = float(task.get("payload_required_kg", 0.0))
    if required_payload > float(candidate.get("payload_capacity_kg", float("inf"))):
        return "payload capacity insufficient"

    pending = [
        item for item in state["tasks"].values()
        if item["uav_id"] == candidate["id"] and item["status"] == "pending"
    ]
    workload_capacity = int(candidate.get("workload_capacity", candidate.get("capacity", len(state["tasks"]) + 1)))
    if len(pending) >= workload_capacity:
        return "workload capacity exhausted"

    speed = max(float(candidate.get("speed", 0.0)), 1e-9)
    distance = math.hypot(
        float(candidate["position"]["x"]) - float(task["position"]["x"]),
        float(candidate["position"]["y"]) - float(task["position"]["y"]),
    )
    dt = float(state.get("dt", 1.0))
    estimated_ticks = int(math.ceil(distance / (speed * dt)))
    reserve_ticks = int(candidate.get("endurance_reserve_ticks", 0))
    remaining_ticks = candidate.get("endurance_remaining_ticks")
    if remaining_ticks is not None and int(remaining_ticks) - estimated_ticks < reserve_ticks:
        return "endurance reserve would be breached"
    battery_pct = candidate.get("battery_pct")
    reserve_pct = float(candidate.get("battery_reserve_pct", 0.0))
    total_ticks = candidate.get("endurance_total_ticks")
    if battery_pct is not None and total_ticks:
        estimated_battery = float(battery_pct) - (100.0 * estimated_ticks / float(total_ticks))
        if estimated_battery < reserve_pct:
            return "battery reserve would be breached"
    return None
