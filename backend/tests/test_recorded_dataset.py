import csv
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.research.recorded_dataset import load_recorded_sensor_csv, split_sessions
from app.research.recorded_features import extract_recorded_features


class RecordedDatasetTests(unittest.TestCase):
    def write_csv(self, path, fields, rows):
        with path.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)

    def test_imports_provenance_and_interpolates_imu_at_gps_epochs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            gps = root / "gps.csv"
            imu = root / "imu.csv"
            self.write_csv(gps, ["session_id", "timestamp_s", "latitude_deg", "longitude_deg", "altitude_m"], [
                {"session_id": "flight-a", "timestamp_s": 0.5, "latitude_deg": 45, "longitude_deg": 7, "altitude_m": 500}])
            imu_fields = ["session_id", "timestamp_s", "accel_x_m_s2", "accel_y_m_s2", "accel_z_m_s2", "gyro_x_rad_s", "gyro_y_rad_s", "gyro_z_rad_s"]
            self.write_csv(imu, imu_fields, [
                dict(session_id="flight-a", timestamp_s=0, accel_x_m_s2=0, accel_y_m_s2=0, accel_z_m_s2=9.8, gyro_x_rad_s=0, gyro_y_rad_s=0, gyro_z_rad_s=0),
                dict(session_id="flight-a", timestamp_s=1, accel_x_m_s2=2, accel_y_m_s2=0, accel_z_m_s2=9.8, gyro_x_rad_s=0, gyro_y_rad_s=0, gyro_z_rad_s=0)])
            result = load_recorded_sensor_csv(gps, imu, source_name="fixture", source_url="https://example.invalid/data", license_name="CC0", max_imu_bracket_s=0.5)
            self.assertEqual(result["manifest"]["origin"], "recorded_external_dataset")
            self.assertEqual(result["samples"][0]["imu"]["accel_x_m_s2"], 1.0)
            self.assertNotIn("label", result["samples"][0]["gps"])

    def test_rejects_missing_provenance_and_duplicate_timestamps(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); gps = root / "gps.csv"; imu = root / "imu.csv"
            gps_fields = ["session_id", "timestamp_s", "latitude_deg", "longitude_deg", "altitude_m"]
            self.write_csv(gps, gps_fields, [{"session_id": "f", "timestamp_s": 0, "latitude_deg": 0, "longitude_deg": 0, "altitude_m": 0}, {"session_id": "f", "timestamp_s": 0, "latitude_deg": 0, "longitude_deg": 0, "altitude_m": 0}])
            self.write_csv(imu, ["session_id", "timestamp_s", "accel_x_m_s2", "accel_y_m_s2", "accel_z_m_s2", "gyro_x_rad_s", "gyro_y_rad_s", "gyro_z_rad_s"], [{"session_id": "f", "timestamp_s": 0, "accel_x_m_s2": 0, "accel_y_m_s2": 0, "accel_z_m_s2": 0, "gyro_x_rad_s": 0, "gyro_y_rad_s": 0, "gyro_z_rad_s": 0}])
            with self.assertRaisesRegex(ValueError, "provenance"):
                load_recorded_sensor_csv(gps, imu, source_name="", source_url="", license_name="")
            with self.assertRaisesRegex(ValueError, "duplicates"):
                load_recorded_sensor_csv(gps, imu, source_name="x", source_url="https://example.invalid", license_name="CC0")

    def test_splits_whole_sessions_and_requires_grouped_holdout(self):
        split = split_sessions(["a", "b", "c", "d", "e"], seed=8)
        groups = [set(split[name]) for name in ("train", "validation", "test")]
        self.assertFalse(groups[0] & groups[1] or groups[0] & groups[2] or groups[1] & groups[2])
        with self.assertRaisesRegex(ValueError, "three distinct"):
            split_sessions(["only", "only"])

    def test_recorded_feature_extraction_keeps_labels_separate(self):
        gps = lambda lat: {"latitude_deg": lat, "longitude_deg": 7.0, "altitude_m": 500.0}
        imu = {"accel_x_m_s2": 0.0, "accel_y_m_s2": 0.0, "accel_z_m_s2": 9.8,
               "gyro_x_rad_s": 0.0, "gyro_y_rad_s": 0.0, "gyro_z_rad_s": 0.1}
        bundle = {"samples": [
            {"session_id": "flight-a", "timestamp_s": 0.0, "gps": gps(45.0), "imu": imu},
            {"session_id": "flight-a", "timestamp_s": 1.0, "gps": gps(45.0001), "imu": imu}],
            "labels": [{"session_id": "flight-a", "timestamp_s": 1.0, "label": "synthetic-test-label"}]}
        result = extract_recorded_features(bundle)
        self.assertIsNone(result["features"][0]["gps_velocity_enu_m_s"])
        self.assertGreater(result["features"][1]["gps_speed_m_s"], 10)
        self.assertNotIn("label", result["features"][1])
        self.assertEqual(result["targets"][0]["label"], "synthetic-test-label")


if __name__ == "__main__":
    unittest.main()
