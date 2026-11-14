import numpy as np
import pytest
from pydantic import ValidationError

from app.research.attacks import (
    ATTACK_SPECS, ATTACK_TYPES, AttackConfig, GpsStream, GroundTruthLeak, OFFSET_RAMP_S,
    assert_no_ground_truth, assert_no_ground_truth_deep, attack_limits, inject,
)


def stream(seconds=100, hz=2.0):
    t = np.arange(0, seconds, 1 / hz)
    position = np.zeros((len(t), 3))
    position[:, 0] = 5.0 * t            # flying east at 5 m/s
    velocity = np.zeros((len(t), 3))
    velocity[:, 0] = 5.0
    return GpsStream(t, position, velocity)


def attack(type_, start=20, duration=30, magnitude=None, direction=0.0):
    spec = ATTACK_SPECS[type_].magnitude
    return AttackConfig(type=type_, start_s=start, duration_s=duration, direction_deg=direction,
                        magnitude=spec.default if magnitude is None else magnitude)


def test_single_shared_schema_covers_the_six_types_with_ranges():
    assert set(ATTACK_TYPES) == set(ATTACK_SPECS) == {"offset", "jump", "drift", "freeze", "noise", "velocity_inconsistency"}
    limits = attack_limits()
    assert {item["type"] for item in limits["types"]} == set(ATTACK_TYPES)
    assert limits["display_name"] == "Attack Lab"
    drift = next(item for item in limits["types"] if item["type"] == "drift")
    assert drift["magnitude"]["unit"] == "m/s"
    with pytest.raises(ValidationError):
        attack("offset", magnitude=5000)
    with pytest.raises(ValidationError):
        attack("freeze", magnitude=3)
    with pytest.raises(ValidationError):
        AttackConfig(type="offset", start_s=0, duration_s=0, magnitude=50)
    with pytest.raises(ValidationError):
        AttackConfig(type="weapon", start_s=0, duration_s=5, magnitude=50)  # type: ignore[arg-type]


def test_injection_is_deterministic_by_seed_and_does_not_modify_the_input():
    base = stream()
    original = base.position.copy()
    attacks = [AttackConfig(type="noise", start_s=10, duration_s=20, magnitude=10), AttackConfig(type="drift", start_s=40, duration_s=20, magnitude=1.0)]
    first = inject(base, attacks, seed=7).stream
    second = inject(base, attacks, seed=7).stream
    other = inject(base, attacks, seed=8).stream
    assert np.array_equal(first.position, second.position)
    assert not np.array_equal(first.position, other.position)
    assert np.array_equal(base.position, original)
    random_dir_a = inject(base, [AttackConfig(type="offset", start_s=10, duration_s=20, magnitude=50)], seed=3).stream.position
    random_dir_b = inject(base, [AttackConfig(type="offset", start_s=10, duration_s=20, magnitude=50)], seed=3).stream.position
    assert np.array_equal(random_dir_a, random_dir_b)


def displacement(result, base):
    return np.linalg.norm((result.stream.position - base.position)[:, :2], axis=1)


def test_each_attack_type_has_its_documented_effect_size():
    base = stream()
    t = base.t
    window = (t >= 20) & (t < 50)
    jump = inject(base, [attack("jump", magnitude=100)], 1)
    assert displacement(jump, base)[window] == pytest.approx(100.0)
    offset = inject(base, [attack("offset", magnitude=50)], 1)
    ramp_done = (t >= 20 + OFFSET_RAMP_S) & (t < 50)
    assert displacement(offset, base)[ramp_done] == pytest.approx(50.0)
    assert displacement(offset, base)[(t >= 20) & (t < 20 + OFFSET_RAMP_S)].max() < 50.0
    drift = inject(base, [attack("drift", magnitude=2.0)], 1)
    assert displacement(drift, base)[(t >= 40) & (t < 41)].mean() == pytest.approx(2.0 * 20.5, rel=0.05)
    freeze = inject(base, [attack("freeze")], 1)
    assert np.allclose(freeze.stream.position[window], base.position[np.argmax(window)])
    assert np.allclose(freeze.stream.velocity[window], 0.0)
    noise = inject(base, [attack("noise", magnitude=15)], 1)
    spread = (noise.stream.position - base.position)[window][:, 0].std()
    assert 10 < spread < 20
    vel = inject(base, [attack("velocity_inconsistency", magnitude=5.0, direction=90.0)], 1)
    assert np.allclose((vel.stream.velocity - base.velocity)[window][:, 1], 0.0, atol=1e-9)  # bearing 90 = east: no north component
    assert np.linalg.norm((vel.stream.velocity - base.velocity)[window], axis=1) == pytest.approx(5.0)
    assert np.allclose(vel.stream.position, base.position)  # position untouched


def test_window_boundaries_are_half_open_and_truth_is_separate():
    base = stream()
    result = inject(base, [attack("jump", start=20, duration=10)], 1)
    changed = displacement(result, base) > 1e-9
    assert changed.sum() == 20  # 10 s at 2 Hz: [20, 30)
    assert base.t[changed][0] == 20.0 and base.t[changed][-1] == 29.5
    assert result.truth.is_attacked.sum() == 20
    assert set(result.truth.attack_type[result.truth.is_attacked]) == {"jump"}
    assert (result.truth.attack_index[~result.truth.is_attacked] == -1).all()
    assert not hasattr(result.stream, "is_attacked")  # labels live only on the hidden truth object
    none = inject(base, [attack("jump", start=500, duration=10)], 1)
    assert not none.truth.is_attacked.any() and displacement(none, base).max() == 0


def test_overlapping_attacks_compose_in_start_order():
    base = stream()
    both = inject(base, [attack("jump", start=20, duration=30, magnitude=100, direction=0), attack("jump", start=30, duration=10, magnitude=40, direction=0)], 1)
    t = base.t
    assert displacement(both, base)[(t >= 20) & (t < 30)] == pytest.approx(100.0)
    assert displacement(both, base)[(t >= 30) & (t < 40)] == pytest.approx(140.0)
    assert displacement(both, base)[(t >= 40) & (t < 50)] == pytest.approx(100.0)
    reordered = inject(base, [attack("jump", start=30, duration=10, magnitude=40, direction=0), attack("jump", start=20, duration=30, magnitude=100, direction=0)], 1)
    assert np.allclose(both.stream.position, reordered.stream.position)


def test_ground_truth_guard_rejects_labels_and_schedules():
    assert_no_ground_truth(["res_pos", "battery_pct"])
    for bad in ("is_attacked", "attack_schedule", "label", "ground_truth_flag", "attack_type"):
        with pytest.raises(GroundTruthLeak):
            assert_no_ground_truth(["res_pos", bad])
    with pytest.raises(GroundTruthLeak):
        assert_no_ground_truth_deep({"window": [{"gps": [1, 2], "meta": {"attacks": []}}]})
    assert_no_ground_truth_deep({"window": [{"gps": [1, 2], "meta": {"battery": 80}}]})
