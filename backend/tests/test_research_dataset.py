import copy
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.research.dataset import FEATURE_COLUMNS, generate_dataset, load_dataset, validate_dataset, write_dataset
from app.research.dataset import SCENARIO_TYPES


class ResearchDatasetTests(unittest.TestCase):
    def make_bundle(self):
        return generate_dataset([11, 22, 33, 44, 55], duration=40, uav_count=3, task_count=40)

    def test_dataset_repeats_and_splits_by_seed_without_leakage(self):
        first = self.make_bundle()
        second = self.make_bundle()
        self.assertEqual(first["samples"], second["samples"])
        self.assertEqual(first["manifest"]["dataset_id"], second["manifest"]["dataset_id"])
        self.assertEqual(first["manifest"]["simulation_backend"], "deterministic_abstract_python")
        self.assertEqual(set(FEATURE_COLUMNS), set(first["manifest"]["features"]))
        self.assertEqual(tuple(first["manifest"]["scenario_types"]), SCENARIO_TYPES)
        self.assertIn("gps_velocity_disagreement", first["manifest"]["features"])
        groups = first["manifest"]["split_seeds"]
        self.assertFalse(set(groups["train"]) & set(groups["validation"]))
        self.assertFalse(set(groups["train"]) & set(groups["test"]))
        self.assertFalse(set(groups["validation"]) & set(groups["test"]))
        self.assertTrue(all({0, 1} == {row["attack_active"] for row in first["samples"] if row["split"] == split}
                            for split in groups))

    def test_velocity_inconsistency_fault_has_positive_gps_velocity_feature(self):
        bundle = self.make_bundle()
        rows = [row for row in bundle["samples"] if row["scenario_type"] == "velocity_inconsistency" and row["attack_active"]]
        self.assertTrue(rows)
        self.assertTrue(all(row["gps_velocity_disagreement"] >= 3.0 for row in rows))

    def test_quality_check_rejects_nonfinite_features(self):
        bundle = copy.deepcopy(self.make_bundle())
        bundle["samples"][0][FEATURE_COLUMNS[0]] = float("nan")
        with self.assertRaisesRegex(ValueError, "finite"):
            validate_dataset(bundle)

    def test_export_writes_csv_and_manifest_and_refuses_overwrite(self):
        bundle = self.make_bundle()
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / bundle["manifest"]["dataset_id"]
            csv_path, manifest_path = write_dataset(bundle, target)
            self.assertTrue(csv_path.is_file())
            self.assertTrue(manifest_path.is_file())
            loaded = load_dataset(target)
            self.assertEqual(loaded["samples"], bundle["samples"])
            self.assertEqual(loaded["manifest"]["dataset_id"], bundle["manifest"]["dataset_id"])
            with self.assertRaises(FileExistsError):
                write_dataset(bundle, target)

    def test_quality_check_rejects_tampered_dataset_hash(self):
        bundle = copy.deepcopy(self.make_bundle())
        bundle["samples"][0][FEATURE_COLUMNS[0]] += 1
        with self.assertRaisesRegex(ValueError, "content hash"):
            validate_dataset(bundle)

    def test_seed_and_duration_validation(self):
        with self.assertRaisesRegex(ValueError, "five distinct seeds"):
            generate_dataset([1, 2, 3, 4], duration=40)
        with self.assertRaisesRegex(ValueError, "duration"):
            generate_dataset([1, 2, 3, 4, 5], duration=19)


if __name__ == "__main__":
    unittest.main()
