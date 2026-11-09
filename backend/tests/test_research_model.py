import copy
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.research.dataset import generate_dataset
from app.research.model import load_model, predict, severity_level, train_model, validate_model, write_model


class ResearchModelTests(unittest.TestCase):
    def make_dataset(self):
        return generate_dataset([11, 22, 33, 44, 55, 66, 77, 88, 99, 111], duration=50, uav_count=3, task_count=40)

    def test_train_uses_seed_disjoint_sets_and_deterministic_model(self):
        dataset = self.make_dataset()
        first = train_model(dataset)
        second = train_model(dataset)
        self.assertEqual(first["model_id"], second["model_id"])
        self.assertEqual(set(first["training_seeds"]) & set(first["validation_seeds"]), set())
        self.assertEqual(set(first["training_seeds"]) & set(first["test_seeds"]), set())
        self.assertEqual(set(first["validation_seeds"]) & set(first["test_seeds"]), set())
        self.assertEqual(first["test_metrics"]["sample_count"], sum(row["split"] == "test" for row in dataset["samples"]))
        self.assertEqual(len(first["test_metrics"]["confusion_matrix_labels_0_1"]), 2)

    def test_predict_returns_probability_confidence_and_severity_band(self):
        dataset = self.make_dataset()
        model = train_model(dataset)
        result = predict(model, {name: dataset["samples"][0][name] for name in model["feature_columns"]})
        self.assertGreaterEqual(result["attack_probability"], 0)
        self.assertLessEqual(result["attack_probability"], 1)
        self.assertGreaterEqual(result["confidence"], 0.5)
        self.assertFalse(result["confidence_is_calibrated"])
        self.assertIn(result["threat_level"], {"low", "moderate", "high", "critical"})
        self.assertGreaterEqual(result["threat_severity"], 0)
        self.assertLessEqual(result["threat_severity"], 100)

    def test_json_model_roundtrip_and_tamper_rejection(self):
        model = train_model(self.make_dataset())
        with tempfile.TemporaryDirectory() as temp:
            path = write_model(model, Path(temp) / model["model_id"] / "artifact")
            loaded = load_model(path)
            self.assertEqual(loaded["model_id"], model["model_id"])
            altered = copy.deepcopy(loaded)
            altered["decision_threshold"] = 0.1 if loaded["decision_threshold"] != 0.1 else 0.2
            with self.assertRaisesRegex(ValueError, "hash"):
                validate_model(altered)

    def test_predict_rejects_incomplete_or_nonfinite_features(self):
        model = train_model(self.make_dataset())
        with self.assertRaisesRegex(ValueError, "model schema"):
            predict(model, {"trajectory_residual": 1.0})
        dataset = self.make_dataset()
        invalid = {name: dataset["samples"][0][name] for name in model["feature_columns"]}
        invalid[model["feature_columns"][0]] = float("nan")
        with self.assertRaisesRegex(ValueError, "finite"):
            predict(model, invalid)

    def test_threat_severity_bands_match_documented_cutoffs(self):
        expected = {
            0: "low", 25: "low", 26: "moderate", 50: "moderate",
            51: "high", 75: "high", 76: "critical", 100: "critical",
        }
        for value, level in expected.items():
            self.assertEqual(severity_level(value), level)
        with self.assertRaises(ValueError):
            severity_level(101)


if __name__ == "__main__":
    unittest.main()
