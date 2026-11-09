import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.research.sensor_threat import assess_sensor_sample


class SensorThreatTests(unittest.TestCase):
    def make_features(self):
        return {
            "gps_expected": 1,
            "gps_available": 1,
            "gps_source_age_s": 0.0,
            "gps_innovation_norm_m": 0.2,
            "gps_mahalanobis_squared": 0.1,
            "gps_measurement_rejected": 0,
            "accelerometer_gravity_residual_m_s2": 0.1,
            "gyroscope_norm_rad_s": 0.02,
            "imu_missing": 0,
        }

    def test_assessment_is_normal_below_model_threshold(self):
        result = assess_sensor_sample(
            self.make_features(), model_score=0.2, decision_threshold=0.5,
        )
        self.assertFalse(result["detected"])
        self.assertEqual(result["classification"], "NORMAL")
        self.assertEqual(result["severity_percent"], 0)
        self.assertEqual(result["severity_level"], "low")

    def test_assessment_explains_combined_sensor_evidence(self):
        features = self.make_features()
        features["gps_measurement_rejected"] = 1
        features["accelerometer_gravity_residual_m_s2"] = 1.2
        result = assess_sensor_sample(
            features, model_score=0.9, decision_threshold=0.5,
        )
        self.assertTrue(result["detected"])
        self.assertEqual(result["classification"], "COMBINED_SENSOR_ANOMALY")
        self.assertEqual(result["severity_percent"], 90)
        self.assertEqual(result["severity_level"], "critical")
        self.assertEqual(len(result["evidence"]), 2)

    def test_assessment_validates_model_inputs(self):
        with self.assertRaisesRegex(ValueError, "between zero and one"):
            assess_sensor_sample(self.make_features(), model_score=1.1, decision_threshold=0.5)


if __name__ == "__main__":
    unittest.main()
