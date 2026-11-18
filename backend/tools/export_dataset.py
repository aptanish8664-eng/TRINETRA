"""Export the detector dataset, model metrics and live records to one spreadsheet: exports/trinetra_dataset_<date>.xlsx.

One command re-creates the file (run from D:\\TRINETRA\\backend):

    ..\\.venv\\Scripts\\python.exe tools\\export_dataset.py [--out-dir ..\\exports] [--max-window-rows N]

It only READS what already exists: backend/research_artifacts/{dataset,metrics.json,model_card.md}, backend/docs/ML_PIPELINE.md and the
SQLite file backend/trinetra.db (opened read-only; only execution, experiment and decision tables are read, never accounts or sessions).
It does not change, regenerate or retrain anything. What is not stored is said so in the README sheet.

Excel holds at most 1,048,576 rows per sheet. If the window table is longer, the full table is written to windows_full.csv next to the
workbook and the sheet holds a stratified sample (same share of attacked rows per attack type, seeded), described in the README.
"""
from __future__ import annotations

import argparse
import ast
import csv
import datetime as dt
import hashlib
import json
import re
import sqlite3
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

BACKEND = Path(__file__).resolve().parents[1]
ARTIFACTS = BACKEND / "research_artifacts"
DATASET = ARTIFACTS / ("dataset_v3" if (ARTIFACTS / "dataset_v3").exists() else "dataset")      # the dataset the current model was trained on
EXCEL_MAX_ROWS = 1_048_576
SAMPLE_SEED = 20261116

# What each model input means, written from backend/app/research/threat_data.py (compute_features). Units: m, m/s, s, degrees, or ratio.
FEATURE_HELP: dict[str, tuple[str, str, str]] = {
    "res_pos_w10": ("m", "Horizontal distance between the GPS position and the IMU dead-reckoned position anchored 10 s earlier.", "residual_series(window 10 s)"),
    "res_pos_w30": ("m", "Same residual with a 30 s anchor.", "residual_series(window 30 s)"),
    "res_vel": ("m/s", "Horizontal difference between GPS velocity and IMU velocity.", "norm(gps_velocity - imu_velocity)"),
    "heading_res_deg": ("deg", "Angle between the GPS and IMU velocity vectors (0 when either is almost still).", "arccos of the cosine of the two horizontal velocities"),
    "innov_ratio": ("ratio", "Squared innovation of a simple EKF-like filter divided by its nominal variance.", "innovation^2 / INNOV_S"),
    "res_rate": ("m/s", "Growth rate of res_pos_w10 over the last 2 s.", "(res10[i] - res10[i-lag]) / (lag*dt)"),
    "res10_mean": ("m", "Mean of res_pos_w10 over the last 10 s.", "rolling mean"),
    "res10_std": ("m", "Standard deviation of res_pos_w10 over the last 10 s.", "rolling std"),
    "res10_max": ("m", "Maximum of res_pos_w10 over the last 10 s.", "rolling max"),
    "resvel_max5": ("m/s", "Maximum of res_vel over the last 5 s.", "rolling max"),
    "speed": ("m/s", "Horizontal GPS speed.", "norm(gps_velocity)"),
    "cross_res10": ("m", "res_pos_w10 minus the median of the other aircraft's (0 for one aircraft).", "own residual - others' median"),
    "n_aircraft": ("count", "Number of aircraft in the flight.", "constant per flight"),
    "res_pos_w60": ("m", "Same residual with a 60 s anchor.", "residual_series(window 60 s)"),
    "res10_z": ("ratio", "res_pos_w10 divided by this flight's own early (5-30 s) residual scale.", "_ratio(res10, early scale)"),
    "res30_z": ("ratio", "res_pos_w30 divided by its early scale.", "_ratio"),
    "res60_z": ("ratio", "res_pos_w60 divided by its early scale.", "_ratio"),
    "resvel_z": ("ratio", "res_vel divided by its early scale.", "_ratio"),
    "innov_z": ("ratio", "innov_ratio divided by its early scale.", "_ratio"),
    "res30_rate10": ("m/s", "Change of res_pos_w30 over 10 s, per second.", "lagged difference / 10"),
    "res60_rate10": ("m/s", "Change of res_pos_w60 over 10 s, per second.", "lagged difference / 10"),
    "res10_accel": ("m/s^2", "Change of the 10 s growth rate of res_pos_w10.", "lagged difference of res10 rate / 10"),
    "res30_accel": ("m/s^2", "Change of the 10 s growth rate of res_pos_w30.", "lagged difference of res30 rate / 10"),
    "noise_std10": ("m", "Scatter of the per-step GPS displacement minus the IMU-predicted displacement over 10 s.", "sqrt(var x + var y) of step innovation"),
    "noise_ratio": ("ratio", "noise_std10 divided by its early scale.", "_ratio"),
    "var_ratio": ("ratio", "Short (6 s) step-innovation variance divided by the preceding 20 s variance (capped at 10000).", "var_short / var_long"),
    "velpos_inconsist": ("m/s", "Reported GPS velocity (4 s mean) minus the derivative of the GPS position over 4 s.", "norm(mean_reported - pos_derivative)"),
    "velpos_z": ("ratio", "velpos_inconsist divided by its early scale.", "_ratio"),
    "gps_travel_3s": ("m", "Distance the GPS position moved in the last 3 s.", "norm(pos[i] - pos[i-3 s])"),
    "imu_travel_3s": ("m", "Distance the IMU says the aircraft moved in the last 3 s.", "norm(cumsum(imu v dt) difference)"),
    "freeze_ratio": ("ratio", "imu_travel_3s / (gps_travel_3s + 0.5) when the IMU says the aircraft moves, else 1.", "freeze check"),
    "frozen_run": ("samples", "Number of consecutive samples (max 40) in which the GPS position did not change.", "run counter"),
    "cross_res30": ("m", "res_pos_w30 minus the median of the other aircraft's (0 for one aircraft).", "own residual - others' median"),
    "cross_z10": ("ratio", "cross_res10 divided by the early scale of res_pos_w10.", "_ratio"),
    "cusum_innov": ("score", "CUSUM (k = 4) of innov_ratio.", "cusum_array(innov_ratio, 4.0)"),
    "res_pos_w90": ("m", "Same residual with a 90 s anchor (long-window feature set).", "residual_series(window 90 s)"),
    "res_pos_w120": ("m", "Same residual with a 120 s anchor.", "residual_series(window 120 s)"),
    "res90_z": ("ratio", "res_pos_w90 divided by its early scale.", "_ratio"),
    "res120_z": ("ratio", "res_pos_w120 divided by its early scale.", "_ratio"),
    "res90_rate10": ("m/s", "Change of res_pos_w90 over 10 s, per second.", "lagged difference / 10"),
    "res120_rate10": ("m/s", "Change of res_pos_w120 over 10 s, per second.", "lagged difference / 10"),
}
META_HELP = {
    "flight_id": "Synthetic flight identifier (F0000...).",
    "aircraft_id": "Aircraft within the flight (px4-1, px4-2, ...). Rows of one flight are time-ordered per aircraft.",
    "t": "Sample time in seconds from the start of the flight (0.5 s sampling, 2 Hz). The features of a row use only samples up to and including this time.",
    "battery_pct": "Simulated battery percentage. Used by the threat score's mission-impact part, NOT a model input.",
    "role": "Aircraft role in the simulated mission (primary, decoy, standby). Metadata, NOT a model input.",
    "remaining_fraction": "Fraction of the route still to fly. Used by the mission-impact part, NOT a model input.",
    "position_error_m": "Simulator truth: the distance between reported and true position. GROUND TRUTH: must never be a feature (it is kept out of the model's inputs).",
}


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def flatten(prefix: str, value: Any, rows: list[tuple[str, Any]]) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            flatten(f"{prefix}.{key}" if prefix else str(key), item, rows)
    elif isinstance(value, list):
        if all(not isinstance(item, (dict, list)) for item in value) and len(value) <= 12:
            rows.append((prefix, ", ".join(str(item) for item in value)))
        else:
            for index, item in enumerate(value):
                flatten(f"{prefix}[{index}]", item, rows)
    else:
        rows.append((prefix, value))


def parse_attack(text: Any) -> dict[str, Any]:
    if not isinstance(text, str) or not text.strip():
        return {}
    try:
        return dict(ast.literal_eval(text))
    except (ValueError, SyntaxError):
        return {}


def build_windows(max_rows: int) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    """Returns (sheet table, full table, info). Labels come from the hidden label file and are joined by flight, aircraft and time."""
    metrics = read_json(ARTIFACTS / "metrics.json")
    features = pd.read_csv(DATASET / "features.csv")
    labels = pd.read_csv(DATASET / "labels_HIDDEN.csv")
    flights = pd.read_csv(DATASET / "flights.csv")
    expected = list(metrics["feature_columns"])
    missing = [name for name in expected if name not in features.columns]
    if missing:
        raise SystemExit(f"features.csv lacks model features: {missing}")
    if len(features) != len(labels) or not (features[["flight_id", "aircraft_id", "t"]].values == labels[["flight_id", "aircraft_id", "t"]].values).all():
        raise SystemExit("features.csv and labels_HIDDEN.csv are not row-aligned; refusing to guess")
    attack = flights["attack"].map(parse_attack)
    flights = flights.assign(attack_start_s=attack.map(lambda a: a.get("start_s")), attack_duration_s=attack.map(lambda a: a.get("duration_s")),
                             attack_magnitude=attack.map(lambda a: a.get("magnitude")), attack_direction_deg=attack.map(lambda a: a.get("direction_deg")),
                             attack_target=attack.map(lambda a: a.get("target_aircraft")))
    info = flights.set_index("flight_id")
    frame = features[["flight_id", "aircraft_id", "t"] + expected].copy()
    frame["LABEL_is_attacked"] = labels["is_attacked"].astype(bool).map({True: "yes", False: "no"})
    frame["LABEL_attack_type"] = labels["attack_type"].fillna("")
    for column, source in (("LABEL_cell", "cell"), ("LABEL_attack_start_s", "attack_start_s"), ("LABEL_attack_duration_s", "attack_duration_s"),
                           ("LABEL_attack_magnitude", "attack_magnitude"), ("LABEL_attack_direction_deg", "attack_direction_deg"), ("META_split", "split")):
        frame[column] = frame["flight_id"].map(info[source])
    for column in ("battery_pct", "role", "remaining_fraction", "position_error_m"):
        if column in features.columns:
            frame[f"META_{column}"] = features[column]
    full = frame
    sampled = False
    if len(frame) > max_rows:
        rng = np.random.default_rng(SAMPLE_SEED)
        strata = frame["LABEL_attack_type"].where(frame["LABEL_is_attacked"] == "yes", "normal")
        keep = pd.Series(False, index=frame.index)
        share = max_rows / len(frame)
        for _, index in frame.groupby(strata).groups.items():
            picks = rng.choice(np.asarray(index), size=max(1, int(round(len(index) * share))), replace=False)
            keep.loc[picks] = True
        frame = frame[keep]
        sampled = True
    return frame, full, {"expected_features": expected, "sampled": sampled, "total_rows": len(full), "sheet_rows": len(frame), "flights": flights}


def stratified_preview(full: pd.DataFrame, size: int) -> tuple[pd.DataFrame, dict[str, tuple[int, int]]]:
    """Exactly `size` rows (or all of them when there are fewer) with the same share of normal rows and of each attack type as the full table.

    Per stratum (normal, or the attack type): rows = size x stratum share, rounded by largest remainder so the total is exactly `size`, at least one row per
    stratum; rows are picked at random with the seed SAMPLE_SEED, then written in the original order. Rows are sampled one by one, not whole flights, so a
    flight in the preview has gaps: it is for looking at and sharing, not for training or for re-running the detector's trailing windows.
    """
    strata = full["LABEL_attack_type"].where(full["LABEL_is_attacked"] == "yes", "normal")
    if len(full) <= size:
        counts = strata.value_counts()
        return full, {name: (int(count), int(count)) for name, count in counts.items()}
    counts = strata.value_counts()
    exact = counts * size / len(full)
    take = exact.apply(np.floor).astype(int).clip(lower=1)
    order = (exact - exact.apply(np.floor)).sort_values(ascending=False).index
    index = 0
    while take.sum() < size:
        take[order[index % len(order)]] += 1
        index += 1
    while take.sum() > size:
        largest = take.idxmax()
        take[largest] -= 1
    rng = np.random.default_rng(SAMPLE_SEED)
    keep = []
    for name, rows in strata.groupby(strata).groups.items():
        keep.extend(rng.choice(np.asarray(rows), size=int(take[name]), replace=False).tolist())
    chosen = full.loc[sorted(keep)]
    return chosen, {name: (int(counts[name]), int(take[name])) for name in counts.index}


def flights_sheet(flights: pd.DataFrame, features: pd.DataFrame) -> pd.DataFrame:
    speed = features.groupby("flight_id")["speed"].agg(speed_mean_mps="mean", speed_max_mps="max")
    out = flights.drop(columns=["attack"]).merge(speed, left_on="flight_id", right_index=True, how="left")
    out["had_attack"] = out["attack_type"].notna().map({True: "yes", False: "no"})
    return out.rename(columns={"seed": "flight_seed"}).round({"speed_mean_mps": 2, "speed_max_mps": 2})


def attacks_sheet() -> pd.DataFrame:
    sys.path.insert(0, str(BACKEND))
    from app.research.attacks import ATTACK_SPECS, DURATION_RANGE_S, FIELD_HELP, OFFSET_RAMP_S, START_RANGE_S  # the generator's own definition

    rows = []
    for spec in ATTACK_SPECS.values():
        rows.append({"attack_type": spec.type, "label": spec.label, "meaning": spec.summary, "magnitude_name": spec.magnitude.label, "unit": spec.magnitude.unit,
                     "magnitude_min": spec.magnitude.minimum, "magnitude_max": spec.magnitude.maximum, "magnitude_default": spec.magnitude.default,
                     "uses_direction": "yes" if spec.uses_direction else "no", "duration_range_s": f"{DURATION_RANGE_S[0]}-{DURATION_RANGE_S[1]}",
                     "start_range_s": f"{START_RANGE_S[0]}-{START_RANGE_S[1]}", "note": f"offset ramps in over {OFFSET_RAMP_S} s" if spec.type == "offset" else ""})
    out = pd.DataFrame(rows)
    notes = pd.DataFrame([{"attack_type": "(dataset)", "label": "How this dataset used the ranges", "meaning":
                           "Dataset flights draw start 30 s to 55% of the flight, duration 20-50 s (hard cases: small drift 30-60 s at 0.15 m/s, noise burst 5-10 s at 6 m, brief jump 2-4 s at 12 m), and one attack per attacked flight. See backend/app/research/threat_data.py (_attack_for_cell)."}])
    return pd.concat([out, notes], ignore_index=True)


def metrics_sheets() -> tuple[pd.DataFrame, pd.DataFrame]:
    metrics = read_json(ARTIFACTS / "metrics.json")
    rows: list[tuple[str, Any]] = []
    for key in ("model_id", "trained_at", "artifact_sha256", "selected_model", "selection_rule", "false_alarm_budget_per_hour", "test_evaluated", "bootstrap",
                "dataset", "models", "feature_set_trial", "calibration", "threat_score_formula", "leakage_controls", "limits"):
        if key in metrics:
            flatten(key, metrics[key], rows)
    all_metrics = pd.DataFrame(rows, columns=["metric_path", "value"])
    tiers = metrics.get("tiers", {})
    tier_rows: list[tuple[str, Any]] = []
    flatten("tiers", tiers, tier_rows)
    tier_table = pd.DataFrame(tier_rows, columns=["item", "value"])
    return all_metrics, tier_table


def live_sheets() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    path = BACKEND / "trinetra.db"
    connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        runs = pd.read_sql_query(
            "select e.id as execution_id, e.aircraft_id, e.state, e.reason, e.started_by_role, e.created_at, e.finished_at, "
            "(select count(*) from mission_execution_events v where v.execution_id = e.id) as event_count from mission_executions e order by e.created_at", connection)
        decisions = pd.read_sql_query("select id, aircraft_id, action, source, source_name, tier, threat_score, created_at from research_decisions order by created_at", connection)
        results = pd.read_sql_query("select * from experiment_results", connection)
        experiments = pd.read_sql_query("select id, name, scenario_id, scenario_version_id, preset_id, seed, status, config_hash, created_at, updated_at from attack_experiments", connection)
    finally:
        connection.close()
    created = pd.to_datetime(runs["created_at"], errors="coerce")
    finished = pd.to_datetime(runs["finished_at"], errors="coerce")
    runs.insert(5, "duration_s", (finished - created).dt.total_seconds().round(1))
    return runs, decisions, results, experiments


def documented_live_baseline() -> pd.DataFrame:
    """The live baseline table exactly as written in backend/docs/ML_PIPELINE.md (the assessments themselves are not stored in the database)."""
    lines = (BACKEND / "docs" / "ML_PIPELINE.md").read_text(encoding="utf-8").splitlines()
    start = next((i for i, line in enumerate(lines) if line.startswith("**Live baseline")), None)
    if start is None:
        return pd.DataFrame([{"note": "The live baseline table was not found in ML_PIPELINE.md."}])
    table = [line for line in lines[start:start + 14] if line.startswith("|")]
    rows = [[cell.strip() for cell in line.strip("|").split("|")] for line in table if not re.match(r"^\|[\s\-|]+\|$", line)]
    return pd.DataFrame(rows[1:], columns=rows[0])


def readme_lines(info: dict[str, Any], model_card: str, today: str, preview: dict[str, Any] | None = None) -> list[tuple[str, str]]:
    manifest = read_json(DATASET / "manifest.json")
    metrics = read_json(ARTIFACTS / "metrics.json")
    sampled = (f"The window table is a stratified SAMPLE of {info['sheet_rows']} of {info['total_rows']} rows (seed {SAMPLE_SEED}, same share per attack type); the full table is windows_full.csv."
               if info["sampled"] else f"The Windows sheet holds ALL {info['total_rows']} rows (it fits Excel's {EXCEL_MAX_ROWS}-row limit); no sampling was applied.")
    if preview is not None:
        shares = "; ".join(f"{name}: {taken} of {total}" for name, (total, taken) in sorted(preview["strata"].items()))
        sampled = (f"PREVIEW: the Windows sheet holds a stratified sample of {info['sheet_rows']} of {info['total_rows']} rows, so the file is small enough to share. The sample keeps the share of normal rows and of each attack type "
                   f"(rows taken per stratum: {shares}); the rounding is by largest remainder, at least one row per stratum, picked at random with seed {SAMPLE_SEED}. Rows are sampled one by one, not whole flights, so a flight has gaps: "
                   f"use it to look at and share the data, not to train or to re-run the detector. Every other sheet is complete. The full table is in {preview['full_name']}.")
    return [
        ("What this is", f"Export made on {today} by backend/tools/export_dataset.py from files already in the repository. Nothing was regenerated, retrained or changed." + (" This is the PREVIEW workbook." if preview else "")),
        ("Sheets", "README, Data dictionary, Windows (model inputs + labels + metadata), Flights, Attacks, Model metrics, Tier thresholds, Live baseline, Run records."),
        ("How the data was made", f"Synthetic flights from the deterministic abstract simulator (backend/app/research/threat_data.py, generate_dataset), profile '{manifest.get('profile')}', "
         f"dataset seed {manifest.get('seed')}, config hash {manifest.get('config_hash')}, {manifest.get('total_flights')} flights, {manifest.get('total_rows')} samples at 2 Hz. "
         "Each flight has its own seed (Flights sheet, flight_seed). Attacks are injected by backend/app/research/attacks.py."),
        ("Model", f"Model id {metrics.get('model_id')}, trained {metrics.get('trained_at')}, selected detector {metrics.get('selected_model')}, artifact sha256 {metrics.get('artifact_sha256')}. "
         f"The model has {len(metrics['feature_columns'])} input features (the Windows sheet lists exactly these, in this order); an earlier description of 35 features is out of date."),
        ("Windows sheet", sampled + " One row is one 0.5 s sample whose features use only earlier samples (causal trailing windows of 3 s to 120 s); there is no single window start/end, so the sheet has the sample time t instead. "
         "Blue headers = model inputs. Red headers (LABEL_*) = ground truth, never a feature. Grey headers (META_*) = flight metadata or simulator truth, never a feature."),
        ("Splits", "Train / validation / test are split BY FLIGHT (never by row), 60/20/20 (META_split)."),
        ("Not stored", "Path type and per-flight speed profile are not stored in flights.csv (speed_mean_mps / speed_max_mps are computed from the 'speed' feature). Live threat assessments are NOT stored per sample in the database; "
         "the Live baseline sheet therefore shows (a) the real flights recorded in the database and (b) the live baseline table exactly as written in backend/docs/ML_PIPELINE.md. Attack parameters exist per flight, not per window row beyond the flight-level columns."),
        ("Caveats", "1) The flights are synthetic and the attacks are injected; there are no real attacks. 2) The ground truth is the label and is never a feature (position_error_m is simulator truth and is excluded from the inputs). "
         "3) The threat score is 100 * confidence * (0.65 * severity + 0.35 * mission impact); it is NOT a probability. 4) The live baseline was validated only at 8 m/s on one aircraft and one route shape. "
         "5) The reference velocity in the live pipeline is the GPS-aided PX4 estimator velocity, so live detection is easier than it would be against real spoofing. 6) Injection is at the monitor level: PX4 and Gazebo never see the attack."),
        ("Model card", model_card.strip().splitlines()[0] if model_card.strip() else ""),
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out-dir", type=Path, default=BACKEND.parent / "exports")
    parser.add_argument("--preview-rows", type=int, default=10_000, help="rows in the Windows sheet of the preview workbook")
    parser.add_argument("--only", choices=("full", "preview", "both"), default="both", help="which workbook to write")
    parser.add_argument("--no-preview", action="store_true")
    parser.add_argument("--max-window-rows", type=int, default=EXCEL_MAX_ROWS - 1, help="rows allowed in the Windows sheet (Excel limit minus the header)")
    args = parser.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    today = dt.date.today().isoformat()
    target = args.out_dir / f"trinetra_dataset_{today}.xlsx"

    sheet, full, info = build_windows(args.max_window_rows)
    expected = info["expected_features"]
    if info["sampled"]:
        full.to_csv(args.out_dir / "windows_full.csv", index=False)
    features_only = pd.read_csv(DATASET / "features.csv", usecols=["flight_id", "speed"])
    flights = flights_sheet(info["flights"].drop(columns=[c for c in ("attack_target",) if c in info["flights"].columns]), features_only)
    attacks = attacks_sheet()
    all_metrics, tiers = metrics_sheets()
    runs, decisions, results, experiments = live_sheets()
    documented = documented_live_baseline()
    model_card = (ARTIFACTS / "model_card.md").read_text(encoding="utf-8")

    dictionary = []
    for name in expected:
        unit, meaning, how = FEATURE_HELP.get(name, ("", "(no description in the code)", ""))
        dictionary.append({"column": name, "kind": "MODEL INPUT (feature)", "unit": unit, "meaning": meaning, "how_computed": how})
    for name, meaning in META_HELP.items():
        kind = "identifier" if name in ("flight_id", "aircraft_id", "t") else "METADATA / ground truth (never a feature)"
        sheet_name = name if name in ("flight_id", "aircraft_id", "t") else f"META_{name}"
        dictionary.append({"column": sheet_name, "kind": kind, "unit": "", "meaning": meaning, "how_computed": "copied from features.csv"})
    for name, meaning in (("LABEL_is_attacked", "yes when this sample lies inside an injected attack window (from labels_HIDDEN.csv)."),
                          ("LABEL_attack_type", "Attack type of that window (offset, jump, drift, freeze, noise, velocity_inconsistency) or empty."),
                          ("LABEL_cell", "Scenario cell the flight was generated for (e.g. drift:low, hard:small_drift, normal)."),
                          ("LABEL_attack_start_s", "Flight-level attack start (s since flight start)."), ("LABEL_attack_duration_s", "Flight-level attack duration (s)."),
                          ("LABEL_attack_magnitude", "Flight-level attack magnitude (unit depends on the type, see Attacks)."), ("LABEL_attack_direction_deg", "Attack direction (empty = random by seed)."),
                          ("META_split", "train, val or test; split by flight.")):
        dictionary.append({"column": name, "kind": "LABEL / split (ground truth, never a feature)" if name.startswith("LABEL") else "split metadata", "unit": "", "meaning": meaning, "how_computed": "joined by flight from flights.csv / labels_HIDDEN.csv"})
    dictionary = pd.DataFrame(dictionary)

    import xlsxwriter

    def write_book(target: Path, windows_frame: pd.DataFrame, readme: pd.DataFrame) -> None:
        book = xlsxwriter.Workbook(str(target), {"constant_memory": True, "nan_inf_to_errors": True, "tmpdir": str(args.out_dir)})   # temp files next to the output, not on the system drive
        head = book.add_format({"bold": True, "bg_color": "#D9E2F3", "border": 1, "text_wrap": True, "valign": "top"})
        red = book.add_format({"bold": True, "bg_color": "#F8CBAD", "border": 1, "text_wrap": True})
        grey = book.add_format({"bold": True, "bg_color": "#D9D9D9", "border": 1, "text_wrap": True})
        wrap = book.add_format({"text_wrap": True, "valign": "top"})

        def cell(value: Any) -> Any:
            if value is None or (isinstance(value, float) and value != value):
                return ""
            if isinstance(value, (np.integer,)):
                return int(value)
            if isinstance(value, (np.floating,)):
                return float(value)
            return value if isinstance(value, (int, float, str, bool)) else str(value)

        def write_frame(ws, frame: pd.DataFrame, start: int = 0, widths: dict[str, int] | None = None, colour=None, wrap_col: str | None = None) -> int:
            """Header at `start`, then the rows, in row order (required by constant_memory). Returns the next free row."""
            for index, column in enumerate(frame.columns):
                ws.write(start, index, str(column), (colour(str(column)) if colour else head) or head)
                width = (widths or {}).get(column) or min(40, max(10, len(str(column)) + 2))
                ws.set_column(index, index, width, wrap if column == wrap_col else None)
            row = start + 1
            clean = frame.round(6).astype(object).where(frame.notna(), "")
            for chunk_start in range(0, len(clean), 20000):
                for values in clean.iloc[chunk_start:chunk_start + 20000].values.tolist():
                    ws.write_row(row, 0, values)
                    row += 1
            return row

        def simple(name: str, frame: pd.DataFrame, widths: dict[str, int] | None = None, wrap_col: str | None = None) -> None:
            ws = book.add_worksheet(name)
            end = write_frame(ws, frame, 0, widths, wrap_col=wrap_col)
            ws.freeze_panes(1, 0)
            ws.autofilter(0, 0, max(end - 1, 1), max(len(frame.columns) - 1, 0))

        simple("README", readme, {"topic": 22, "text": 150}, wrap_col="text")
        simple("Data dictionary", dictionary, {"column": 28, "kind": 38, "unit": 8, "meaning": 100, "how_computed": 50})
        windows = book.add_worksheet("Windows")
        end = write_frame(windows, windows_frame, 0, colour=lambda c: red if c.startswith("LABEL_") else grey if c.startswith("META_") else head)
        windows.freeze_panes(1, 3)
        windows.autofilter(0, 0, end - 1, len(windows_frame.columns) - 1)
        simple("Flights", flights)
        simple("Attacks", attacks, {"meaning": 90})
        simple("Model metrics", all_metrics, {"metric_path": 90, "value": 60})
        simple("Tier thresholds", tiers, {"item": 70, "value": 60})
        live = book.add_worksheet("Live baseline")
        live.write(0, 0, "A. Real flights recorded in backend/trinetra.db (mission executions). Threat assessments were NOT stored per sample.", head)
        row = write_frame(live, runs, 1)
        live.write(row + 2, 0, "B. Live baseline (no attack, PX4 SITL, 8 m/s) exactly as written in backend/docs/ML_PIPELINE.md", head)
        row = write_frame(live, documented, row + 3)
        live.write(row + 2, 0, "C. Operator responses recorded in the database (research_decisions)", head)
        write_frame(live, decisions, row + 3)
        live.set_column(0, 12, 26)
        records = book.add_worksheet("Run records")
        records.set_column(0, 0, 150, wrap)
        records.write(0, 0, f"{len(experiments)} saved experiment(s) and {len(results)} stored experiment result(s) exist in the database. Live run records with injected attack times, detection latency and "
                      "operator responses are not stored yet (the live runner does not exist), so there are no run records to list. Saved experiments (headers and rows):", wrap)
        write_frame(records, experiments, 2)
        book.close()
        size = target.stat().st_size
        digest = hashlib.sha256(target.read_bytes()).hexdigest()[:16]
        print(f"wrote {target} ({size / 1e6:.1f} MB, sha256 {digest}...)")

    if args.only in ("full", "both"):
        write_book(target, sheet, pd.DataFrame(readme_lines(info, model_card, today), columns=["topic", "text"]))
        print(f"windows rows in sheet: {info['sheet_rows']} of {info['total_rows']} (sampled: {info['sampled']}); model features: {len(expected)}")
    if args.only in ("preview", "both") and not args.no_preview:
        chosen, strata = stratified_preview(full, args.preview_rows)
        preview_info = {**info, "sampled": True, "sheet_rows": len(chosen)}
        preview_target = args.out_dir / f"trinetra_dataset_preview_{today}.xlsx"
        write_book(preview_target, chosen, pd.DataFrame(readme_lines(preview_info, model_card, today, {"strata": strata, "full_name": target.name}), columns=["topic", "text"]))
        print(f"preview windows rows: {len(chosen)} of {info['total_rows']}; strata (total, taken): {strata}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
