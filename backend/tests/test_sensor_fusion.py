import math
import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.research.sensor_fusion import (
    PositionVelocityBiasEkf,
    QuaternionComplementaryFilter,
    SimulationSensorFusion,
    euler_from_quaternion,
    quaternion_from_euler,
    rotate_body_to_world,
)


class SensorFusionTests(unittest.TestCase):
    def test_quaternion_round_trip_and_body_to_world_rotation(self):
        yaw = math.pi / 2
        quaternion = quaternion_from_euler(0.0, 0.0, yaw)
        self.assertAlmostEqual(euler_from_quaternion(quaternion)[2], yaw, places=7)
        rotated = rotate_body_to_world(quaternion, (1.0, 0.0, 0.0))
        np.testing.assert_allclose(rotated, (0.0, 1.0, 0.0), atol=1e-7)
        self.assertAlmostEqual(float(np.linalg.norm(quaternion)), 1.0, places=7)

    def test_complementary_attitude_converges_to_magnetic_heading(self):
        filter_ = QuaternionComplementaryFilter()
        yaw = 0.7
        for _ in range(50):
            estimate = filter_.update(
                (0.0, 0.0, 0.0),
                (0.0, 0.0, 9.80665),
                (math.cos(yaw), -math.sin(yaw), 0.0),
                0.1,
            )
        self.assertAlmostEqual(estimate["euler_rad"][2], yaw, places=2)
        self.assertAlmostEqual(
            float(np.linalg.norm(estimate["quaternion_body_to_world"])), 1.0, places=7,
        )

    def test_complementary_filter_estimates_constant_gyro_bias(self):
        filter_ = QuaternionComplementaryFilter()
        for _ in range(3000):
            estimate = filter_.update(
                (0.0, 0.0, 0.035),
                (0.0, 0.0, 9.80665),
                (1.0, 0.0, 0.0),
                0.1,
            )
        self.assertAlmostEqual(estimate["gyro_bias_rad_s"][2], 0.035, delta=0.002)
        self.assertAlmostEqual(estimate["euler_rad"][2], 0.0, delta=0.01)

    def test_ekf_estimates_accelerometer_bias_from_gps_updates(self):
        ekf = PositionVelocityBiasEkf((0.0, 0.0, 0.0), initial_time_s=0.0)
        for index in range(1, 601):
            timestamp = index * 0.1
            ekf.predict(timestamp, (0.2, -0.1, 0.0))
            if index % 5 == 0:
                self.assertTrue(
                    ekf.update_gps(timestamp, timestamp, (0.0, 0.0, 0.0), sigma_m=0.15)
                )
        np.testing.assert_allclose(ekf.state[6:9], (0.2, -0.1, 0.0), atol=0.002)
        self.assertGreater(float(np.linalg.eigvalsh(ekf.covariance).min()), 0)
        self.assertTrue(math.isfinite(float(ekf.covariance.trace())))

    def test_ekf_rejects_gps_outliers_and_out_of_sequence_measurements(self):
        ekf = PositionVelocityBiasEkf((0.0, 0.0, 0.0), initial_time_s=0.0)
        ekf.predict(0.1, (0.0, 0.0, 0.0))
        self.assertFalse(ekf.update_gps(0.1, 0.1, (100.0, -80.0, 0.0), sigma_m=0.5))
        self.assertEqual(ekf.last_gps_rejection_reason, "innovation_gate")
        self.assertLess(float(np.linalg.norm(ekf.state[:3])), 1.0)
        self.assertFalse(ekf.update_gps(0.0, 0.1, (0.0, 0.0, 0.0)))
        self.assertEqual(ekf.last_gps_rejection_reason, "out_of_sequence_measurement")
        self.assertTrue(ekf.update_gps(0.1, 0.1, (0.0, 0.0, 0.0)))

    def test_ekf_uses_absolute_innovation_limit_when_covariance_is_large(self):
        ekf = PositionVelocityBiasEkf((0.0, 0.0, 0.0), initial_time_s=0.0)
        for index in range(1, 101):
            ekf.predict(index * 0.1, None)
        self.assertFalse(ekf.update_gps(10.0, 10.0, (4.0, 0.0, 0.0), sigma_m=1.0))
        self.assertEqual(ekf.last_gps_rejection_reason, "innovation_gate")

    def test_ekf_prediction_increases_uncertainty_without_measurements(self):
        ekf = PositionVelocityBiasEkf((0.0, 0.0, 0.0), initial_time_s=0.0)
        initial_trace = float(np.trace(ekf.covariance))
        for index in range(1, 11):
            ekf.predict(index * 0.1, None)
        self.assertGreater(float(np.trace(ekf.covariance)), initial_trace)
        self.assertEqual(ekf.view()["coordinate_frame"], "synthetic_local_ENU")

    def test_sensor_fusion_reacquires_gps_only_after_three_consistent_fixes(self):
        fusion = SimulationSensorFusion()
        common = {
            "acceleration_body_m_s2": (0.0, 0.0, 9.80665),
            "gyro_body_rad_s": (0.0, 0.0, 0.0),
            "magnetic_body_gauss": (1.0, 0.0, 0.0),
        }
        fusion.update(
            timestamp_s=0.0,
            gps_position_enu_m=(0.0, 0.0, 0.0),
            gps_source_timestamp_s=0.0,
            **common,
        )
        for timestamp in (1.0, 2.0, 3.0, 4.0):
            fusion.update(
                timestamp_s=timestamp,
                gps_position_enu_m=None,
                gps_source_timestamp_s=None,
                **common,
            )
        first = fusion.update(
            timestamp_s=5.0,
            gps_position_enu_m=(5.0, 0.0, 0.0),
            gps_source_timestamp_s=5.0,
            **common,
        )
        second = fusion.update(
            timestamp_s=6.0,
            gps_position_enu_m=(5.2, 0.0, 0.0),
            gps_source_timestamp_s=6.0,
            **common,
        )
        recovered = fusion.update(
            timestamp_s=7.0,
            gps_position_enu_m=(5.4, 0.0, 0.0),
            gps_source_timestamp_s=7.0,
            **common,
        )
        self.assertFalse(first.navigation["gps_measurement_used"])
        self.assertFalse(second.navigation["gps_measurement_used"])
        self.assertTrue(recovered.navigation["gps_measurement_used"])
        self.assertLess(abs(recovered.navigation["position_enu_m"][0] - 5.2), 0.5)

    def test_attitude_rejects_non_monotonic_or_invalid_timesteps(self):
        filter_ = QuaternionComplementaryFilter()
        with self.assertRaisesRegex(ValueError, "timestep"):
            filter_.update((0, 0, 0), (0, 0, 1), (1, 0, 0), 0)
        with self.assertRaisesRegex(ValueError, "finite"):
            filter_.update((0, 0, float("nan")), (0, 0, 1), (1, 0, 0), 0.1)


if __name__ == "__main__":
    unittest.main()
