"""Generate and evaluate reproducible software-only sensor research artifacts."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from app.research.sensor_dataset import generate_sensor_dataset, write_sensor_dataset
from app.research.sensor_models import evaluate_sensor_dataset


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate and evaluate deterministic synthetic ENU sensor-fusion data.",
    )
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--seeds", type=int, nargs="+", default=list(range(100, 110)))
    parser.add_argument("--duration-s", type=float, default=20.0)
    parser.add_argument("--sample-rate-hz", type=float, default=10.0)
    args = parser.parse_args()

    project_root = Path(__file__).resolve().parents[3]
    output_root = Path(os.path.abspath(str(args.output_root)))
    if not output_root.is_relative_to(project_root):
        parser.error("output-root must resolve inside D:\\TRINETRA")
    bundle = generate_sensor_dataset(
        args.seeds,
        duration_s=args.duration_s,
        sample_rate_hz=args.sample_rate_hz,
    )
    dataset_dir = output_root / "datasets" / bundle["manifest"]["dataset_id"]
    evaluation_dir = output_root / "evaluations" / bundle["manifest"]["dataset_id"]
    samples_path, manifest_path = write_sensor_dataset(bundle, dataset_dir)
    evaluation = evaluate_sensor_dataset(bundle, evaluation_dir)
    print("dataset_id=" + str(bundle["manifest"]["dataset_id"]))
    print("sample_count=" + str(bundle["manifest"]["sample_count"]))
    print("split_seeds=" + json.dumps(bundle["manifest"]["split_seeds"], sort_keys=True))
    print("samples=" + str(samples_path))
    print("manifest=" + str(manifest_path))
    print("evaluation=" + str(evaluation_dir))
    print("test_metrics=" + json.dumps({
        name: result["test_metrics"]
        for name, result in evaluation["results"].items()
    }, sort_keys=True))


if __name__ == "__main__":
    main()
