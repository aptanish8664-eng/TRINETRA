import copy
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.research.sensor_dataset import (
    SENSOR_SCENARIO_TYPES,
    generate_sensor_dataset,
    load_sensor_dataset,
    validate_sensor_dataset,
    write_sensor_dataset,
)


class SensorDatasetTests(unittest.TestCase):
    def make_bundle(self):
        return generate_sensor_dataset(range(5), duration_s=10, sample_rate_hz=2)

    def test_sensor_dataset_is_reproducible_complete_and_seed_grouped(self):
        first = self.make_bundle()
        second = self.make_bundle()
        self.assertEqual(first["samples"], second["samples"])
        self.assertEqual(first["manifest"]["dataset_id"], second["manifest"]["dataset_id"])
        self.assertEqual(tuple(first["manifest"]["scenario_types"]), SENSOR_SCENARIO_TYPES)
        self.assertEqual(
            first["manifest"]["simulation_backend"],
            "deterministic_software_only_sensor_simulator",
        )
        self.assertEqual(
            {row["scenario_type"] for row in first["samples"]},
            set(SENSOR_SCENARIO_TYPES),
        )
        split_groups = first["manifest"]["split_seeds"]
        self.assertFalse(set(split_groups["train"]) & set(split_groups["validation"]))
        self.assertFalse(set(split_groups["train"]) & set(split_groups["test"]))
        self.assertFalse(set(split_groups["validation"]) & set(split_groups["test"]))
        for scenario_id in {row["scenario_id"] for row in first["samples"]}:
            self.assertEqual(
                len({row["split"] for row in first["samples"] if row["scenario_id"] == scenario_id}),
                1,
            )
        self.assertTrue(all(row["timestamp_s"] is not None for row in first["samples"]))
        self.assertTrue(all(row["uav_id"] == "SIM-UAV-01" for row in first["samples"]))
        self.assertTrue(all(row["estimated_quaternion_w"] is not None for row in first["samples"]))
        initial_clean_sample = next(
            row for row in first["samples"]
            if row["scenario_type"] == "normal" and row["timestamp_s"] == 0
        )
        self.assertEqual(initial_clean_sample["gps_measurement_rejected"], 0)

    def test_fault_scenarios_include_missing_delayed_and_recovery_labels(self):
        bundle = self.make_bundle()
        rows = bundle["samples"]
        self.assertTrue(any(row["gps_source_age_s"] > 0 for row in rows))
        self.assertTrue(any(row["imu_missing"] for row in rows))
        self.assertTrue(any(
            row["scenario_type"] == "gps_outage"
            and row["fault_active"]
            and row["gps_expected"]
            and not row["gps_available"]
            for row in rows
        ))
        self.assertTrue(any(row["fault_recovery"] for row in rows))
        self.assertTrue(any(row["gps_measurement_rejected"] for row in rows))
        self.assertTrue(any(row["scenario_type"] == "combined_faults" and row["fault_active"] for row in rows))

    def test_dataset_validation_rejects_tampered_content_and_nonfinite_features(self):
        bundle = self.make_bundle()
        altered = copy.deepcopy(bundle)
        altered["samples"][0]["truth_x_m"] += 1
        with self.assertRaisesRegex(ValueError, "content hash"):
            validate_sensor_dataset(altered)
        altered = copy.deepcopy(bundle)
        altered["samples"][0]["gps_innovation_norm_m"] = float("nan")
        with self.assertRaisesRegex(ValueError, "must be finite"):
            validate_sensor_dataset(altered)

    def test_csv_export_load_is_hash_checked_and_refuses_overwrite(self):
        bundle = self.make_bundle()
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / bundle["manifest"]["dataset_id"]
            csv_path, manifest_path = write_sensor_dataset(bundle, target)
            self.assertTrue(csv_path.is_file())
            self.assertTrue(manifest_path.is_file())
            loaded = load_sensor_dataset(target)
            self.assertEqual(loaded, bundle)
            with self.assertRaises(FileExistsError):
                write_sensor_dataset(bundle, target)

    def test_generator_requires_distinct_seed_groups_and_safe_ranges(self):
        with self.assertRaisesRegex(ValueError, "five distinct seeds"):
            generate_sensor_dataset((1, 2, 3, 4))
        with self.assertRaisesRegex(ValueError, "duration_s"):
            generate_sensor_dataset(range(5), duration_s=3)
        with self.assertRaisesRegex(ValueError, "sample_rate_hz"):
            generate_sensor_dataset(range(5), sample_rate_hz=101)


if __name__ == "__main__":
    unittest.main()
