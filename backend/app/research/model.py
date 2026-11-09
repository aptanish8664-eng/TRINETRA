"""Train and safely load a seed-disjoint abstract telemetry classifier."""

from __future__ import annotations

import hashlib
import json
import math
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Mapping, Sequence

from sklearn.metrics import accuracy_score, confusion_matrix, f1_score, precision_score, recall_score, roc_auc_score
from sklearn.tree import DecisionTreeClassifier
import sklearn

from app.research.features import FEATURE_COLUMNS, FEATURE_COLUMNS_V1


MODEL_TYPES = {1: "scikit-learn-decision-tree-json-v1", 2: "scikit-learn-decision-tree-json-v2"}
FEATURE_COLUMNS_BY_MODEL_SCHEMA = {1: FEATURE_COLUMNS_V1, 2: FEATURE_COLUMNS}
MODEL_PARAMETERS = {
    "criterion": "entropy",
    "max_depth": 6,
    "min_samples_leaf": 12,
    "class_weight": "balanced",
    "random_state": 0,
}
IDENTITY_KEYS = (
    "model_schema_version", "model_type", "dataset_id", "simulation_backend",
    "label_definition", "feature_columns", "training_seeds", "validation_seeds",
    "test_seeds", "training_library", "parameters", "decision_threshold",
    "validation_metrics", "test_metrics", "tree",
)


def _identity_hash(artifact: Mapping[str, Any]) -> str:
    identity = {key: artifact[key] for key in IDENTITY_KEYS}
    payload = json.dumps(identity, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _matrix(rows: Sequence[Mapping[str, Any]], feature_columns: Sequence[str]):
    return [[float(row[name]) for name in feature_columns] for row in rows]


def _labels(rows: Sequence[Mapping[str, Any]]):
    return [int(row["attack_active"]) for row in rows]


def _metrics(actual, probabilities, threshold: float) -> Dict[str, Any]:
    predicted = [int(probability >= threshold) for probability in probabilities]
    return {
        "sample_count": len(actual),
        "attack_count": int(sum(actual)),
        "accuracy": float(accuracy_score(actual, predicted)),
        "precision": float(precision_score(actual, predicted, zero_division=0)),
        "recall": float(recall_score(actual, predicted, zero_division=0)),
        "f1": float(f1_score(actual, predicted, zero_division=0)),
        "roc_auc": float(roc_auc_score(actual, probabilities)),
        "confusion_matrix_labels_0_1": confusion_matrix(actual, predicted, labels=[0, 1]).tolist(),
    }


def _select_threshold(actual, probabilities) -> float:
    candidates = sorted({0.0, 0.5, 1.0, *(float(value) for value in probabilities)})
    scored = []
    for threshold in candidates:
        predicted = [int(value >= threshold) for value in probabilities]
        scored.append((
            float(f1_score(actual, predicted, zero_division=0)),
            float(precision_score(actual, predicted, zero_division=0)),
            -abs(threshold - 0.5),
            threshold,
        ))
    return max(scored)[3]


def severity_level(severity: int) -> str:
    if not 0 <= severity <= 100:
        raise ValueError("threat severity must be between zero and one hundred")
    return "low" if severity <= 25 else "moderate" if severity <= 50 else "high" if severity <= 75 else "critical"


def train_model(bundle: Mapping[str, Any]) -> Dict[str, Any]:
    """Fit on train seeds, select threshold on validation, and score test once."""
    from app.research.dataset import validate_dataset

    validate_dataset(bundle)
    manifest = bundle["manifest"]
    feature_columns = list(manifest["features"])
    schema_version = next(
        (version for version, columns in FEATURE_COLUMNS_BY_MODEL_SCHEMA.items() if list(columns) == feature_columns),
        None,
    )
    if schema_version is None:
        raise ValueError("dataset feature schema is unsupported for model training")
    rows = bundle["samples"]
    split_rows = {split: [row for row in rows if row["split"] == split] for split in ("train", "validation", "test")}
    for split, split_samples in split_rows.items():
        if not split_samples or {row["attack_active"] for row in split_samples} != {0, 1}:
            raise ValueError("each dataset split must contain both classes before model training")

    estimator = DecisionTreeClassifier(**MODEL_PARAMETERS)
    estimator.fit(_matrix(split_rows["train"], feature_columns), _labels(split_rows["train"]))
    validation_probabilities = estimator.predict_proba(_matrix(split_rows["validation"], feature_columns))[:, 1].tolist()
    validation_labels = _labels(split_rows["validation"])
    threshold = _select_threshold(validation_labels, validation_probabilities)
    test_probabilities = estimator.predict_proba(_matrix(split_rows["test"], feature_columns))[:, 1].tolist()
    test_labels = _labels(split_rows["test"])

    tree = estimator.tree_
    values = tree.value[:, 0, :]
    probabilities = []
    for node_values in values:
        total = float(sum(node_values))
        probabilities.append(float(node_values[1] / total) if total else 0.0)
    artifact: Dict[str, Any] = {
        "model_schema_version": schema_version,
        "model_type": MODEL_TYPES[schema_version],
        "dataset_id": manifest["dataset_id"],
        "simulation_backend": manifest["simulation_backend"],
        "label_definition": manifest["label_definition"],
        "feature_columns": feature_columns,
        "training_seeds": manifest["split_seeds"]["train"],
        "validation_seeds": manifest["split_seeds"]["validation"],
        "test_seeds": manifest["split_seeds"]["test"],
        "training_library": {"name": "scikit-learn", "version": sklearn.__version__, "python": sys.version.split()[0], "platform": platform.platform()},
        "parameters": dict(MODEL_PARAMETERS),
        "decision_threshold": threshold,
        "validation_metrics": _metrics(validation_labels, validation_probabilities, threshold),
        "test_metrics": _metrics(test_labels, test_probabilities, threshold),
        "tree": {
            "children_left": tree.children_left.astype(int).tolist(),
            "children_right": tree.children_right.astype(int).tolist(),
            "feature": tree.feature.astype(int).tolist(),
            "threshold": tree.threshold.astype(float).tolist(),
            "attack_probability": probabilities,
        },
    }
    digest = _identity_hash(artifact)
    artifact["model_id"] = digest
    artifact["artifact_sha256"] = digest
    artifact["trained_at_utc"] = datetime.now(timezone.utc).isoformat()
    validate_model(artifact)
    return artifact


def validate_model(artifact: Mapping[str, Any]) -> None:
    if not isinstance(artifact, Mapping):
        raise ValueError("unsupported model artifact schema")
    schema_version = artifact.get("model_schema_version")
    if schema_version not in FEATURE_COLUMNS_BY_MODEL_SCHEMA:
        raise ValueError("unsupported model artifact schema")
    model_features = FEATURE_COLUMNS_BY_MODEL_SCHEMA[schema_version]
    if artifact.get("model_type") != MODEL_TYPES[schema_version] or artifact.get("feature_columns") != list(model_features):
        raise ValueError("model type or feature schema is unsupported")
    groups = [set(artifact.get(key, [])) for key in ("training_seeds", "validation_seeds", "test_seeds")]
    if any(groups[a] & groups[b] for a, b in ((0, 1), (0, 2), (1, 2))):
        raise ValueError("model seed groups overlap")
    if not all(groups):
        raise ValueError("model is missing a train/validation/test seed group")
    threshold = artifact.get("decision_threshold")
    if isinstance(threshold, bool) or not isinstance(threshold, (float, int)) or not 0 <= threshold <= 1:
        raise ValueError("model decision threshold must be between zero and one")
    tree = artifact.get("tree")
    if not isinstance(tree, dict):
        raise ValueError("model tree is missing")
    arrays = [tree.get(name) for name in ("children_left", "children_right", "feature", "threshold", "attack_probability")]
    if not arrays or any(not isinstance(values, list) for values in arrays) or len({len(values) for values in arrays}) != 1:
        raise ValueError("model tree arrays have inconsistent sizes")
    count = len(arrays[0])
    if count == 0:
        raise ValueError("model tree is empty")
    left, right, features, thresholds, attack_probabilities = arrays
    for index in range(count):
        if not 0 <= float(attack_probabilities[index]) <= 1 or not math.isfinite(float(attack_probabilities[index])):
            raise ValueError("model tree contains an invalid probability")
        leaf = left[index] == right[index]
        if not leaf:
            if not (0 <= int(left[index]) < count and 0 <= int(right[index]) < count):
                raise ValueError("model tree contains an invalid child index")
            if not 0 <= int(features[index]) < len(model_features):
                raise ValueError("model tree contains an invalid feature index")
            if not math.isfinite(float(thresholds[index])):
                raise ValueError("model tree contains an invalid split threshold")
    digest = _identity_hash(artifact)
    if artifact.get("model_id") != digest or artifact.get("artifact_sha256") != digest:
        raise ValueError("model artifact hash does not match its contents")


def predict(model: Mapping[str, Any], features: Mapping[str, float]) -> Dict[str, Any]:
    """Return model probability, calibrated decision, confidence and severity."""
    validate_model(model)
    model_features = model["feature_columns"]
    if not set(model_features).issubset(features) or not set(features).issubset(FEATURE_COLUMNS):
        raise ValueError("inference features do not match the model schema")
    values = [float(features[name]) for name in model_features]
    if any(not math.isfinite(value) for value in values):
        raise ValueError("inference features must be finite numbers")
    tree = model["tree"]
    node = 0
    for _ in range(len(tree["children_left"])):
        left = tree["children_left"][node]
        right = tree["children_right"][node]
        if left == right:
            probability = float(tree["attack_probability"][node])
            break
        feature_index = tree["feature"][node]
        node = left if values[feature_index] <= tree["threshold"][node] else right
    else:
        raise ValueError("model tree traversal did not reach a leaf")
    severity = max(0, min(100, int(round(probability * 100))))
    level = severity_level(severity)
    detected = probability >= float(model["decision_threshold"])
    return {
        "attack_probability": probability,
        "confidence": max(probability, 1.0 - probability),
        "confidence_is_calibrated": False,
        "detected": detected,
        "classification": "gps_anomaly" if detected else "nominal",
        "threat_severity": severity,
        "threat_level": level,
        "model_id": model["model_id"],
    }


def write_model(artifact: Mapping[str, Any], output_dir: Path) -> Path:
    validate_model(artifact)
    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=False)
    model_path = target / "model.json"
    with model_path.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(artifact, handle, sort_keys=True, indent=2, allow_nan=False)
        handle.write("\n")
    return model_path


def load_model(model_path: Path) -> Dict[str, Any]:
    with Path(model_path).open("r", encoding="utf-8") as handle:
        artifact = json.load(handle)
    validate_model(artifact)
    return artifact
