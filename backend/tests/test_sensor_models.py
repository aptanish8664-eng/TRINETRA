import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.research.sensor_dataset import generate_sensor_dataset
from app.research.sensor_models import evaluate_sensor_dataset


class SensorModelTests(unittest.TestCase):
    def test_model_comparison_uses_validation_thresholds_and_writes_research_artifacts(self):
        bundle = generate_sensor_dataset(range(5), duration_s=8, sample_rate_hz=2)
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "evaluation"
            result = evaluate_sensor_dataset(bundle, output)
            self.assertEqual(
                set(result["results"]),
                {"threshold_based", "isolation_forest", "random_forest", "gradient_boosting"},
            )
            for model in result["results"].values():
                self.assertIn("validation_metrics", model)
                self.assertLessEqual(model["validation_metrics"]["false_positive_rate"], 0.05)
                test = model["test_metrics"]
                self.assertIn("precision", test)
                self.assertIn("recall", test)
                self.assertIn("f1", test)
                self.assertIn("false_positive_rate", test)
                self.assertIn("roc_auc", test)
                self.assertIn("detection_latency", test)
                self.assertEqual(len(test["confusion_matrix_labels_0_1"]), 2)
            self.assertTrue((output / "isolation_forest.joblib").is_file())
            self.assertTrue((output / "random_forest.joblib").is_file())
            self.assertTrue((output / "gradient_boosting.joblib").is_file())
            self.assertTrue((output / "correlation-matrix.csv").is_file())
            self.assertTrue((output / "threat-assessments.csv").is_file())
            self.assertIn("<svg", (output / "correlation-matrix.svg").read_text(encoding="utf-8"))
            self.assertIn("<svg", (output / "sensor-timeseries.svg").read_text(encoding="utf-8"))
            report = (output / "critical-analysis.md").read_text(encoding="utf-8")
            self.assertIn("not flight evidence", report)
            saved = json.loads((output / "model-comparison.json").read_text(encoding="utf-8"))
            self.assertEqual(saved["dataset_id"], bundle["manifest"]["dataset_id"])
            self.assertIn("ekf_position_rmse_3d_m", saved["state_estimation_metrics"])
            self.assertIn("normal", saved["state_estimation_metrics"]["per_scenario_type"])
            self.assertIn("gps_outage", saved["state_estimation_metrics"]["per_scenario_type"])
            self.assertIn("gps_jump", saved["results"]["random_forest"]["per_scenario_test_metrics"])
            self.assertTrue(output.is_dir())

    def test_evaluation_refuses_existing_output_directory(self):
        bundle = generate_sensor_dataset(range(5), duration_s=4, sample_rate_hz=1)
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "existing"
            output.mkdir()
            with self.assertRaises(FileExistsError):
                evaluate_sensor_dataset(bundle, output)


if __name__ == "__main__":
    unittest.main()
