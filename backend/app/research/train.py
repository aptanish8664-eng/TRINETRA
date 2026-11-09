"""CLI for train/validation/test evaluation of the abstract GPS classifier."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from app.research.dataset import load_dataset
from app.research.model import train_model, write_model


def main() -> None:
    parser = argparse.ArgumentParser(description="Train an abstract-simulator GPS anomaly classifier from seed-disjoint data.")
    parser.add_argument("--dataset-dir", type=Path, required=True, help="validated dataset directory inside D:\\TRINETRA")
    parser.add_argument("--output-root", type=Path, required=True, help="new or existing model parent directory inside D:\\TRINETRA")
    args = parser.parse_args()
    project_root = Path(__file__).resolve().parents[3]
    dataset_dir = Path(os.path.abspath(str(args.dataset_dir)))
    output_root = Path(os.path.abspath(str(args.output_root)))
    if not dataset_dir.is_relative_to(project_root) or not output_root.is_relative_to(project_root):
        parser.error("dataset-dir and output-root must resolve inside D:\\TRINETRA")
    artifact = train_model(load_dataset(dataset_dir))
    model_path = write_model(artifact, output_root / artifact["model_id"])
    print("model_id=" + artifact["model_id"])
    print("dataset_id=" + artifact["dataset_id"])
    print("decision_threshold=" + str(artifact["decision_threshold"]))
    print("test_metrics=" + __import__("json").dumps(artifact["test_metrics"], sort_keys=True))
    print("model=" + str(model_path))


if __name__ == "__main__":
    main()
