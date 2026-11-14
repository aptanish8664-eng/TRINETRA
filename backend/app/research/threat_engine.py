"""Threat engine: severity, mission impact, threat score, tiers, evidence, ranked actions, handover scoring.

Three separate outputs (kept apart on purpose):
  * anomaly_confidence - CALIBRATED probability that the GPS stream is inconsistent with the inertial estimate.
  * severity           - how large / fast-growing the inconsistency is (0..1), from residuals only.
  * mission_impact     - how much it would hurt the mission (role, battery, remaining route), 0..1.
The threat score (0..100) is NOT a probability. Documented formula:

    threat_score = 100 * anomaly_confidence * (0.65 * severity + 0.35 * mission_impact)

Tiers (Low / Medium / High / Critical) come from thresholds tuned on validation data (see threat_models).
The engine only RECOMMENDS. It never sends or executes anything; the operator's choice is recorded elsewhere.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Mapping, Optional, Sequence

import numpy as np

ROLE_WEIGHT = {"primary": 1.0, "standby": 0.5, "decoy": 0.3}
DEFAULT_MIN_BATTERY_PCT = 40.0
TIERS = ("Low", "Medium", "High", "Critical")
THREAT_SCORE_FORMULA = "100 * anomaly_confidence * (0.65 * severity + 0.35 * mission_impact); not a probability"

# Plain-language evidence templates: (feature, unit, text). `reference` is the normal (99th percentile) value.
EVIDENCE_TEMPLATES = {
    "res_pos_w10": ("m", "GPS position is {value:.0f} m away from the IMU-based estimate over the last 10 s (normal: up to {reference:.0f} m)"),
    "res_pos_w30": ("m", "GPS position disagrees with the IMU-based estimate by {value:.0f} m over 30 s (normal: up to {reference:.0f} m)"),
    "res_vel": ("m/s", "GPS velocity differs from the IMU velocity by {value:.1f} m/s (normal: up to {reference:.1f} m/s)"),
    "heading_res_deg": ("deg", "GPS heading differs from the IMU heading by {value:.0f} degrees (normal: up to {reference:.0f} degrees)"),
    "innov_ratio": ("", "The navigation filter innovation is {value:.0f} times its nominal level (normal: up to {reference:.0f})"),
    "res_rate": ("m/s", "The GPS/IMU disagreement is changing by {value:+.1f} m/s (normal: up to {reference:.1f} m/s)"),
    "cross_res10": ("m", "This aircraft's GPS residual is {value:.0f} m larger than the other aircraft's (normal: up to {reference:.0f} m)"),
    "res60_z": ("x", "GPS has moved {value:.1f}x further from the IMU-based estimate over 60 s than is usual for this flight (normal: up to {reference:.1f}x)"),
    "noise_ratio": ("x", "GPS position jitter is {value:.1f}x this flight's early level (normal: up to {reference:.1f}x)"),
    "freeze_ratio": ("x", "The IMU shows {value:.0f}x more travel than GPS over 3 s (normal: up to {reference:.1f}x)"),
    "velpos_inconsist": ("m/s", "Reported GPS velocity disagrees with the change in GPS position by {value:.1f} m/s (normal: up to {reference:.1f} m/s)"),
}


@dataclass(frozen=True)
class TierThresholds:
    medium: float
    high: float
    critical: float

    def tier(self, score: float) -> str:
        if score >= self.critical:
            return "Critical"
        if score >= self.high:
            return "High"
        if score >= self.medium:
            return "Medium"
        return "Low"

    def as_dict(self) -> dict[str, float]:
        return {"medium": self.medium, "high": self.high, "critical": self.critical}


def severity(res_pos_w10, res_rate, res_vel):
    """0..1 from residual size, residual growth rate and velocity disagreement (vectorised)."""
    pos = 1.0 - np.exp(-np.asarray(res_pos_w10, dtype=float) / 25.0)
    rate = 1.0 - np.exp(-np.maximum(np.asarray(res_rate, dtype=float), 0.0) / 2.0)
    vel = 1.0 - np.exp(-np.asarray(res_vel, dtype=float) / 3.0)
    return 0.6 * pos + 0.2 * rate + 0.2 * vel


def mission_impact(role, battery_pct, remaining_fraction):
    """0..1 from role weight, low battery and remaining route (vectorised over battery/remaining)."""
    weight = np.vectorize(lambda value: ROLE_WEIGHT.get(str(value), 0.5))(role) if not isinstance(role, str) else ROLE_WEIGHT.get(role, 0.5)
    battery = np.clip(np.asarray(battery_pct, dtype=float), 0, 100)
    remaining = np.clip(np.asarray(remaining_fraction, dtype=float), 0, 1)
    return 0.4 * weight + 0.3 * (1.0 - battery / 100.0) + 0.3 * remaining


def threat_score(anomaly_confidence, severity_value, impact_value):
    return 100.0 * np.asarray(anomaly_confidence, dtype=float) * (0.65 * np.asarray(severity_value, dtype=float) + 0.35 * np.asarray(impact_value, dtype=float))


def evidence_items(features: Mapping[str, float], reference: Mapping[str, float], limit: int = 3) -> list[dict[str, Any]]:
    """Top evidence: features ranked by how far they exceed their normal (99th percentile) reference. Uses features only."""
    ranked = []
    for name, (unit, template) in EVIDENCE_TEMPLATES.items():
        if name not in features:
            continue
        value = float(features[name])
        ref = max(float(reference.get(name, 1.0)), 1e-6)
        ratio = abs(value) / ref
        if ratio >= 1.0:
            ranked.append((ratio, name, value, ref, unit, template))
    ranked.sort(reverse=True)
    return [{"feature": name, "value": round(value, 3), "reference": round(ref, 3), "unit": unit, "times_normal": round(ratio, 1),
             "text": template.format(value=value, reference=ref)}
            for ratio, name, value, ref, unit, template in ranked[:limit]]


ACTIONS = {
    "continue_monitor": {
        "label": "Continue and monitor", "tradeoff": "No cost; accepts the risk that an attack is already degrading navigation.",
    },
    "increase_monitoring": {
        "label": "Increase monitoring", "tradeoff": "Costs operator attention; keeps the mission running while evidence accumulates.",
    },
    "dead_reckoning_fallback": {
        "label": "Switch to dead-reckoning fallback", "tradeoff": "Ignores GPS, so position error grows with time and the IMU bias; best for short windows.",
    },
    "isolate_aircraft": {
        "label": "Isolate aircraft", "tradeoff": "Stops trusting the aircraft's data; its mission tasks are paused or lost until cleared.",
    },
    "handover_to_standby": {
        "label": "Hand over to standby", "tradeoff": "Keeps the task going with another aircraft; uses that aircraft's battery and leaves the original unproductive.",
    },
    "return_to_launch": {
        "label": "Return to launch", "tradeoff": "Ends the aircraft's task early; may be unsafe on a spoofed position so combine with fallback navigation.",
    },
}
# Base preference per tier (higher = more suitable).
_TIER_PREFERENCE = {
    "Low": {"continue_monitor": 1.0, "increase_monitoring": 0.4, "dead_reckoning_fallback": 0.1, "isolate_aircraft": 0.0, "handover_to_standby": 0.0, "return_to_launch": 0.0},
    "Medium": {"continue_monitor": 0.3, "increase_monitoring": 1.0, "dead_reckoning_fallback": 0.6, "isolate_aircraft": 0.2, "handover_to_standby": 0.3, "return_to_launch": 0.1},
    "High": {"continue_monitor": 0.0, "increase_monitoring": 0.4, "dead_reckoning_fallback": 1.0, "isolate_aircraft": 0.7, "handover_to_standby": 0.8, "return_to_launch": 0.4},
    "Critical": {"continue_monitor": 0.0, "increase_monitoring": 0.1, "dead_reckoning_fallback": 0.8, "isolate_aircraft": 1.0, "handover_to_standby": 0.9, "return_to_launch": 0.6},
}


def handover_candidates(candidates: Sequence[Mapping[str, Any]], min_battery_pct: float = DEFAULT_MIN_BATTERY_PCT,
                        allowed_roles: Sequence[str] = ("standby", "decoy"), max_distance_m: float = 2000.0) -> list[dict[str, Any]]:
    """Score handover candidates: battery, distance to the task, workload and role. Ineligible ones stay listed with a reason."""
    scored = []
    for item in candidates:
        battery = float(item.get("battery_pct") or 0.0)
        distance = float(item.get("distance_to_task_m") or 0.0)
        workload = float(item.get("workload") or 0.0)
        role = str(item.get("role") or "")
        reason = None
        if role not in allowed_roles:
            reason = f"role {role or 'unknown'} is not an allowed handover role"
        elif battery < min_battery_pct:
            reason = f"battery {battery:.0f}% is below the {min_battery_pct:.0f}% minimum"
        score = (0.4 * min(battery, 100.0) / 100.0 + 0.3 * (1.0 - min(distance / max_distance_m, 1.0))
                 + 0.2 * (1.0 - min(max(workload, 0.0), 1.0)) + 0.1 * (1.0 if role == "standby" else 0.5))
        scored.append({"aircraft_id": item.get("aircraft_id"), "role": role, "battery_pct": battery, "distance_to_task_m": distance,
                       "workload": workload, "eligible": reason is None, "ineligible_reason": reason, "score": round(score, 3)})
    scored.sort(key=lambda row: (not row["eligible"], -row["score"]))
    return scored


def rank_actions(tier: str, confidence: float, role: str, battery_pct: float, candidates: Sequence[Mapping[str, Any]],
                 min_battery_pct: float = DEFAULT_MIN_BATTERY_PCT) -> list[dict[str, Any]]:
    """Ranked recommendations with reason and trade-off. Recommendations only: nothing is executed."""
    preference = dict(_TIER_PREFERENCE[tier])
    eligible_candidates = [row for row in candidates if row["eligible"]]
    if role != "primary":
        preference["handover_to_standby"] *= 0.3   # handing over a non-primary aircraft rarely helps the mission
    if battery_pct < min_battery_pct and tier != "Low":
        preference["return_to_launch"] += 0.4      # low battery under threat makes returning more attractive
    rows = []
    for key, score in preference.items():
        meta = ACTIONS[key]
        eligible, reason = True, f"{tier} threat tier"
        if key == "handover_to_standby":
            if not eligible_candidates:
                eligible, reason = False, "no eligible standby/decoy aircraft (battery or role requirement not met)"
                score = 0.0
            else:
                reason = f"{tier} threat tier; best candidate {eligible_candidates[0]['aircraft_id']} at {eligible_candidates[0]['battery_pct']:.0f}% battery"
        elif key == "continue_monitor":
            reason = "no sustained inconsistency detected" if tier == "Low" else f"{tier} threat tier does not support ignoring the evidence"
        elif key == "increase_monitoring":
            reason = f"{tier} threat tier; more evidence is cheap" + ("" if confidence > 0.3 else "; confidence is still low")
        elif key == "dead_reckoning_fallback":
            reason = f"{tier} threat tier: GPS is inconsistent with the IMU" if tier != "Low" else "no need while GPS is consistent"
        elif key == "isolate_aircraft":
            reason = f"{tier} threat tier; stops trusting this aircraft's data" if tier in ("High", "Critical") else "premature at this tier"
        elif key == "return_to_launch":
            reason = ("low battery under threat" if battery_pct < min_battery_pct and tier != "Low" else f"{tier} threat tier")
        rows.append({"action": key, "label": meta["label"], "reason": reason, "tradeoff": meta["tradeoff"],
                     "eligible": eligible, "rank_score": round(float(score), 3)})
    rows.sort(key=lambda row: (not row["eligible"], -row["rank_score"]))
    for index, row in enumerate(rows, start=1):
        row["rank"] = index
    return rows
