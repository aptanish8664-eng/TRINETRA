"""Adds recorded LIVE clean flights (PX4 SITL, no attack) to a training dataset directory in the same three-file format.

Rows come from `TRINETRA_RECORD_LIVE_DIR/live_features.jsonl` (the feature row of the last sample of every live assessment, computed by the same
code the live monitor uses, with its real peers). A flight is one aircraft's run (`aircraft_id#flight_idx`). Every flight goes to exactly one split,
chosen by its batch (a batch is one speed and path type), never by row. Ground truth: live clean flights are all label 0; there is no simulator
truth column for them (`position_error_m` is left empty).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Mapping

import pandas as pd

from .threat_data import FEATURE_COLUMNS_ALL


def read_batches(path: Path) -> list[dict]:
    """batches.csv lines: `start,speed,type,count,size` then `END,time`. Returns one dict per finished batch, in order."""
    batches, current = [], None
    for line in path.read_text(encoding="utf-8").splitlines():
        parts = line.split(",")
        if parts[0] == "END":
            if current is not None:
                current["end"] = float(parts[1])
                batches.append(current)
                current = None
        else:
            current = {"start": float(parts[0]), "speed": float(parts[1]), "type": parts[2], "count": int(parts[3]), "size": float(parts[4])}
    return batches


def load_live_rows(jsonl: Path) -> pd.DataFrame:
    rows = []
    for line in jsonl.read_text(encoding="utf-8").splitlines():
        item = json.loads(line)
        ctx = item["ctx"]
        rows.append({**{k: v for k, v in ctx.items() if k not in ("rates_hz", "mission", "gap_restart")},
                     "gps_hz": (ctx.get("rates_hz") or {}).get("GPS_RAW_INT"), "vel_hz": (ctx.get("rates_hz") or {}).get("LOCAL_POSITION_NED"),
                     "gap": bool(ctx.get("gap_restart")), **item["features"]})
    frame = pd.DataFrame(rows)
    frame["fid"] = frame["aircraft_id"] + "#" + frame["flight_idx"].astype(str)
    return frame


def assign_batches(frame: pd.DataFrame, batches: list[dict]) -> pd.DataFrame:
    """Each aircraft's k-th flight belongs to the k-th batch (the recorder counts flights per aircraft in order)."""
    out = frame.copy()
    out["batch"] = out["flight_idx"].astype(int)
    meta = {i: batches[i] for i in range(len(batches))}
    out["speed_plan"] = out["batch"].map(lambda i: meta[i]["speed"] if i in meta else None)
    out["path_type"] = out["batch"].map(lambda i: meta[i]["type"] if i in meta else None)
    return out


def _to_grid(flight: pd.DataFrame, step: float = 0.5, hold_s: float = 6.0) -> pd.DataFrame:
    """Live assessments arrive about every 3 s on a busy machine; the training tables are on a 0.5 s grid. Each grid time takes the latest live
    assessment at or before it (zero-order hold, at most `hold_s` old), which is what an operator would see on screen; no value is interpolated."""
    grid = pd.DataFrame({"t": [round(float(x), 3) for x in __import__("numpy").arange(float(flight["t"].min()), float(flight["t"].max()), step)]})
    merged = pd.merge_asof(grid, flight.sort_values("t"), on="t", direction="backward", tolerance=hold_s)
    merged["t_source"] = merged["t"]
    return merged.dropna(subset=["aircraft_id"]).reset_index(drop=True)


def append_live_clean(dataset_dir: Path, frame: pd.DataFrame, split_of_batch: Mapping[int, str]) -> dict:
    """Appends the rows to features.csv, labels_HIDDEN.csv and flights.csv; returns what was added per split."""
    features = pd.read_csv(dataset_dir / "features.csv", nrows=0).columns
    added = {"train": 0, "val": 0, "test": 0}
    feature_rows, label_rows, flight_rows = [], [], []
    for fid, flight in frame.groupby("fid"):
        batch = int(flight["batch"].iloc[0])
        split = split_of_batch[batch]
        flight_id = "L" + fid.replace("px4-", "P").replace("#", "_") + f"_b{batch}"
        rows = _to_grid(flight.sort_values("t"))
        out = pd.DataFrame({"flight_id": flight_id, "aircraft_id": rows["aircraft_id"].to_numpy(), "t": rows["t"].to_numpy()})
        for column in FEATURE_COLUMNS_ALL:
            out[column] = rows[column].to_numpy()
        out["battery_pct"] = rows["battery_pct"].fillna(100.0).to_numpy()
        out["role"] = "primary" if rows["aircraft_id"].iloc[0] == "px4-1" else "decoy"
        out["remaining_fraction"] = 1.0 - (rows["t"] - rows["t"].min()).to_numpy() / max(float(rows["t"].max() - rows["t"].min()), 1.0)
        out["position_error_m"] = float("nan")
        feature_rows.append(out[[c for c in features if c in out.columns]])
        label_rows.append(pd.DataFrame({"flight_id": flight_id, "aircraft_id": rows["aircraft_id"].to_numpy(), "t": rows["t"].to_numpy(), "is_attacked": False, "attack_type": ""}))
        flight_rows.append({"flight_id": flight_id, "seed": -1, "split": split, "cell": "live_clean", "attack_type": "", "tier": "", "hard_case": False, "n_aircraft": 5,
                            "duration_s": round(float(rows["t"].max() - rows["t"].min()), 1), "attack": ""})
        added[split] += len(rows)
    pd.concat(feature_rows).to_csv(dataset_dir / "features.csv", mode="a", header=False, index=False)
    pd.concat(label_rows).to_csv(dataset_dir / "labels_HIDDEN.csv", mode="a", header=False, index=False)
    pd.DataFrame(flight_rows).to_csv(dataset_dir / "flights.csv", mode="a", header=False, index=False)
    return added
