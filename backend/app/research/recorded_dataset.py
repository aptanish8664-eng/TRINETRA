"""Strict, offline importer for separately recorded GPS and IMU CSV streams.

The importer accepts normalized SI data; it never downloads data or infers labels.
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
from bisect import bisect_left
from pathlib import Path
from typing import Any, Iterable

GPS_FIELDS = ("session_id", "timestamp_s", "latitude_deg", "longitude_deg", "altitude_m")
IMU_FIELDS = ("session_id", "timestamp_s", "accel_x_m_s2", "accel_y_m_s2", "accel_z_m_s2",
              "gyro_x_rad_s", "gyro_y_rad_s", "gyro_z_rad_s")


def _read(path: Path, fields: Iterable[str]) -> list[dict[str, Any]]:
    with path.open("r", newline="", encoding="utf-8-sig") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames is None or not set(fields).issubset(reader.fieldnames):
            raise ValueError(f"{path.name} is missing required columns: {', '.join(fields)}")
        rows = []
        seen: set[tuple[str, float]] = set()
        last: dict[str, float] = {}
        for line, raw in enumerate(reader, start=2):
            session = (raw.get("session_id") or "").strip()
            if not session:
                raise ValueError(f"{path.name}:{line} has an empty session_id")
            try:
                timestamp = float(raw["timestamp_s"])
                values = {key: float(raw[key]) for key in fields if key not in ("session_id", "timestamp_s")}
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{path.name}:{line} contains a missing or non-numeric required value") from exc
            if not math.isfinite(timestamp) or not all(math.isfinite(v) for v in values.values()):
                raise ValueError(f"{path.name}:{line} contains a non-finite value")
            if "latitude_deg" in values and not -90 <= values["latitude_deg"] <= 90:
                raise ValueError(f"{path.name}:{line} latitude is outside [-90, 90]")
            if "longitude_deg" in values and not -180 <= values["longitude_deg"] <= 180:
                raise ValueError(f"{path.name}:{line} longitude is outside [-180, 180]")
            key = (session, timestamp)
            if key in seen:
                raise ValueError(f"{path.name}:{line} duplicates a session timestamp")
            if session in last and timestamp <= last[session]:
                raise ValueError(f"{path.name}:{line} timestamps must increase within each session")
            seen.add(key)
            last[session] = timestamp
            rows.append({"session_id": session, "timestamp_s": timestamp, **values})
    if not rows:
        raise ValueError(f"{path.name} contains no observations")
    return rows


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _interpolate(earlier: dict[str, Any], later: dict[str, Any], timestamp: float) -> dict[str, float]:
    span = later["timestamp_s"] - earlier["timestamp_s"]
    ratio = (timestamp - earlier["timestamp_s"]) / span
    ignored = {"session_id", "timestamp_s"}
    return {key: earlier[key] + ratio * (later[key] - earlier[key])
            for key in earlier if key not in ignored}


def load_recorded_sensor_csv(
    gps_csv: str | Path,
    imu_csv: str | Path,
    *,
    source_name: str,
    source_url: str,
    license_name: str,
    max_imu_bracket_s: float = 0.25,
    labels_csv: str | Path | None = None,
) -> dict[str, Any]:
    """Load normalized GPS/IMU files and align IMU to GPS epochs offline.

    Interpolation requires a bracketing IMU sample on both sides and never
    extrapolates. Labels, if supplied, are retained as metadata only.
    """
    if not source_name.strip() or not source_url.strip() or not license_name.strip():
        raise ValueError("source_name, source_url, and license_name provenance are required")
    if not math.isfinite(max_imu_bracket_s) or max_imu_bracket_s <= 0:
        raise ValueError("max_imu_bracket_s must be positive and finite")
    gps_path, imu_path = Path(gps_csv), Path(imu_csv)
    gps = _read(gps_path, GPS_FIELDS)
    imu = _read(imu_path, IMU_FIELDS)
    imu_sessions: dict[str, list[dict[str, Any]]] = {}
    imu_times: dict[str, list[float]] = {}
    for row in imu:
        imu_sessions.setdefault(row["session_id"], []).append(row)
        imu_times.setdefault(row["session_id"], []).append(row["timestamp_s"])
    aligned = []
    dropped_unbracketed = 0
    for fix in gps:
        stream = imu_sessions.get(fix["session_id"], [])
        times = imu_times.get(fix["session_id"], [])
        right = bisect_left(times, fix["timestamp_s"])
        if right >= len(stream):
            dropped_unbracketed += 1
            continue
        after = stream[right]
        if times[right] == fix["timestamp_s"]:
            before = after
        elif right == 0:
            dropped_unbracketed += 1
            continue
        else:
            before = stream[right - 1]
        if max(fix["timestamp_s"] - before["timestamp_s"], after["timestamp_s"] - fix["timestamp_s"]) > max_imu_bracket_s:
            dropped_unbracketed += 1
            continue
        imu_values = before if before is after else _interpolate(before, after, fix["timestamp_s"])
        aligned.append({"session_id": fix["session_id"], "timestamp_s": fix["timestamp_s"],
                        "gps": {k: v for k, v in fix.items() if k not in ("session_id", "timestamp_s")},
                        "imu": imu_values})
    if not aligned:
        raise ValueError("no GPS epochs have an adequately bracketed IMU sample")
    label_path = Path(labels_csv) if labels_csv else None
    label_rows = _read_labels(label_path) if label_path else []
    gps_epochs = {(row["session_id"], row["timestamp_s"]) for row in gps}
    if any((row["session_id"], row["timestamp_s"]) not in gps_epochs for row in label_rows):
        raise ValueError("each label must reference an existing GPS session timestamp")
    sessions = sorted({row["session_id"] for row in aligned})
    manifest = {
        "schema_version": 1, "origin": "recorded_external_dataset", "source_name": source_name,
        "source_url": source_url, "license": license_name, "units": "SI; geodetic GPS degrees/metres",
        "coordinate_frame": "WGS84 geodetic; IMU body frame as supplied by source",
        "gps_sha256": _sha256(gps_path), "imu_sha256": _sha256(imu_path),
        "labels_sha256": _sha256(label_path) if label_path else None,
        "sessions": sessions, "gps_rows": len(gps), "imu_rows": len(imu),
        "aligned_rows": len(aligned), "dropped_unbracketed_gps_rows": dropped_unbracketed,
        "label_rows": len(label_rows),
        "split_policy": "session_group_required; caller must keep each session in one split",
        "label_leakage_policy": "labels are separate metadata and excluded from gps/imu feature payloads",
    }
    manifest["dataset_id"] = hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()
    return {"manifest": manifest, "samples": aligned, "labels": label_rows}


def split_sessions(session_ids: Iterable[str], *, seed: int = 0) -> dict[str, list[str]]:
    """Assign complete sessions to disjoint train/validation/test groups."""
    import random
    sessions = sorted(set(session_ids))
    if len(sessions) < 3:
        raise ValueError("at least three distinct sessions are required for a grouped holdout")
    if seed < 0:
        raise ValueError("seed must be non-negative")
    random.Random(seed).shuffle(sessions)
    test_count = max(1, round(len(sessions) * 0.2))
    validation_count = max(1, round(len(sessions) * 0.2))
    if test_count + validation_count >= len(sessions):
        test_count, validation_count = 1, 1
    result = {"test": sorted(sessions[:test_count]),
              "validation": sorted(sessions[test_count:test_count + validation_count]),
              "train": sorted(sessions[test_count + validation_count:])}
    if set(result["train"]) & (set(result["test"]) | set(result["validation"])) or set(result["test"]) & set(result["validation"]):
        raise AssertionError("session split leaked across groups")
    return result


def _read_labels(path: Path) -> list[dict[str, Any]]:
    with path.open("r", newline="", encoding="utf-8-sig") as stream:
        reader = csv.DictReader(stream)
        required = {"session_id", "timestamp_s", "label"}
        if reader.fieldnames is None or not required.issubset(reader.fieldnames):
            raise ValueError("labels CSV requires session_id,timestamp_s,label columns")
        rows = []
        seen = set()
        for raw in reader:
            session, timestamp, label = (raw.get("session_id") or "").strip(), float(raw["timestamp_s"]), (raw.get("label") or "").strip()
            if not session or not label or not math.isfinite(timestamp) or (session, timestamp) in seen:
                raise ValueError("labels contain an empty, invalid, or duplicate session timestamp")
            seen.add((session, timestamp))
            rows.append({"session_id": session, "timestamp_s": timestamp, "label": label})
    return rows
