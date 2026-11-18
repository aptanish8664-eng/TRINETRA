"""A full restart with fewer aircraft must not leave the earlier simulation's aircraft listed as "link lost"."""
from app.telemetry.gazebo import GazeboPoseStore
from app.telemetry.px4 import Px4TelemetryStore
from test_live_threat import feed


def test_the_telemetry_store_forgets_aircraft_that_are_not_kept():
    store = Px4TelemetryStore(stale_after_seconds=60.0, disconnected_after_seconds=120.0)
    for system_id in (1, 2, 3):
        feed(store, system_id, 5.0)
    assert sorted(store.vehicle_ids()) == ["px4-1", "px4-2", "px4-3"]
    assert sorted(store.forget_except({"px4-1", "px4-2"})) == ["px4-3"]
    assert sorted(store.vehicle_ids()) == ["px4-1", "px4-2"] and store.forget_except({"px4-1", "px4-2"}) == []


def test_the_pose_store_forgets_models_that_are_not_kept():
    store = GazeboPoseStore()
    store._models = {"x500_0": {"model_name": "x500_0", "_trail": []}, "x500_3": {"model_name": "x500_3", "_trail": []}}
    assert store.forget_except({"x500_0"}) == ["x500_3"] and list(store._models) == ["x500_0"]
