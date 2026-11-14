import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.research.spoofing_detector import GpsImuConsistencyDetector, SensorObservation


class SpoofingDetectorTests(unittest.TestCase):
    def test_normal_constant_velocity_is_not_an_anomaly(self):
        detector = GpsImuConsistencyDetector()
        outputs = []
        for i in range(5):
            outputs.append(detector.update(SensorObservation(i, (float(i), 0, 0), (0, 0, 0),
                                                               (1, 0, 0), gps_accuracy_m=1, satellite_count=12)))
        self.assertEqual(outputs[-1]["status"], "NORMAL")
        self.assertIsNone(outputs[-1]["confidence"])

    def test_persistent_position_velocity_conflict_has_structured_evidence(self):
        detector = GpsImuConsistencyDetector()
        detector.update(SensorObservation(0, (0, 0, 0), (0, 0, 0), (0, 0, 0)))
        results = [detector.update(SensorObservation(i, (10.0 * i, 0, 0), (0, 0, 0), (-9, 0, 0))) for i in range(1, 5)]
        self.assertEqual(results[-1]["status"], "LIKELY_GNSS_SPOOFING")
        self.assertEqual(results[-1]["anomaly_score"], 2)
        self.assertEqual({e["code"] for e in results[-1]["evidence"]}, {"position_discontinuity", "gps_velocity_position_residual"})
        self.assertIn("not a spoof probability", results[-1]["limitations"])

    def test_low_quality_and_out_of_order_are_not_labeled_spoofing(self):
        detector = GpsImuConsistencyDetector()
        detector.update(SensorObservation(0, (0, 0, 0), (0, 0, 0)))
        poor = detector.update(SensorObservation(1, (1, 0, 0), (0, 0, 0), gps_accuracy_m=20, satellite_count=3))
        self.assertEqual(poor["status"], "INSUFFICIENT_DATA")
        self.assertEqual(detector.update(SensorObservation(0.5, (1, 0, 0), (0, 0, 0)))["status"], "INSUFFICIENT_DATA")

    def test_course_heading_conflict_is_reported_without_spoof_certainty(self):
        detector = GpsImuConsistencyDetector()
        detector.update(SensorObservation(0, (0, 0, 0), (0, 0, 0), gps_course_rad=0, imu_heading_rad=0))
        result = detector.update(SensorObservation(1, (1, 0, 0), (0, 0, 0), gps_course_rad=1.5, imu_heading_rad=0))
        self.assertEqual(result["status"], "SUSPICIOUS")
        self.assertEqual(result["evidence"][0]["code"], "gps_course_imu_heading_residual")


if __name__ == "__main__":
    unittest.main()
