import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.simulation.engine import Simulation, SimulationConfig
from app.simulation.response import reassignment_block_reason, reassign_tasks


class SimulationTests(unittest.TestCase):
    def build(self, **overrides):
        values = {"duration": 100, "spoof_start": 5, "spoof_duration": 30}
        values.update(overrides)
        return Simulation(SimulationConfig(**values))

    def test_seed_and_config_are_reproducible(self):
        a, b = self.build(), self.build()
        a.start(); b.start()
        for _ in range(8):
            va, vb = a.step(), b.step()
        self.assertEqual(va["uavs"], vb["uavs"])
        self.assertEqual(va["tasks"], vb["tasks"])
        self.assertEqual(va["events"], vb["events"])

    def test_pause_freezes_simulation_time(self):
        sim = self.build()
        sim.start(); sim.step(); sim.pause()
        frozen = sim.view()
        self.assertEqual(sim.view()["tick"], frozen["tick"])
        self.assertEqual(sim.view()["uavs"], frozen["uavs"])

    def test_explicit_step_advances_while_paused(self):
        sim = self.build()
        sim.start(); sim.step(); sim.pause()
        sim.step()
        self.assertEqual(sim.view()["tick"], 2)
        self.assertEqual(sim.view()["status"], "paused")

    def test_stop_freezes_future_steps(self):
        sim = self.build()
        sim.start(); sim.step(); sim.stop()
        tick = sim.view()["tick"]
        sim.step()
        self.assertEqual(sim.view()["tick"], tick)
        self.assertEqual(sim.view()["status"], "stopped")

    def test_reported_gps_is_separate_from_ground_truth(self):
        sim = self.build(spoofing_type="sudden", spoof_magnitude=4.0)
        sim.start()
        for _ in range(7):
            view = sim.step()
        target = view["uavs"]["UAV-02"]
        self.assertNotEqual(target["position"], target["gps"])
        self.assertEqual(view["coordinate_system"], "abstract simulation units; not geographic coordinates")

    def test_spoofing_only_active_during_configured_window(self):
        sim = self.build(spoofing_type="sudden", spoof_start=3, spoof_duration=2, spoof_magnitude=2)
        self.assertEqual(sim._spoof_offset("UAV-02"), (0.0, 0.0))
        sim.tick_count = 3
        self.assertEqual(sim._spoof_offset("UAV-02"), (2, 0.44))
        sim.tick_count = 5
        self.assertEqual(sim._spoof_offset("UAV-02"), (0.0, 0.0))

    def test_reset_restores_seeded_initial_state(self):
        sim = self.build()
        expected = self.build().view()
        sim.start(); sim.step(); reset = sim.reset()
        self.assertEqual(reset["tick"], 0)
        self.assertEqual(reset["status"], "ready")
        self.assertEqual(reset["uavs"], expected["uavs"])
        self.assertEqual(reset["tasks"], expected["tasks"])

    def test_task_completion_is_recorded_once(self):
        sim = self.build(task_count=1, spoofing_type="none")
        task = next(iter(sim.state["tasks"].values()))
        sim.state["uavs"][task["uav_id"]]["position"] = dict(task["position"])
        sim.step(); sim.step()
        matching = [event for event in sim.events if event["type"] == "task_completed" and event["task_id"] == task["id"]]
        self.assertEqual(len(matching), 1)
        self.assertEqual(task["status"], "complete")

    def test_invalid_configuration_is_rejected(self):
        with self.assertRaises(ValueError):
            Simulation(SimulationConfig(uav_count=1))
        with self.assertRaises(ValueError):
            Simulation(SimulationConfig(detector_threshold=0))

    def test_detector_threshold_can_be_configured_and_resets(self):
        sim = self.build(detector_threshold=0.2)
        self.assertEqual(sim.view()["calibration"]["threshold"], 0.2)
        sim.start(); sim.step(); sim.reset()
        self.assertEqual(sim.view()["calibration"]["threshold"], 0.2)

    def test_initial_tasks_have_single_valid_owner(self):
        sim = self.build(task_count=8)
        owners = [task["uav_id"] for task in sim.state["tasks"].values()]
        self.assertTrue(all(owner in sim.state["uavs"] for owner in owners))

    def test_persistent_spoof_is_detected_and_adaptively_isolated(self):
        sim = self.build(spoofing_type="sudden", spoof_start=5, spoof_duration=30)
        sim.start()
        for _ in range(10):
            sim.step()
        self.assertEqual(sim.state["uavs"]["UAV-02"]["status"], "isolated")
        self.assertEqual(sum(event["type"] == "uav_isolated" for event in sim.events), 1)
        self.assertGreaterEqual(sum(event["type"] == "task_reassigned" for event in sim.events), 1)

    def test_gradual_drift_accumulates_as_telemetry_anomaly(self):
        sim = self.build(spoofing_type="gradual", spoof_start=5, spoof_duration=20, spoof_magnitude=4)
        sim.start()
        for _ in range(11):
            sim.step()
        self.assertEqual(sim.state["uavs"]["UAV-02"]["status"], "isolated")

    def test_jump_is_a_single_tick_and_labels_only_the_applied_tick(self):
        sim = self.build(spoofing_type="jump", spoof_start=2, spoof_duration=5)
        sim.tick_count = 1
        self.assertFalse(sim.fault_active_at_current_tick("UAV-02"))
        sim.tick_count = 2
        self.assertTrue(sim.fault_active_at_current_tick("UAV-02"))
        self.assertEqual(sim._spoof_offset("UAV-02"), (3.0, 0.66))
        sim.tick_count = 3
        self.assertFalse(sim.fault_active_at_current_tick("UAV-02"))
        self.assertEqual(sim._spoof_offset("UAV-02"), (0.0, 0.0))

    def test_freeze_holds_reported_fix_without_freezing_truth(self):
        sim = self.build(spoofing_type="freeze", spoof_start=1, spoof_duration=4, response_policy="monitor")
        sim.step(); first = sim.view()["uavs"]["UAV-02"]
        sim.step(); second = sim.view()["uavs"]["UAV-02"]
        sim.step(); third = sim.view()["uavs"]["UAV-02"]
        self.assertEqual(first["gps"], second["gps"])
        self.assertEqual(second["gps"], third["gps"])
        self.assertNotEqual(first["position"], third["position"])
        self.assertTrue(sim.fault_active_at_current_tick("UAV-02"))

    def test_noise_and_degradation_have_deterministic_active_windows(self):
        noisy = self.build(spoofing_type="noise", spoof_start=2, spoof_duration=5)
        degraded = self.build(spoofing_type="degradation", spoof_start=2, spoof_duration=5)
        noisy.tick_count = degraded.tick_count = 2
        self.assertEqual(noisy._gps_noise_sigma("UAV-02"), 0.12)
        self.assertAlmostEqual(degraded._gps_noise_sigma("UAV-02"), 0.058)
        degraded.tick_count = 6
        self.assertAlmostEqual(degraded._gps_noise_sigma("UAV-02"), 0.25)
        degraded.tick_count = 7
        self.assertEqual(degraded._gps_noise_sigma("UAV-02"), 0.01)

    def test_velocity_inconsistency_is_separate_from_position_and_truth(self):
        sim = self.build(spoofing_type="velocity_inconsistency", spoof_start=1, spoof_duration=4)
        view = sim.step()
        uav = view["uavs"]["UAV-02"]
        self.assertNotEqual(uav["gps_velocity"], uav["velocity"])
        self.assertTrue(sim.fault_active_at_current_tick("UAV-02"))

    def test_software_gps_faults_do_not_change_truth_trajectory(self):
        for fault in ("jump", "freeze", "noise", "degradation", "velocity_inconsistency"):
            clean = self.build(spoofing_type="none", response_policy="monitor")
            attacked = self.build(spoofing_type=fault, response_policy="monitor")
            for _ in range(5):
                clean_state = clean.step()
                attacked_state = attacked.step()
                self.assertEqual(
                    {uid: row["position"] for uid, row in clean_state["uavs"].items()},
                    {uid: row["position"] for uid, row in attacked_state["uavs"].items()},
                    fault,
                )

    def test_reassignment_respects_capacity(self):
        sim = self.build(uav_count=2, task_count=6, spoofing_type="sudden", spoof_start=2, spoof_duration=20)
        sim.start()
        for _ in range(7):
            sim.step()
        for uid, uav in sim.state["uavs"].items():
            if uav["status"] == "isolated":
                continue
            owned = sum(task["status"] == "pending" and task["uav_id"] == uid for task in sim.state["tasks"].values())
            self.assertLessEqual(owned, uav["capacity"])

    def test_reassignment_selects_nearest_candidate_that_meets_all_resources(self):
        sim = self.build(uav_count=4, task_count=4, spoofing_type="none")
        task = sim.state["tasks"]["T-02"]
        source_id = task["uav_id"]
        required = task["required_capability"]
        task["position"] = {"x": 5.0, "y": 5.0}
        task["payload_required_kg"] = 1.0

        # Closest is out of communications; next is missing the required capability.
        sim.state["uavs"]["UAV-01"]["position"] = {"x": 5.1, "y": 5.0}
        sim.state["uavs"]["UAV-01"]["communications_available"] = False
        sim.state["uavs"]["UAV-03"]["position"] = {"x": 5.2, "y": 5.0}
        sim.state["uavs"]["UAV-03"]["capabilities"] = [cap for cap in sim.state["uavs"]["UAV-03"]["capabilities"] if cap != required]
        sim.state["uavs"]["UAV-04"]["position"] = {"x": 7.0, "y": 5.0}
        sim.state["uavs"]["UAV-04"]["capabilities"] = list(set(sim.state["uavs"]["UAV-04"]["capabilities"]) | {required})
        sim.state["uavs"]["UAV-04"]["payload_capacity_kg"] = 1.5
        sim.state["uavs"]["UAV-04"]["endurance_remaining_ticks"] = 200
        sim.state["uavs"]["UAV-04"]["endurance_reserve_ticks"] = 10

        events = reassign_tasks(sim.state, source_id)
        reassigned = next(event for event in events if event["task_id"] == task["id"])
        self.assertEqual(reassigned["type"], "task_reassigned")
        self.assertEqual(reassigned["uav_id"], "UAV-04")
        source_task_id = sim.state["uavs"][source_id]["task_id"]
        if source_task_id is not None:
            self.assertEqual(sim.state["tasks"][source_task_id]["uav_id"], source_id)
        self.assertEqual(sim.state["uavs"]["UAV-04"]["task_id"], "T-04")

    def test_reassignment_rejects_payload_capability_link_and_reserve_failures(self):
        sim = self.build(uav_count=3, task_count=3, spoofing_type="none")
        task = sim.state["tasks"]["T-02"]
        source_id = task["uav_id"]
        candidate = sim.state["uavs"]["UAV-01"]
        task["required_capability"] = "survey"
        task["payload_required_kg"] = 2.0
        candidate["capabilities"] = []
        self.assertIn("missing capability", reassignment_block_reason(sim.state, source_id, task, candidate))

        candidate["capabilities"] = ["survey"]
        candidate["payload_capacity_kg"] = 1.0
        self.assertEqual(reassignment_block_reason(sim.state, source_id, task, candidate), "payload capacity insufficient")

        candidate["payload_capacity_kg"] = 3.0
        candidate["workload_capacity"] = 0
        self.assertEqual(reassignment_block_reason(sim.state, source_id, task, candidate), "workload capacity exhausted")
        candidate["workload_capacity"] = candidate["capacity"]

        candidate["payload_capacity_kg"] = 3.0
        candidate["communications_available"] = False
        self.assertEqual(reassignment_block_reason(sim.state, source_id, task, candidate), "communications unavailable")

        candidate["communications_available"] = True
        task["position"] = {"x": 9.0, "y": 9.0}
        candidate["position"] = {"x": 0.0, "y": 0.0}
        candidate["endurance_remaining_ticks"] = 5
        candidate["endurance_reserve_ticks"] = 4
        candidate["endurance_total_ticks"] = 100
        candidate["battery_pct"] = 100.0
        candidate["battery_reserve_pct"] = 20.0
        self.assertEqual(reassignment_block_reason(sim.state, source_id, task, candidate), "endurance reserve would be breached")

    def test_task_is_left_unassigned_with_explicit_reasons_when_no_resource_fit_exists(self):
        sim = self.build(uav_count=2, task_count=4, spoofing_type="none")
        task = sim.state["tasks"]["T-02"]
        source_id = task["uav_id"]
        recipient_id = next(uid for uid in sim.state["uavs"] if uid != source_id)
        recipient = sim.state["uavs"][recipient_id]
        recipient["communications_available"] = False

        events = reassign_tasks(sim.state, source_id)
        event = next(event for event in events if event["task_id"] == task["id"])
        self.assertEqual(event["type"], "task_unassigned")
        self.assertIsNone(task["uav_id"])
        self.assertIn("communications unavailable", event["reason"])

    def test_reassignment_allocates_limited_capacity_to_highest_priority_first(self):
        sim = self.build(uav_count=2, task_count=4, spoofing_type="none")
        source_id, target_id = "UAV-01", "UAV-02"
        source_tasks = [task for task in sim.state["tasks"].values() if task["uav_id"] == source_id]
        target = sim.state["uavs"][target_id]
        target["capabilities"] = ["survey", "relay", "inspection"]
        target["payload_capacity_kg"] = 10.0
        target["workload_capacity"] = 1
        for task in sim.state["tasks"].values():
            if task["uav_id"] == target_id:
                task["status"] = "complete"
        source_tasks[0]["priority"] = 1
        source_tasks[1]["priority"] = 3
        for task in source_tasks:
            task["required_capability"] = "survey"
            task["payload_required_kg"] = 0.5

        events = reassign_tasks(sim.state, source_id)
        by_task = {event["task_id"]: event["type"] for event in events}

        self.assertEqual(by_task[source_tasks[1]["id"]], "task_reassigned")
        self.assertEqual(by_task[source_tasks[0]["id"]], "task_unassigned")

    def test_endurance_is_reduced_as_abstract_vehicle_travels(self):
        sim = self.build(uav_count=2, task_count=2, spoofing_type="none")
        uav = sim.state["uavs"]["UAV-01"]
        before = uav["endurance_remaining_ticks"]
        sim.step()
        self.assertLess(uav["endurance_remaining_ticks"], before)
        self.assertLess(uav["battery_pct"], 100.0)
        self.assertIn("workload", sim.view()["uavs"]["UAV-01"])

    def test_endurance_reserve_stops_new_work_and_releases_pending_tasks(self):
        sim = self.build(uav_count=2, task_count=2, spoofing_type="none")
        source = sim.state["uavs"]["UAV-01"]
        task = sim.state["tasks"]["T-01"]
        task["position"] = {"x": 9.0, "y": 9.0}
        source["position"] = {"x": 0.0, "y": 0.0}
        source["endurance_remaining_ticks"] = source["endurance_reserve_ticks"] + 1
        sim.state["uavs"]["UAV-02"]["communications_available"] = False

        sim.step()

        self.assertEqual(source["readiness"], "low_endurance")
        self.assertLessEqual(source["battery_pct"], source["battery_reserve_pct"])
        self.assertIsNone(task["uav_id"])
        self.assertTrue(any(event["type"] == "uav_low_endurance" for event in sim.events))
        self.assertTrue(any(event["type"] == "task_unassigned" and event["task_id"] == task["id"] for event in sim.events))

    def test_monitor_policy_detects_without_isolation(self):
        sim = self.build(response_policy="monitor", spoofing_type="sudden", spoof_start=5, spoof_duration=30)
        sim.start()
        for _ in range(10):
            sim.step()
        self.assertEqual(sim.state["uavs"]["UAV-02"]["status"], "degraded")
        self.assertFalse(any(event["type"] == "uav_isolated" for event in sim.events))


if __name__ == "__main__":
    unittest.main()
