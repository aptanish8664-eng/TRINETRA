"""Shared GPS-attack schema and deterministic seeded injector.

ONE definition of attack types, parameters, units, ranges and defaults. Attack Lab (experiments), the dataset
generator and the offline replay all import from here; nothing re-declares it.

Software-only: the injector operates on a *software copy* of a GPS stream in a local ENU frame (flat-earth
approximation around the stream origin; fine for areas of a few kilometres). PX4 and its EKF never see it, and
nothing here sends a command anywhere.

Ground truth (which samples are attacked, attack types and schedule) is returned separately as `AttackTruth`.
It must never be used as a detector or threat-engine input and must never be shown in Mission Control or
Scenario Lab; `assert_no_ground_truth` is the guard used by the research API and the feature builders.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Iterable, Literal, Optional, Sequence

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, model_validator

ATTACK_LAB_NAME = "Attack Lab"

AttackType = Literal["offset", "jump", "drift", "freeze", "noise", "velocity_inconsistency"]
ATTACK_TYPES: tuple[str, ...] = ("offset", "jump", "drift", "freeze", "noise", "velocity_inconsistency")

# Offset ramps in over this many seconds (an instantaneous step is what `jump` models).
OFFSET_RAMP_S = 3.0


@dataclass(frozen=True)
class ParamSpec:
    key: str
    label: str
    unit: str
    minimum: float
    maximum: float
    default: float


@dataclass(frozen=True)
class AttackSpec:
    type: str
    label: str
    summary: str
    magnitude: ParamSpec
    uses_direction: bool


def _spec(type_: str, label: str, summary: str, unit: str, minimum: float, maximum: float, default: float,
          magnitude_label: str, uses_direction: bool = True) -> AttackSpec:
    return AttackSpec(type_, label, summary, ParamSpec("magnitude", magnitude_label, unit, minimum, maximum, default), uses_direction)


ATTACK_SPECS: dict[str, AttackSpec] = {item.type: item for item in (
    _spec("offset", "Position offset", "The reported position is moved a fixed distance to one side and held there (it slides over in about 3 seconds).", "m", 5, 500, 50, "Offset distance"),
    _spec("jump", "Position jump", "The reported position leaps a fixed distance in one step, usually for a short time.", "m", 10, 500, 100, "Jump distance"),
    _spec("drift", "Gradual drift", "The reported position creeps away at a steady rate, so the error grows slowly.", "m/s", 0.1, 10, 1.0, "Drift rate"),
    _spec("freeze", "Position freeze", "The reported position stops updating while the aircraft keeps moving.", "", 0, 0, 0, "Not used", uses_direction=False),
    _spec("noise", "Noise burst", "Random scatter is added to the reported position; it does not pull in one direction.", "m", 1, 100, 15, "Noise standard deviation", uses_direction=False),
    _spec("velocity_inconsistency", "Velocity inconsistency", "The reported velocity disagrees with how the aircraft is really moving, by a fixed bias.", "m/s", 0.5, 30, 5, "Velocity bias"),
)}
DURATION_RANGE_S = (1.0, 3600.0)
START_RANGE_S = (0.0, 86_400.0)

# What each experiment field means, in plain words. Served by the limits endpoint and mirrored in the frontend.
FIELD_HELP: dict[str, str] = {
    "start": "Seconds after the aircraft starts its route when the attack begins.",
    "duration": "How long the attack lasts, in seconds. It must end before the route does.",
    "magnitude": "How strong the attack is: a distance in metres, a rate in m/s, or a noise level, depending on the type.",
    "direction": "Compass direction the reported position is pushed (0 = north, 90 = east). Leave it blank for a random direction fixed by the seed.",
    "seed": "A number that fixes every random choice, so the same seed repeats the experiment exactly.",
}
HIDDEN_SCHEDULE_NOTE = "Operators will not see this schedule while the experiment runs. It is shown only in the evaluation after the run ends."
SOFTWARE_ONLY_NOTE = "This is a software simulation. It changes only the simulated GPS data the monitor reads; it does not affect the aircraft."


def attack_limits() -> dict[str, Any]:
    """JSON-serialisable specs for the API and for frontends (which must not hard-code these ranges)."""
    return {
        "types": [{
            "type": spec.type, "label": spec.label, "summary": spec.summary, "uses_direction": spec.uses_direction,
            "magnitude": {"label": spec.magnitude.label, "unit": spec.magnitude.unit, "min": spec.magnitude.minimum,
                          "max": spec.magnitude.maximum, "default": spec.magnitude.default},
        } for spec in ATTACK_SPECS.values()],
        "duration_s": {"min": DURATION_RANGE_S[0], "max": DURATION_RANGE_S[1]},
        "start_s": {"min": START_RANGE_S[0], "max": START_RANGE_S[1]},
        "direction_deg": {"min": 0, "max": 360, "note": "omit for a seeded random direction"},
        "display_name": ATTACK_LAB_NAME,
        "field_help": dict(FIELD_HELP),
        "notes": {"hidden_schedule": HIDDEN_SCHEDULE_NOTE, "software_only": SOFTWARE_ONLY_NOTE},
        "frame": "local ENU around the aircraft start (flat-earth approximation)",
    }


class AttackConfig(BaseModel):
    """One simulated GPS attack. Range checks are enforced here, from `ATTACK_SPECS`."""
    model_config = ConfigDict(extra="forbid")
    target_aircraft: Optional[str] = Field(default=None, max_length=16, pattern=r"^px4-[1-9][0-9]{0,2}$")
    type: AttackType
    start_s: float = Field(ge=START_RANGE_S[0], le=START_RANGE_S[1], allow_inf_nan=False)
    duration_s: float = Field(ge=DURATION_RANGE_S[0], le=DURATION_RANGE_S[1], allow_inf_nan=False)
    magnitude: float = Field(default=0.0, allow_inf_nan=False)
    direction_deg: Optional[float] = Field(default=None, ge=0, lt=360, allow_inf_nan=False)

    @model_validator(mode="after")
    def check_magnitude(self) -> "AttackConfig":
        spec = ATTACK_SPECS[self.type].magnitude
        if self.type == "freeze":
            if self.magnitude != 0:
                raise ValueError("freeze takes no magnitude")
        elif not spec.minimum <= self.magnitude <= spec.maximum:
            raise ValueError(f"{self.type} magnitude must be between {spec.minimum:g} and {spec.maximum:g} {spec.unit}")
        return self

    @property
    def end_s(self) -> float:
        return self.start_s + self.duration_s


@dataclass
class GpsStream:
    """Time series of GPS-reported state in local ENU metres (east, north, up) and m/s."""
    t: np.ndarray            # (N,) seconds
    position: np.ndarray     # (N, 3)
    velocity: np.ndarray     # (N, 3)

    def copy(self) -> "GpsStream":
        return GpsStream(self.t.copy(), self.position.copy(), self.velocity.copy())


@dataclass
class AttackTruth:
    """HIDDEN ground truth. Stored apart from features; never an input to detection or threat scoring."""
    is_attacked: np.ndarray        # (N,) bool
    attack_index: np.ndarray       # (N,) int, -1 = none, otherwise index of the first active attack
    attack_type: np.ndarray        # (N,) '<U24', '' = none


@dataclass
class InjectionResult:
    stream: GpsStream
    truth: AttackTruth


def _direction(attack: AttackConfig, seed: int, index: int) -> np.ndarray:
    degrees = attack.direction_deg
    if degrees is None:
        degrees = float(np.random.default_rng([seed, index, 17]).uniform(0.0, 360.0))
    radians = math.radians(degrees)
    return np.array([math.sin(radians), math.cos(radians), 0.0])  # bearing clockwise from north


def inject(stream: GpsStream, attacks: Sequence[AttackConfig], seed: int) -> InjectionResult:
    """Deterministic: identical (stream, attacks, seed) always gives identical output. Input is not modified."""
    out = stream.copy()
    is_attacked = np.zeros(len(out.t), dtype=bool)
    attack_index = np.full(len(out.t), -1, dtype=int)
    attack_type = np.full(len(out.t), "", dtype="<U24")
    ordered = sorted(enumerate(attacks), key=lambda item: (item[1].start_s, item[0]))
    for index, attack in ordered:
        mask = (out.t >= attack.start_s) & (out.t < attack.end_s)
        if not mask.any():
            continue
        unit = _direction(attack, seed, index)
        elapsed = out.t[mask] - attack.start_s
        if attack.type == "offset":
            scale = np.minimum(1.0, elapsed / OFFSET_RAMP_S)
            out.position[mask] += scale[:, None] * attack.magnitude * unit
        elif attack.type == "jump":
            out.position[mask] += attack.magnitude * unit
        elif attack.type == "drift":
            out.position[mask] += (attack.magnitude * elapsed)[:, None] * unit
        elif attack.type == "freeze":
            first = int(np.argmax(mask))
            out.position[mask] = out.position[first]
            out.velocity[mask] = 0.0
        elif attack.type == "noise":
            rng = np.random.default_rng([seed, index, 29])
            noise = rng.normal(0.0, attack.magnitude, size=(int(mask.sum()), 3))
            noise[:, 2] = 0.0
            out.position[mask] += noise
        elif attack.type == "velocity_inconsistency":
            out.velocity[mask] += attack.magnitude * unit
        first_unlabelled = mask & (attack_index < 0)
        attack_index[first_unlabelled] = index
        attack_type[first_unlabelled] = attack.type
        is_attacked |= mask
    return InjectionResult(out, AttackTruth(is_attacked, attack_index, attack_type))


# --- ground-truth guard ---------------------------------------------------------------------------------------

FORBIDDEN_FIELD_FRAGMENTS = (
    "label", "ground_truth", "groundtruth", "is_attacked", "attack_schedule", "attack_index", "attack_type",
    "attacks", "spoof_schedule", "truth",
)


def forbidden_fields(names: Iterable[str]) -> list[str]:
    return sorted({name for name in names if any(fragment in str(name).lower() for fragment in FORBIDDEN_FIELD_FRAGMENTS)})


def assert_no_ground_truth(names: Iterable[str], where: str = "input") -> None:
    """Raise if any field name looks like a label, ground truth or the attack schedule."""
    bad = forbidden_fields(names)
    if bad:
        raise GroundTruthLeak(f"{where} contains ground-truth fields: {', '.join(bad)}")


def assert_no_ground_truth_deep(value: Any, where: str = "payload") -> None:
    """Recursively reject ground-truth keys in nested JSON-like payloads."""
    if isinstance(value, dict):
        assert_no_ground_truth(value.keys(), where)
        for key, item in value.items():
            assert_no_ground_truth_deep(item, f"{where}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            assert_no_ground_truth_deep(item, f"{where}[{index}]")


class GroundTruthLeak(ValueError):
    pass
