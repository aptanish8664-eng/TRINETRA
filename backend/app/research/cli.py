"""Command-line entry point for explicitly exporting an abstract dataset."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import List

from app.research.dataset import generate_dataset, write_dataset


def _parse_seeds(value: str) -> List[int]:
    try:
        seeds = [int(item.strip()) for item in value.split(",") if item.strip()]
    except ValueError as exc:
        raise argparse.ArgumentTypeError("seeds must be comma-separated integers") from exc
    if len(seeds) < 5 or len(set(seeds)) != len(seeds):
        raise argparse.ArgumentTypeError("provide at least five distinct seeds")
    return seeds


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate a seed-split abstract GPS dataset from TRINETRA's deterministic simulator.")
    parser.add_argument("--seeds", type=_parse_seeds, required=True, help="at least five distinct comma-separated integer seeds")
    parser.add_argument("--output-root", type=Path, required=True, help="new or existing parent directory inside D:\\TRINETRA; dataset subdirectory must not exist")
    parser.add_argument("--duration", type=int, default=90, help="simulation ticks (20-3600; default 90)")
    parser.add_argument("--uav-count", type=int, default=3, help="abstract simulator UAV count (default 3)")
    parser.add_argument("--task-count", type=int, default=40, help="tasks to keep telemetry active across attack windows (default 40)")
    args = parser.parse_args()

    project_root = Path(__file__).resolve().parents[3]
    output_root = Path(os.path.abspath(str(args.output_root)))
    if not output_root.is_relative_to(project_root):
        parser.error("output-root must resolve inside D:\\TRINETRA")
    bundle = generate_dataset(args.seeds, duration=args.duration, uav_count=args.uav_count, task_count=args.task_count)
    target = output_root / bundle["manifest"]["dataset_id"]
    csv_path, manifest_path = write_dataset(bundle, target)
    print("dataset_id=" + bundle["manifest"]["dataset_id"])
    print("samples=" + str(bundle["manifest"]["row_count"]))
    print("csv=" + str(csv_path))
    print("manifest=" + str(manifest_path))


if __name__ == "__main__":
    main()
