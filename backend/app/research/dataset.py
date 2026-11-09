"""Versioned, seed-grouped datasets from the deterministic abstract simulator.

Features are derived only from reported GPS and the existing trajectory
residual detector. Attack labels come from the simulator's software-only fault
schedule. These samples are not PX4 or field telemetry.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence

from app.simulation.detector import DETECTOR_VERSION
from app.simulation.engine import SIMULATION_VERSION, Simulation, SimulationConfig
from app.research.features import FEATURE_COLUMNS, FEATURE_COLUMNS_V1, GpsFeatureExtractor


DATASET_GENERATOR_VERSION = "abstract-gps-dataset-v2"
DATASET_SCHEMA_VERSION = 2
BASE_COLUMNS = ("seed", "split", "scenario_type", "tick", "uav_id", "attack_active")
FEATURE_COLUMNS_BY_SCHEMA = {1: FEATURE_COLUMNS_V1, 2: FEATURE_COLUMNS}
FEATURE_SOURCE_BY_SCHEMA = {
    1: "reported_gps_and_trajectory_residual_only",
    2: "reported_gps_velocity_and_trajectory_residual_only",
}
SCENARIO_TYPES_V1 = ("none", "sudden", "gradual", "intermittent")
SCENARIO_TYPES = (*SCENARIO_TYPES_V1, "jump", "freeze", "noise", "degradation", "velocity_inconsistency")
CSV_COLUMNS = (*BASE_COLUMNS, *FEATURE_COLUMNS)
IDENTITY_MANIFEST_KEYS = (
    "dataset_schema_version", "dataset_generator_version", "simulation_version",
    "detector_version", "simulation_backend", "coordinate_system", "label_definition",
    "feature_source", "features", "scenario_types", "seeds", "split_strategy",
    "split_seeds", "duration_ticks", "uav_count", "task_count", "row_count",
    "python_version", "platform",
)


def _content_hash(manifest: Mapping[str, Any], rows: List[Dict[str, Any]]) -> str:
    identity = {key: manifest[key] for key in IDENTITY_MANIFEST_KEYS}
    canonical_content = json.dumps(
        {"manifest": identity, "samples": rows},
        sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(canonical_content).hexdigest()


def _seed_splits(seeds: Sequence[int]) -> Dict[str, List[int]]:
    unique = list(dict.fromkeys(seeds))
    if len(unique) != len(seeds):
        raise ValueError("seeds must be unique so seed groups cannot leak across splits")
    if len(unique) < 5:
        raise ValueError("at least five distinct seeds are required for train/validation/test splits")
    ranked = sorted(unique, key=lambda seed: hashlib.sha256(
        ("trinetra-abstract-split-v1:" + str(seed)).encode("ascii")
    ).digest())
    train_end = max(3, int(len(ranked) * 0.6))
    validation_size = max(1, int(len(ranked) * 0.2))
    if train_end + validation_size >= len(ranked):
        train_end, validation_size = len(ranked) - 2, 1
    return {
        "train": ranked[:train_end],
        "validation": ranked[train_end:train_end + validation_size],
        "test": ranked[train_end + validation_size:],
    }


def validate_dataset(bundle: Mapping[str, Any]) -> None:
    """Raise ValueError if data shape, labels, splits, or sample keys are invalid."""
    manifest = bundle.get("manifest")
    rows = bundle.get("samples")
    if not isinstance(manifest, dict) or not isinstance(rows, list) or not rows:
        raise ValueError("dataset must include a manifest and non-empty samples")
    schema_version = manifest.get("dataset_schema_version")
    if schema_version not in FEATURE_COLUMNS_BY_SCHEMA:
        raise ValueError("unsupported dataset schema version")
    expected_features = FEATURE_COLUMNS_BY_SCHEMA[schema_version]
    if manifest.get("features") != list(expected_features):
        raise ValueError("dataset feature schema does not match this generator")
    if manifest.get("feature_source") != FEATURE_SOURCE_BY_SCHEMA[schema_version]:
        raise ValueError("dataset feature source is invalid")
    supported_scenarios = set(SCENARIO_TYPES_V1 if schema_version == 1 else SCENARIO_TYPES)
    scenario_profiles = manifest.get("scenario_types")
    if (not isinstance(scenario_profiles, list) or not scenario_profiles
            or len(set(scenario_profiles)) != len(scenario_profiles)
            or not set(scenario_profiles).issubset(supported_scenarios)):
        raise ValueError("dataset scenario profile list is invalid")
    allowed_scenarios = set(scenario_profiles)
    split_seeds = manifest.get("split_seeds")
    if not isinstance(split_seeds, dict) or set(split_seeds) != {"train", "validation", "test"}:
        raise ValueError("dataset must define train, validation, and test seed groups")
    seed_sets = {name: set(values) for name, values in split_seeds.items()}
    if any(seed_sets[a] & seed_sets[b] for a, b in (("train", "validation"), ("train", "test"), ("validation", "test"))):
        raise ValueError("seed groups overlap across dataset splits")
    expected_seeds = set(manifest.get("seeds", []))
    if set.union(*seed_sets.values()) != expected_seeds:
        raise ValueError("split seed groups do not cover the declared dataset seeds")

    seen = set()
    split_labels: Dict[str, set] = {name: set() for name in split_seeds}
    split_counts: Dict[str, Dict[str, int]] = {name: {"clean": 0, "active_fault": 0} for name in split_seeds}
    for row in rows:
        if not isinstance(row, dict) or set(row) != set((*BASE_COLUMNS, *expected_features)):
            raise ValueError("dataset row columns do not match the declared schema")
        if row["split"] not in split_seeds or row["seed"] not in seed_sets[row["split"]]:
            raise ValueError("dataset row is assigned to the wrong seed split")
        if row["attack_active"] not in (0, 1):
            raise ValueError("attack_active must be a binary label")
        if row["scenario_type"] not in allowed_scenarios or row["tick"] < 1:
            raise ValueError("dataset scenario type or tick is invalid")
        key = (row["seed"], row["scenario_type"], row["tick"], row["uav_id"])
        if key in seen:
            raise ValueError("duplicate sample key")
        seen.add(key)
        split_labels[row["split"]].add(row["attack_active"])
        split_counts[row["split"]]["active_fault" if row["attack_active"] else "clean"] += 1
        for column in expected_features:
            value = row[column]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError("feature values must be finite numbers")
    if any(labels != {0, 1} for labels in split_labels.values()):
        raise ValueError("each split must contain both clean and active-fault samples")
    if manifest.get("row_count") != len(rows) or manifest.get("label_counts") != split_counts:
        raise ValueError("manifest row or label counts do not match dataset contents")
    content_hash = _content_hash(manifest, rows)
    if manifest.get("dataset_id") != content_hash or manifest.get("content_sha256") != content_hash:
        raise ValueError("dataset content hash does not match the manifest and samples")


def generate_dataset(
    seeds: Sequence[int],
    *,
    duration: int = 90,
    uav_count: int = 3,
    task_count: int = 40,
    scenario_types: Sequence[str] = SCENARIO_TYPES,
) -> Dict[str, Any]:
    """Generate deterministic tick-level samples and seed-disjoint splits."""
    if duration < 20 or duration > 3600:
        raise ValueError("duration must be between 20 and 3600 ticks to include clean and fault windows")
    if not 2 <= uav_count <= 12:
        raise ValueError("uav_count must be between 2 and 12")
    if not 1 <= task_count <= 40:
        raise ValueError("task_count must be between 1 and 40")
    if any(isinstance(seed, bool) or not isinstance(seed, int) for seed in seeds):
        raise ValueError("seeds must be integers")
    supported_scenarios = set(SCENARIO_TYPES)
    if (not scenario_types or len(set(scenario_types)) != len(scenario_types)
            or not set(scenario_types).issubset(supported_scenarios)):
        raise ValueError("scenario_types must be a non-empty list of unique supported fault profiles")
    splits = _seed_splits(seeds)
    split_by_seed = {seed: split for split, group in splits.items() for seed in group}
    ordered_seeds = sorted(split_by_seed)
    rows: List[Dict[str, Any]] = []

    for seed in ordered_seeds:
        for scenario_type in scenario_types:
            config = SimulationConfig(
                uav_count=uav_count,
                task_count=task_count,
                seed=seed,
                duration=duration,
                spoofing_type=scenario_type,
                target_uav="UAV-02",
                spoof_start=15,
                spoof_duration=min(35, duration - 15),
                spoof_magnitude=3.0,
                response_policy="monitor",
            )
            simulation = Simulation(config)
            feature_extractor = GpsFeatureExtractor()
            previous_tick = 0
            while simulation.tick_count < duration and simulation.status not in {"completed", "stopped"}:
                state = simulation.step()
                if state["tick"] <= previous_tick:
                    break
                previous_tick = state["tick"]
                threshold = float(state["calibration"]["threshold"])
                for uid, uav in state["uavs"].items():
                    gps = uav["gps"]
                    current_gps = (float(gps["x"]), float(gps["y"]))
                    residual = float(uav["residual"])
                    gps_velocity = uav["gps_velocity"]
                    features = feature_extractor.update(
                        uid, current_gps, residual, threshold, config.dt,
                        (float(gps_velocity["x"]), float(gps_velocity["y"])),
                    )
                    active = simulation.fault_active_at_current_tick(uid)
                    rows.append({
                        "seed": seed,
                        "split": split_by_seed[seed],
                        "scenario_type": scenario_type,
                        "tick": int(state["tick"]),
                        "uav_id": uid,
                        "attack_active": int(active),
                        **{name: round(features[name], 8) for name in FEATURE_COLUMNS},
                    })

    core_manifest = {
        "dataset_schema_version": DATASET_SCHEMA_VERSION,
        "dataset_generator_version": DATASET_GENERATOR_VERSION,
        "simulation_version": SIMULATION_VERSION,
        "detector_version": DETECTOR_VERSION,
        "simulation_backend": "deterministic_abstract_python",
        "coordinate_system": "abstract_simulation_units",
        "label_definition": "software_fault offset is active for this UAV at this tick",
        "feature_source": FEATURE_SOURCE_BY_SCHEMA[DATASET_SCHEMA_VERSION],
        "features": list(FEATURE_COLUMNS),
        "scenario_types": list(scenario_types),
        "seeds": ordered_seeds,
        "split_strategy": "seed_group_sha256_60_20_20_v1",
        "split_seeds": splits,
        "duration_ticks": duration,
        "uav_count": uav_count,
        "task_count": task_count,
        "row_count": len(rows),
        "python_version": sys.version.split()[0],
        "platform": platform.platform(),
    }
    label_counts: Dict[str, Dict[str, int]] = {name: {"clean": 0, "active_fault": 0} for name in splits}
    for row in rows:
        label_counts[row["split"]]["active_fault" if row["attack_active"] else "clean"] += 1
    identity = {key: core_manifest[key] for key in IDENTITY_MANIFEST_KEYS}
    canonical_content = json.dumps(
        {"manifest": identity, "samples": rows},
        sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")
    content_hash = hashlib.sha256(canonical_content).hexdigest()
    manifest = {
        **core_manifest,
        "dataset_id": content_hash,
        "content_sha256": content_hash,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "attack_active_counts": {
            name: sum(row["attack_active"] for row in rows if row["split"] == name)
            for name in splits
        },
        "label_counts": label_counts,
    }
    bundle = {"manifest": manifest, "samples": rows}
    validate_dataset(bundle)
    return bundle


def write_dataset(bundle: Mapping[str, Any], output_dir: Path) -> Tuple[Path, Path]:
    """Write a validated bundle to a new directory; never overwrite files."""
    validate_dataset(bundle)
    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=False)
    csv_path = target / "samples.csv"
    manifest_path = target / "manifest.json"
    with csv_path.open("x", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS, extrasaction="raise")
        writer.writeheader()
        writer.writerows(bundle["samples"])
    with manifest_path.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(bundle["manifest"], handle, sort_keys=True, indent=2, allow_nan=False)
        handle.write("\n")
    return csv_path, manifest_path


def load_dataset(dataset_dir: Path) -> Dict[str, Any]:
    """Load and fully validate an exported dataset before downstream use."""
    source = Path(dataset_dir)
    manifest_path = source / "manifest.json"
    csv_path = source / "samples.csv"
    with manifest_path.open("r", encoding="utf-8") as handle:
        manifest = json.load(handle)
    rows: List[Dict[str, Any]] = []
    schema_version = manifest.get("dataset_schema_version")
    expected_features = FEATURE_COLUMNS_BY_SCHEMA.get(schema_version)
    if expected_features is None:
        raise ValueError("unsupported dataset schema version")
    csv_columns = (*BASE_COLUMNS, *expected_features)
    with csv_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != list(csv_columns):
            raise ValueError("dataset CSV header does not match the declared schema")
        for raw in reader:
            row: Dict[str, Any] = {
                "seed": int(raw["seed"]),
                "split": raw["split"],
                "scenario_type": raw["scenario_type"],
                "tick": int(raw["tick"]),
                "uav_id": raw["uav_id"],
                "attack_active": int(raw["attack_active"]),
            }
            for column in expected_features:
                row[column] = float(raw[column])
            rows.append(row)
    bundle = {"manifest": manifest, "samples": rows}
    validate_dataset(bundle)
    if source.name != manifest["dataset_id"]:
        raise ValueError("dataset directory name does not match its content ID")
    return bundle
