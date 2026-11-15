"""Read-only threat assessment API: offline replays and live PX4 SITL telemetry (GET /api/research/live).

Nothing here executes anything: /assess returns recommendations, /decisions records the operator's choice.
Requests and features are guarded against ground-truth fields (labels, attack schedule). Replay ground truth stays
on the server and is revealed only by the evaluation endpoint after a replay is marked finished.
"""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from datetime import datetime, timezone
from collections import OrderedDict
from pathlib import Path
from typing import Any, Literal, Optional

import numpy as np
from fastapi import APIRouter, Body, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .attack_lab_routes import load_pinned_plan
from .auth_routes import require_csrf, require_permission
from .authorization import Permission, has_permission
from .config import LIVE_ASSESSMENT_INTERVAL_S, MIN_HANDOVER_BATTERY_PCT
from .persistence.database import (add_audit_event, get_attack_experiment, latest_mission_deployment, list_attack_experiments,
                                   list_research_decisions, record_research_decision)
from .research import live_inputs
from .research import threat_engine as engine
from .research.attacks import AttackConfig, GroundTruthLeak, assert_no_ground_truth_deep
from .research.threat_data import DT, FEATURE_COLUMNS, FEATURE_COLUMNS_LONG, HZ, compute_features, residual_series, simulate_aircraft
from .research.threat_models import ARTIFACT_DIR, RECOVERY_S, ThreatModel
from .mission_planning.geometry import to_enu
from .security import AuthContext

router = APIRouter()
model_dir: Path = Path(os.environ.get("TRINETRA_THREAT_MODEL_DIR", str(ARTIFACT_DIR)))
_loaded: dict[str, Any] = {"dir": None, "model": None, "error": None}
REPLAY_STORE: "OrderedDict[str, dict[str, Any]]" = OrderedDict()
MAX_REPLAYS = 8
MAX_REPLAY_S = 900.0
EVAL_STRIDE_S = 2.0
EVAL_WINDOW_S = 120.0
HOLD_S = 30.0  # alert hold: the strongest evidence of the last 30 s stays visible (the windowed residual only sees changes)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


def get_model() -> Optional[ThreatModel]:
    if _loaded["dir"] != str(model_dir):
        _loaded.update(dir=str(model_dir), model=None, error=None)
        try:
            _loaded["model"] = ThreatModel(model_dir)
        except Exception as error:  # missing or tampered artifact: report, never guess
            _loaded["error"] = f"{type(error).__name__}: {error}"
    return _loaded["model"]


class Sample(StrictModel):
    t: float
    gps_position: list[float] = Field(min_length=3, max_length=3)
    gps_velocity: list[float] = Field(min_length=3, max_length=3)
    imu_velocity: list[float] = Field(min_length=3, max_length=3)


class Peer(StrictModel):
    aircraft_id: str = Field(max_length=16)
    samples: list[Sample] = Field(min_length=5, max_length=600)


class Candidate(StrictModel):
    aircraft_id: str = Field(max_length=16)
    role: str = Field(max_length=16)
    battery_pct: float = Field(ge=0, le=100)
    distance_to_task_m: float = Field(default=0.0, ge=0)
    workload: float = Field(default=0.0, ge=0, le=1)


class AssessRequest(StrictModel):
    aircraft_id: str = Field(pattern=r"^px4-[1-9][0-9]{0,2}$")
    role: Literal["primary", "standby", "decoy"]
    battery_pct: float = Field(ge=0, le=100)
    remaining_fraction: float = Field(default=1.0, ge=0, le=1)
    samples: list[Sample] = Field(min_length=5, max_length=600)
    peers: list[Peer] = Field(default_factory=list, max_length=4)
    handover_candidates: list[Candidate] = Field(default_factory=list, max_length=5)
    min_battery_pct: float = Field(default=engine.DEFAULT_MIN_BATTERY_PCT, ge=0, le=100)
    candidate_roles: list[Literal["standby", "decoy"]] = Field(default_factory=lambda: ["standby", "decoy"], min_length=1, max_length=2)


def _arrays(samples: list[Sample]) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    return (np.array([s.t for s in samples], dtype=float), np.array([s.gps_position for s in samples], dtype=float),
            np.array([s.gps_velocity for s in samples], dtype=float), np.array([s.imu_velocity for s in samples], dtype=float))


def assess_arrays(model: ThreatModel, t, gps_position, gps_velocity, imu_velocity, role: str, battery_pct: float, remaining_fraction: float,
                  peer_res10: Optional[np.ndarray], candidates: list[dict], min_battery_pct: float, candidate_roles: list[str],
                  peer_res30: Optional[np.ndarray] = None) -> dict[str, Any]:
    """Pure assessment of the LAST sample of a window from sensor arrays only."""
    features = compute_features(t, gps_position, gps_velocity, imu_velocity, peer_res10, 1 + (0 if peer_res10 is None else 1), peer_res30,
                                 include_long=any(name in FEATURE_COLUMNS_LONG for name in model.feature_columns))
    recent = np.flatnonzero(t >= t[-1] - HOLD_S)
    recent_confidence = model.confidence(features.iloc[recent])   # the model scores rows independently: only the hold window is needed
    pick = int(recent[len(recent) - 1 - int(np.argmax(recent_confidence[::-1]))])  # latest of the strongest
    row = features.iloc[[pick]]
    confidence = float(recent_confidence[pick - int(recent[0])])
    values = {name: float(row[name].iloc[0]) for name in FEATURE_COLUMNS}
    sev = float(engine.severity(values["res_pos_w10"], values["res_rate"], values["res_vel"]))
    impact = float(engine.mission_impact(role, battery_pct, remaining_fraction))
    score = float(engine.threat_score(confidence, sev, impact))
    tier = model.tiers.tier(score)
    ranked_candidates = engine.handover_candidates(candidates, min_battery_pct, candidate_roles)
    return {
        "anomaly_confidence": round(confidence, 4), "severity": round(sev, 4), "mission_impact": round(impact, 4),
        "threat_score": round(score, 1), "tier": tier, "threat_score_is_probability": False, "threat_score_formula": engine.THREAT_SCORE_FORMULA,
        "evidence": engine.evidence_items(values, model.reference), "handover_candidates": ranked_candidates,
        "actions": engine.rank_actions(tier, confidence, role, battery_pct, ranked_candidates, min_battery_pct),
        "t": float(t[-1]), "model_id": model.model_id, "evidence_from_s": float(t[pick]), "held": pick != len(t) - 1, "hold_s": HOLD_S,
    }


def _reject_ground_truth(payload: Any) -> None:
    try:
        assert_no_ground_truth_deep(payload, "request")
    except GroundTruthLeak as error:
        raise HTTPException(status_code=422, detail={"code": "ground_truth_rejected", "message": str(error)}) from error


@router.get("/api/research/model-info")
def model_info(_context: AuthContext = Depends(require_permission(Permission.MISSION_READ_OWN))) -> dict:
    model = get_model()
    if model is None:
        return {"available": False, "reason": _loaded["error"] or "No trained model artifact found. Run: python -m app.research.threat_models",
                "formula": engine.THREAT_SCORE_FORMULA}
    metrics = model.metrics
    selected = metrics["models"][metrics["selected_model"]]
    return {
        "available": True, "model_id": model.model_id, "trained_at": metrics["trained_at"], "selected_model": metrics["selected_model"],
        "tiers": metrics["tiers"]["thresholds"], "formula": engine.THREAT_SCORE_FORMULA, "feature_columns": metrics["feature_columns"],
        "test_metrics": {k: v for k, v in (selected.get("test") or {}).items() if not k.startswith("by_")}, "dataset": metrics["dataset"],
        "calibration": {"method": metrics["calibration"]["method"], "brier_test_calibrated": metrics["calibration"].get("brier_test_calibrated")},
        "limits": metrics["limits"], "live_residual_inputs_available": telemetry_store is not None,
        "min_battery_pct_default": engine.DEFAULT_MIN_BATTERY_PCT, "sample_hz": HZ, "handover_rule": handover_rule(),
    }


def handover_rule(min_battery_pct: Optional[float] = None) -> dict[str, Any]:
    """The one handover battery rule. The server enforces it in /decisions; the UI only displays it."""
    minimum = float(MIN_HANDOVER_BATTERY_PCT if min_battery_pct is None else min_battery_pct)
    return {"min_battery_pct": minimum, "enforced_by": "server",
            "message": f"A handover is refused when the receiving aircraft's battery is below {minimum:.0f}%."}


# --- live assessment (PX4 SITL telemetry buffer; nothing is sent in a request body) --------------------------------

telemetry_store: Any = None        # set by main.py to the Px4TelemetryStore; None in a process without telemetry
# A live result depends on the aircraft roles of the viewer's deployed plan (mission impact, handover candidates), so every
# cache below is keyed by that role map: two users whose plans give an aircraft different roles never see each other's result.
_live_cache: dict[tuple, dict[str, Any]] = {}
_live_last: dict[tuple, dict[str, dict[str, Any]]] = {}   # per role map: last computed assessment per aircraft (landed / link lost)
_live_lock = threading.Lock()
_roles_cache: dict[str, tuple[float, dict[str, str]]] = {}
LANDED_KEEP_S = 600.0        # a landed aircraft keeps its last assessment on screen this long; then the monitor waits for a flight
LINK_LOST_KEEP_S = 1800.0    # an aircraft whose link dropped in flight stays listed (stale) this long
UNKNOWN_DISTANCE_M = 2000.0  # the ranking's distance limit: an unknown distance earns no credit
INPUT_MAX_AGE_S = {"GPS_RAW_INT": 2.0, "LOCAL_POSITION_NED": 2.0, "HIGHRES_IMU": 2.0, "ESTIMATOR_STATUS": 6.0, "VIBRATION": 6.0}
VELOCITY_REFERENCE_NOTE = ("The inertial reference is PX4's estimator velocity (LOCAL_POSITION_NED). It is GPS-aided, "
                           "not an independent inertial solution, so slow GPS deviations are harder to see than in the offline simulation.")


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def roles_key(roles: dict[str, str]) -> tuple:
    return tuple(sorted(roles.items()))


def last_assessments(roles: dict[str, str]) -> dict[str, dict[str, Any]]:
    """Last computed assessment per aircraft for one role map."""
    if len(_live_last) > 16 and roles_key(roles) not in _live_last:
        _live_last.clear()
    return _live_last.setdefault(roles_key(roles), {})


def _deployment_roles(context: AuthContext) -> dict[str, str]:
    now = time.monotonic()
    cached = _roles_cache.get(context.user.id)
    if cached is not None and now - cached[0] < 10.0:
        return cached[1]
    roles: dict[str, str] = {}
    try:
        deployment = latest_mission_deployment(context.user.id, include_all=has_permission(context.user.role, Permission.MISSION_READ_ALL))
        if deployment:
            plan, _scenario, _issues = load_pinned_plan(context, deployment["scenario_id"], deployment["scenario_version_id"])
            if plan is not None:
                roles = {item.uav_id: item.role for item in plan.aircraft if item.role in engine.ROLE_WEIGHT}
    except Exception:   # a role is context for mission impact only; never fail the assessment over it
        roles = {}
    if len(_roles_cache) > 64:
        _roles_cache.clear()
    _roles_cache[context.user.id] = (now, roles)
    return roles


def _missing_inputs(inputs: dict[str, Any]) -> list[dict[str, Any]]:
    ages = inputs["ages_seconds"]

    def late(kind: str) -> Optional[str]:
        age = ages.get(kind)
        if age is None:
            return f"{kind} has not been received"
        return f"last {kind} was {age:.1f} s ago" if age > INPUT_MAX_AGE_S[kind] else None

    missing = []
    gps = late("GPS_RAW_INT")
    if gps:
        missing.append(live_inputs.input_message("gps_position", gps))
    elif not (inputs["gps_raw"] or {}).get("valid"):
        missing.append(live_inputs.input_message("gps_position", "the receiver has no 3D fix"))
    elif (inputs["gps_raw"] or {}).get("ground_speed_m_s") is None or (inputs["gps_raw"] or {}).get("course_deg") is None:
        missing.append(live_inputs.input_message("gps_velocity", "GPS_RAW_INT carries no speed or course"))
    for kind, name in (("LOCAL_POSITION_NED", "estimator_velocity"), ("HIGHRES_IMU", "imu"), ("ESTIMATOR_STATUS", "estimator_status"), ("VIBRATION", "vibration")):
        detail = late(kind)
        if detail:
            missing.append(live_inputs.input_message(name, detail))
    if (inputs["battery"] or {}).get("remaining_percent") is None:
        missing.append(live_inputs.input_message("battery", "no battery percentage"))
    return missing


def _live_aircraft(inputs: dict[str, Any], role: str, role_source: str, last_by_aircraft: Optional[dict[str, dict[str, Any]]] = None) -> dict[str, Any]:
    """State and aligned window for one aircraft. No model call here.

    States: idle (on the ground, never assessed or long landed), warming_up, live, inputs_missing, landed (disarmed after an
    assessed flight: the last assessment with its age, whether or not the link is still up), link_lost (the link dropped
    while the aircraft was flying), offline (no link and nothing to show).
    """
    battery = (inputs["battery"] or {}).get("remaining_percent")
    base = {"aircraft_id": inputs["id"], "armed": bool(inputs["armed"]), "role": role, "role_source": role_source,
            "link": {"status": inputs["status"], "telemetry_age_s": inputs["telemetry_age_seconds"], "heartbeat_age_s": inputs["heartbeat_age_seconds"]},
            "flight_mode": inputs["flight_mode"], "rates_hz": inputs["rates_hz"], "missing_inputs": [], "assessment": None, "assessment_age_s": None,
            "landed_age_s": None, "battery_pct": None if battery is None else float(battery), "warming_up": None, "data_quality": None}
    store = last_by_aircraft if last_by_aircraft is not None else last_assessments({})
    last = store.get(inputs["id"])
    connected = inputs["status"] == "connected"
    now = time.monotonic()
    if not inputs["armed"]:
        landed_for = inputs.get("disarmed_for_seconds")
        if last and landed_for is not None and landed_for <= LANDED_KEEP_S:
            # It landed. A link that drops afterwards does not turn a finished flight into "link lost".
            base.update(state="landed", assessment=last["assessment"], assessment_age_s=round(now - last["at"], 1), landed_age_s=round(landed_for, 1))
        else:
            store.pop(inputs["id"], None)
            base["state"] = "idle" if connected else "offline"
        return base
    if not connected:                      # the link dropped while the aircraft was flying
        lost_for = inputs["telemetry_age_seconds"] or 0.0
        if lost_for > LINK_LOST_KEEP_S:
            store.pop(inputs["id"], None)
            base["state"] = "offline"
            return base
        base["state"] = "link_lost"
        if last:   # the last computed values, clearly aged; never refreshed from old data
            base.update(assessment=last["assessment"], assessment_age_s=round(now - last["at"], 1))
        return base
    missing = _missing_inputs(inputs)
    base["missing_inputs"] = missing
    if any(item["required"] for item in missing):
        base["state"] = "inputs_missing"
        return base
    try:
        window = live_inputs.align_window(inputs["gps"], inputs["velocity"], flight_start_t=inputs["flight_start_t"], origin=inputs["origin"])
    except live_inputs.InputUnavailable as error:
        base["missing_inputs"] = [live_inputs.input_message(error.name, error.detail)] + [item for item in missing if item["input"] != error.name]
        base["state"] = "inputs_missing" if inputs["armed_for_seconds"] is None or inputs["armed_for_seconds"] > 5.0 else "warming_up"
        if base["state"] == "warming_up":
            base.update(missing_inputs=missing, warming_up={"seconds_collected": 0.0, "seconds_needed": live_inputs.MIN_SAMPLES / HZ})
        return base
    if not window["ready"]:
        base["state"] = "warming_up"
        base["warming_up"] = {"seconds_collected": window["seconds_collected"], "seconds_needed": window["seconds_needed"]}
        return base
    base["state"] = "live"
    base["_window"] = window
    base["_inputs"] = inputs
    return base


def _data_quality(inputs: dict[str, Any], window: dict[str, Any], notes: list[str]) -> dict[str, Any]:
    estimator, vibration, imu = inputs["estimator_status"], inputs["vibration"], inputs["imu_raw"]
    flags = list(notes)
    if window["restarted_after_gap"]:
        flags.append("The window restarted after a gap in: " + ", ".join(live_inputs.INPUT_LABELS[name] for name in window["restarted_after_gap"]) + ".")
    if not window["window_starts_at_flight_start"]:
        flags.append("The window no longer includes the start of the flight; features scaled by the early-flight residual are neutral.")
    if vibration and any(vibration["accelerometer_clipping"]):
        flags.append("Accelerometer clipping reported by PX4.")
    return {
        "clock": "px4_boot_clock", "sample_hz": window["hz"], "gps_delay_s": window["gps_delay_s"], "velocity": "interval_averaged",
        "window_samples": window["samples"], "window_seconds": round(window["samples"] / window["hz"], 1),
        "velocity_reference": "px4_estimator_velocity", "velocity_reference_note": VELOCITY_REFERENCE_NOTE,
        "vertical_gps_velocity": "not reported by GPS_RAW_INT; the detector uses horizontal components only",
        "estimator": None if estimator is None else {key: estimator[key] for key in ("velocity_test_ratio", "horizontal_position_test_ratio", "horizontal_position_accuracy_m", "flags")},
        "vibration": None if vibration is None else {"vibration_m_s2": vibration["vibration_m_s2"], "accelerometer_clipping": vibration["accelerometer_clipping"]},
        "imu": None if imu is None else {"acceleration_m_s2": imu["acceleration_m_s2"], "source_time_usec": imu["source_time_usec"]},
        "gps": {key: (inputs["gps_raw"] or {}).get(key) for key in ("fix_type", "satellites_visible", "eph_m", "epv_m")},
        "flags": flags,
    }


def compute_live(store: Any, roles: dict[str, str]) -> dict[str, Any]:
    """Assess every aircraft from the server's own telemetry buffer (same feature and model code as /assess)."""
    model = get_model()
    last_by_aircraft = last_assessments(roles)
    rows = []
    for vehicle_id in store.vehicle_ids():
        inputs = store.detector_inputs(vehicle_id)
        if inputs is not None:
            rows.append(_live_aircraft(inputs, roles.get(vehicle_id, "primary"), "deployed_plan" if vehicle_id in roles else "assumed_primary", last_by_aircraft))
    ready = [row for row in rows if row["state"] == "live"]
    for row in ready:
        window, inputs = row["_window"], row["_inputs"]
        if model is None:
            row["state"] = "model_unavailable"
            continue
        n = window["samples"]
        peers10, peers30 = [], []
        for other in ready:
            other_window = other.get("_window")
            if other is not row and other_window is not None and other_window["samples"] >= n:
                peers10.append(residual_series(other_window["t"][-n:], other_window["gps_position"][-n:], other_window["imu_velocity"][-n:], 10.0))
                peers30.append(residual_series(other_window["t"][-n:], other_window["gps_position"][-n:], other_window["imu_velocity"][-n:], 30.0))
        notes = []
        battery = (inputs["battery"] or {}).get("remaining_percent")
        if battery is None:
            notes.append("No battery reading: mission impact assumes a full battery.")
        mission = inputs["mission"] or {}
        if mission.get("total_sequence"):
            remaining = max(0.0, min(1.0, 1.0 - float(mission["current_sequence"]) / float(mission["total_sequence"])))
        else:
            remaining = 1.0
            notes.append("Mission progress unknown: mission impact assumes the whole mission remains.")
        if row["role_source"] != "deployed_plan":
            notes.append("No role in the deployed plan for this aircraft: mission impact assumes a primary aircraft.")
        # Handover candidates: every other aircraft with a live link, flying or on the ground. A value the telemetry does
        # not provide is never invented: an unknown battery makes the aircraft ineligible, and the distance to the task and
        # the workload (no live source) are returned as unavailable and earn no credit in the ranking.
        others = [other for other in rows if other is not row and other["link"]["status"] == "connected"]
        known = {other["aircraft_id"]: {"battery": other["battery_pct"] is not None, "role": other["role_source"] == "deployed_plan"} for other in others}
        candidates = [{"aircraft_id": other["aircraft_id"], "role": other["role"] if known[other["aircraft_id"]]["role"] else "",
                       "battery_pct": other["battery_pct"] if known[other["aircraft_id"]]["battery"] else 0.0,
                       "distance_to_task_m": UNKNOWN_DISTANCE_M, "workload": 1.0} for other in others]
        result = assess_arrays(model, window["t"], window["gps_position"], window["gps_velocity"], window["imu_velocity"], row["role"],
                               100.0 if battery is None else float(battery), remaining,
                               np.median(np.vstack(peers10), axis=0) if peers10 else None,
                               candidates, float(MIN_HANDOVER_BATTERY_PCT), ["standby", "decoy"],
                               np.median(np.vstack(peers30), axis=0) if peers30 else None)
        for candidate in result["handover_candidates"]:
            facts = known[candidate["aircraft_id"]]
            candidate.update(distance_to_task_m=None, workload=None, distance_known=False, workload_known=False, battery_known=facts["battery"], role_known=facts["role"])
            if not facts["role"]:
                candidate.update(role=None, eligible=False, ineligible_reason="role unavailable: this aircraft is not in the deployed plan")
            if not facts["battery"]:
                candidate.update(battery_pct=None, eligible=False, ineligible_reason="battery unavailable: no live battery reading")
        result["handover_candidates_note"] = ("Distance to the task and workload have no live source: they are shown as unavailable and earn no "
                                              "credit in the ranking. An aircraft with an unknown battery or role is never eligible.")
        result.update(aircraft_id=row["aircraft_id"], source="live_telemetry", px4_time_s=round(window["px4_time_s"], 2), flight_time_s=round(float(window["t"][-1]), 1), computed_at=_utc())
        row["assessment"], row["assessment_age_s"] = result, 0.0
        row["data_quality"] = _data_quality(inputs, window, notes)
        last_by_aircraft[row["aircraft_id"]] = {"at": time.monotonic(), "assessment": result}
    for row in rows:
        row.pop("_window", None)
        row.pop("_inputs", None)
    return {"source": "live_telemetry", "generated_at": _utc(), "model_available": model is not None, "model_id": None if model is None else model.model_id,
            "flight_running": any(row["armed"] and row["link"]["status"] == "connected" for row in rows),
            "update_interval_s": LIVE_ASSESSMENT_INTERVAL_S, "handover_rule": handover_rule(), "threat_score_is_probability": False,
            "input_help": [live_inputs.input_message(name) for name in live_inputs.REQUIRED_INPUTS + live_inputs.OPTIONAL_INPUTS],
            "aircraft": rows}


@router.get("/api/research/live")
def live_assessment(context: AuthContext = Depends(require_permission(Permission.MISSION_READ_OWN))) -> dict:
    """Live threat assessment per aircraft, computed on the server from the telemetry buffer. Read-only; takes no body."""
    if telemetry_store is None:
        return {"source": "live_telemetry", "generated_at": _utc(), "model_available": get_model() is not None, "flight_running": False,
                "update_interval_s": LIVE_ASSESSMENT_INTERVAL_S, "handover_rule": handover_rule(), "aircraft": [],
                "unavailable_reason": "PX4 telemetry is not enabled in this backend."}
    return live_payload(context)


def live_payload(context: AuthContext) -> dict[str, Any]:
    """The live result for this user's role map: computed at most once per interval however many clients poll."""
    roles = _deployment_roles(context)
    key = roles_key(roles)
    with _live_lock:
        now = time.monotonic()
        cached = _live_cache.get(key)
        if cached is not None and now - cached["at"] < LIVE_ASSESSMENT_INTERVAL_S:
            return cached["payload"]
        payload = compute_live(telemetry_store, roles)
        if len(_live_cache) > 16:
            _live_cache.clear()
        _live_cache[key] = {"at": now, "payload": payload}
        return payload


@router.post("/api/research/assess")
def assess(body: dict = Body(...), _context: AuthContext = Depends(require_permission(Permission.MISSION_READ_OWN)),
           _csrf: AuthContext = Depends(require_csrf)) -> dict:
    _reject_ground_truth(body)
    try:
        request = AssessRequest.model_validate(body)
    except ValidationError as error:
        raise HTTPException(status_code=422, detail={"code": "assess_invalid", "message": "The assessment window is invalid.",
                                                      "issues": [{"field": ".".join(str(p) for p in item["loc"]), "message": item["msg"]} for item in error.errors()]}) from error
    model = get_model()
    if model is None:
        raise HTTPException(status_code=503, detail={"code": "model_unavailable", "message": "No trained threat model is available. Run: python -m app.research.threat_models"})
    t, gp, gv, iv = _arrays(request.samples)
    if np.any(np.diff(t) <= 0):
        raise HTTPException(status_code=422, detail={"code": "assess_invalid", "message": "Sample times must be strictly increasing."})
    peer_res = peer_res30 = None
    peer_series, peer_series30 = [], []
    for peer in request.peers:
        pt, pgp, _pgv, piv = _arrays(peer.samples)
        if len(pt) >= len(t):
            peer_series.append(residual_series(pt[-len(t):], pgp[-len(t):], piv[-len(t):], 10.0))
            peer_series30.append(residual_series(pt[-len(t):], pgp[-len(t):], piv[-len(t):], 30.0))
    if peer_series:
        peer_res, peer_res30 = np.median(np.vstack(peer_series), axis=0), np.median(np.vstack(peer_series30), axis=0)
    result = assess_arrays(model, t, gp, gv, iv, request.role, request.battery_pct, request.remaining_fraction, peer_res,
                           [item.model_dump() for item in request.handover_candidates], request.min_battery_pct, list(request.candidate_roles), peer_res30)
    result["aircraft_id"] = request.aircraft_id
    return result


class DecisionRequest(StrictModel):
    aircraft_id: str = Field(pattern=r"^px4-[1-9][0-9]{0,2}$")
    action: Literal["continue_monitor", "increase_monitoring", "dead_reckoning_fallback", "isolate_aircraft", "handover_to_standby", "return_to_launch"]
    handover_candidate: Optional[str] = Field(default=None, pattern=r"^px4-[1-9][0-9]{0,2}$")
    source: Literal["live_telemetry", "offline_replay"]
    source_name: Optional[str] = Field(default=None, max_length=120)
    tier: Optional[Literal["Low", "Medium", "High", "Critical"]] = None
    threat_score: Optional[float] = Field(default=None, ge=0, le=100)
    replay_id: Optional[str] = Field(default=None, pattern=r"^[0-9a-f]{32}$")     # offline replay: lets the server check the candidate's battery
    replay_t_s: Optional[float] = Field(default=None, ge=0, le=MAX_REPLAY_S)


def _refuse_handover(code: str, message: str) -> HTTPException:
    return HTTPException(status_code=409, detail={"code": code, "message": message, "handover_rule": handover_rule()})


def check_handover(decision: "DecisionRequest", context: AuthContext) -> None:
    """Server-side handover rule: the receiving aircraft's battery, as the SERVER knows it, must be at or above the minimum."""
    candidate = decision.handover_candidate
    if candidate == decision.aircraft_id:
        raise _refuse_handover("handover_same_aircraft", "An aircraft cannot hand over to itself.")
    if decision.source == "live_telemetry":
        minimum = float(MIN_HANDOVER_BATTERY_PCT)
        inputs = telemetry_store.detector_inputs(candidate) if telemetry_store is not None else None
        battery = None if inputs is None or inputs["status"] != "connected" else (inputs["battery"] or {}).get("remaining_percent")
        if battery is None:
            raise _refuse_handover("handover_battery_unknown", f"Handover refused: {candidate} has no live battery reading, so the {minimum:.0f}% minimum cannot be checked.")
    else:
        replay = REPLAY_STORE.get(decision.replay_id or "")
        entry = None if replay is None or replay["user_id"] != context.user.id else replay["sims"].get(candidate)
        if entry is None or decision.replay_t_s is None:
            raise _refuse_handover("handover_battery_unknown", f"Handover refused: the server cannot check {candidate}'s battery for this replay (replay_id and replay_t_s are required).")
        sim = entry["sim"]
        minimum = float(replay["min_battery_pct"])
        battery = float(sim.battery_pct[min(int(round(decision.replay_t_s / DT)), len(sim.battery_pct) - 1)])
    if float(battery) < minimum:
        raise _refuse_handover("handover_below_min_battery", f"Handover refused: {candidate} battery {float(battery):.0f}% is below the {minimum:.0f}% minimum.")


@router.post("/api/research/decisions", status_code=201)
def record_decision(request: Request, body: dict = Body(...), context: AuthContext = Depends(require_permission(Permission.SCENARIO_CREATE)),
                    _csrf: AuthContext = Depends(require_csrf)) -> dict:
    """Records the operator's choice. It does not command any aircraft."""
    _reject_ground_truth(body)
    try:
        decision = DecisionRequest.model_validate(body)
    except ValidationError as error:
        raise HTTPException(status_code=422, detail={"code": "decision_invalid", "message": "The decision is invalid.",
                                                      "issues": [{"field": ".".join(str(p) for p in item["loc"]), "message": item["msg"]} for item in error.errors()]}) from error
    if decision.action == "handover_to_standby" and not decision.handover_candidate:
        raise HTTPException(status_code=422, detail={"code": "decision_invalid", "message": "Choose a handover candidate aircraft.", "issues": [{"field": "handover_candidate", "message": "required for handover"}]})
    if decision.action != "handover_to_standby" and decision.handover_candidate:
        raise HTTPException(status_code=422, detail={"code": "decision_invalid", "message": "A handover candidate is only valid for a handover.", "issues": [{"field": "handover_candidate", "message": "not allowed"}]})
    if decision.action == "handover_to_standby":
        check_handover(decision, context)
    tier, score = decision.tier, decision.threat_score
    if decision.source == "live_telemetry":      # logged with the server's own score and tier at this moment, not the client's
        tier = score = None
        if telemetry_store is not None:
            row = next((item for item in live_payload(context)["aircraft"] if item["aircraft_id"] == decision.aircraft_id), None)
            if row is not None and row["state"] == "live" and row["assessment"] is not None:
                tier, score = row["assessment"]["tier"], row["assessment"]["threat_score"]
    saved = record_research_decision(context.user.id, context.user.username, decision.aircraft_id, decision.action, decision.handover_candidate,
                                     decision.source, decision.source_name, tier, score)
    add_audit_event(action="research_decision_recorded", outcome="success", reason="Operator choice recorded; no command was sent.",
                    request_id=getattr(request.state, "request_id", "request-unavailable"), actor_id=context.user.id, actor_name=context.user.full_name,
                    target_type="research_decision", target_id=saved["id"], metadata={"aircraft_id": decision.aircraft_id, "action": decision.action, "source": decision.source})
    return saved


@router.get("/api/research/decisions")
def decisions(_context: AuthContext = Depends(require_permission(Permission.MISSION_READ_OWN))) -> dict:
    return {"decisions": list_research_decisions(50)}


# --- offline replays (synthetic telemetry over a pinned experiment) -----------------------------------------------

class ReplayRequest(StrictModel):
    experiment_id: str = Field(min_length=1, max_length=64)


def _route_path_enu(aircraft) -> tuple[np.ndarray, float]:
    route = aircraft.route
    origin = (aircraft.start_location.latitude, aircraft.start_location.longitude)
    points = [to_enu(origin, w.latitude, w.longitude)[:2] for w in route.waypoints]
    return np.array(points if len(points) > 1 else [(0.0, 0.0), (1.0, 1.0)]), float(aircraft.speed_mps or 5.0)


@router.post("/api/research/replays", status_code=201)
def create_replay(body: dict = Body(...), context: AuthContext = Depends(require_permission(Permission.MISSION_READ_OWN)),
                  _csrf: AuthContext = Depends(require_csrf)) -> dict:
    """Simulates telemetry for a pinned experiment. The response contains sensors only; the schedule stays server-side."""
    try:
        request = ReplayRequest.model_validate(body)
    except ValidationError as error:
        raise HTTPException(status_code=422, detail={"code": "replay_invalid", "message": "experiment_id is required."}) from error
    visible = {item["id"] for item in list_attack_experiments(context.user.id, include_all=has_permission(context.user.role, Permission.MISSION_READ_ALL))}
    experiment = get_attack_experiment(request.experiment_id) if request.experiment_id in visible else None
    if experiment is None:
        raise HTTPException(status_code=404, detail="Experiment not found.")
    plan, _scenario, issues = load_pinned_plan(context, experiment["scenario_id"], experiment["scenario_version_id"])
    if plan is None:
        raise HTTPException(status_code=422, detail={"code": "replay_invalid", "message": issues[0]["message"]})
    seed = int(experiment["seed"])
    attacks = [AttackConfig.model_validate(item) for item in experiment["config"]["attacks"]]
    aircraft_payload, sims = [], {}
    duration = 0.0
    routed = [item for item in plan.aircraft if item.route and item.start_location]
    if not routed:
        raise HTTPException(status_code=422, detail={"code": "replay_invalid", "message": "The pinned plan has no generated routes to replay."})
    for index, item in enumerate(routed):
        path, speed = _route_path_enu(item)
        total_s = min(float(item.route.duration_s), MAX_REPLAY_S)
        rng = np.random.default_rng([seed, index, 77])
        sim = simulate_aircraft(rng, item.uav_id, item.role or "standby", path, [a for a in attacks if a.target_aircraft == item.uav_id],
                                seed + 101 * index, max(total_s, 20.0), speed=speed)
        sims[item.uav_id] = {"sim": sim, "reassignable": item.reassignable}
        duration = max(duration, sim.total_s)
        aircraft_payload.append({
            "aircraft_id": item.uav_id, "role": item.role, "reassignable": item.reassignable,
            "t": [round(float(v), 2) for v in sim.t],
            "gps_position": np.round(sim.gps.position, 3).tolist(), "gps_velocity": np.round(sim.gps.velocity, 3).tolist(),
            "imu_velocity": np.round(sim.imu_velocity, 3).tolist(), "battery_pct": np.round(sim.battery_pct, 2).tolist(),
        })
    replay_id = uuid.uuid4().hex
    REPLAY_STORE[replay_id] = {"user_id": context.user.id, "name": experiment["name"], "sims": sims, "attacks": experiment["config"]["attacks"],
                               "finished": False, "min_battery_pct": experiment["config"]["handover"]["min_battery_pct"],
                               "candidate_roles": experiment["config"]["handover"]["candidate_roles"]}
    while len(REPLAY_STORE) > MAX_REPLAYS:
        REPLAY_STORE.popitem(last=False)
    return {"replay_id": replay_id, "experiment_name": experiment["name"], "experiment_id": experiment["id"], "hz": HZ, "duration_s": round(duration, 1),
            "aircraft": aircraft_payload, "min_battery_pct": experiment["config"]["handover"]["min_battery_pct"],
            "candidate_roles": experiment["config"]["handover"]["candidate_roles"], "source": "offline_replay"}


def _replay(replay_id: str, context: AuthContext) -> dict[str, Any]:
    replay = REPLAY_STORE.get(replay_id)
    if replay is None or replay["user_id"] != context.user.id:
        raise HTTPException(status_code=404, detail="Replay not found.")
    return replay


@router.post("/api/research/replays/{replay_id}/finish")
def finish_replay(replay_id: str, context: AuthContext = Depends(require_permission(Permission.MISSION_READ_OWN)), _csrf: AuthContext = Depends(require_csrf)) -> dict:
    _replay(replay_id, context)["finished"] = True
    return {"finished": True}


@router.get("/api/research/replays/{replay_id}/evaluation")
def replay_evaluation(replay_id: str, context: AuthContext = Depends(require_permission(Permission.MISSION_READ_OWN))) -> dict:
    """Evaluation summary. Reveals the attack schedule, so it is refused until the replay is marked finished."""
    replay = _replay(replay_id, context)
    if not replay["finished"]:
        raise HTTPException(status_code=409, detail={"code": "replay_not_finished", "message": "The attack schedule is revealed only after the replay ends."})
    model = get_model()
    if model is None:
        raise HTTPException(status_code=503, detail={"code": "model_unavailable", "message": "No trained threat model is available."})
    results = []
    for aircraft_id, entry in replay["sims"].items():
        sim = entry["sim"]
        window = 600  # the whole history up to the API limit: the features use the flight's own early-flight scale
        stride = max(1, int(EVAL_STRIDE_S * HZ))
        tiers, times = [], []
        for end in range(stride, len(sim.t) + 1, stride):
            start = max(0, end - window)
            result = assess_arrays(model, sim.t[start:end], sim.gps.position[start:end], sim.gps.velocity[start:end], sim.imu_velocity[start:end],
                                   sim.role, float(sim.battery_pct[end - 1]), 1.0 - float(sim.t[end - 1]) / max(sim.total_s, 1.0), None, [], replay["min_battery_pct"], replay["candidate_roles"])
            tiers.append(result["tier"])
            times.append(float(sim.t[end - 1]))
        alert = [tier != "Low" for tier in tiers]
        sustained_alert = [index >= 1 and alert[index] and alert[index - 1] for index in range(len(alert))]
        attacked = sim.truth.is_attacked
        start_t = float(sim.t[int(np.argmax(attacked))]) if attacked.any() else None
        first = next((times[i] for i, flag in enumerate(sustained_alert) if flag and (start_t is None or times[i] >= start_t)), None)
        onsets = [i for i, flag in enumerate(sustained_alert) if flag and (i == 0 or not sustained_alert[i - 1])]
        # Un-attacked time within RECOVERY_S after an attack ends is neither normal flight nor a false alarm (the residual window still sees the GPS return).
        last_attacked = float(sim.t[np.flatnonzero(attacked)[-1]]) if attacked.any() else None
        recovering = lambda moment: last_attacked is not None and last_attacked < moment <= last_attacked + RECOVERY_S
        attacked_at = lambda moment: bool(attacked[min(int(round(moment / DT)), len(attacked) - 1)])
        false_alarms = sum(1 for i in onsets if not attacked_at(times[i]) and not recovering(times[i]))
        recovery_samples = int(sum(1 for moment in sim.t if recovering(float(moment)) and not attacked_at(float(moment))))
        clean_hours = max(float(((~attacked).sum() - recovery_samples) * DT) / 3600.0, 1e-9)
        error = np.linalg.norm((sim.gps.position - sim.true_position)[:, :2], axis=1)
        results.append({
            "aircraft_id": aircraft_id, "role": sim.role, "attacked": bool(attacked.any()),
            "attack_start_s": start_t, "detection_latency_s": round(first - start_t, 1) if (first is not None and start_t is not None) else None,
            "detected": first is not None if attacked.any() else None, "false_alarms": false_alarms,
            "false_alarms_per_hour": round(false_alarms / clean_hours, 2), "max_gps_position_error_m": round(float(error.max()), 1),
            "mean_gps_position_error_m": round(float(error.mean()), 1), "max_tier": max(tiers, key=engine.TIERS.index) if tiers else "Low",
            "mission_completion_pct": None, "handover_time_s": None,
        })
    return {"experiment_name": replay["name"], "attacks": replay["attacks"], "aircraft": results,
            "not_measured": {"mission_completion_pct": "needs the experiment runner", "handover_time_s": "no handover is executed in offline replay"}}
