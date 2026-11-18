from __future__ import annotations

import os
from pathlib import Path

from dotenv import dotenv_values, load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[2]
_PROCESS_ENVIRONMENT_KEYS = set(os.environ)
load_dotenv(PROJECT_ROOT / ".env", override=False)


def _load_backend_ai_environment(env_path: Path, protected_keys: set[str]) -> None:
    for name, value in dotenv_values(env_path).items():
        if (
            name in {"OPENAI_API_KEY", "OPENAI_MODEL"}
            and name not in protected_keys
            and isinstance(value, str)
            and value.strip()
        ):
            os.environ[name] = value.strip()


_load_backend_ai_environment(PROJECT_ROOT / "backend" / ".env", _PROCESS_ENVIRONMENT_KEYS)


def env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except ValueError:
        return default
    return min(max(value, minimum), maximum)


SESSION_COOKIE_NAME = "trinetra_session"
SESSION_TTL_SECONDS = env_int("TRINETRA_SESSION_TTL_SECONDS", 8 * 60 * 60, 60, 7 * 24 * 60 * 60)
# Local development typically runs over plain HTTP on loopback addresses, so the
# default is insecure-only to keep the browser session usable. HTTPS deployments
# can explicitly set this to true via environment configuration.
SESSION_COOKIE_SECURE = env_bool("TRINETRA_SESSION_COOKIE_SECURE", False)
LOGIN_FAILURE_LIMIT = env_int("TRINETRA_LOGIN_FAILURE_LIMIT", 5, 1, 100)
LOGIN_WINDOW_SECONDS = env_int("TRINETRA_LOGIN_WINDOW_SECONDS", 300, 10, 3600)
_origin_values = os.environ.get(
    "TRINETRA_ALLOWED_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173"
)
ALLOWED_ORIGINS = tuple(origin.strip().rstrip("/") for origin in _origin_values.split(",") if origin.strip())

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "").strip()
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4.1-mini").strip() or "gpt-4.1-mini"
DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
TRINETRA_ML_MODEL_PATH = os.environ.get("TRINETRA_ML_MODEL_PATH", "").strip()
AI_TIMEOUT_SECONDS = env_int("TRINETRA_AI_TIMEOUT_SECONDS", 20, 3, 90)
AI_MAX_INPUT_CHARS = env_int("TRINETRA_AI_MAX_INPUT_CHARS", 6000, 200, 20000)
AI_MAX_OUTPUT_TOKENS = env_int("TRINETRA_AI_MAX_OUTPUT_TOKENS", 1800, 128, 6000)
AI_REQUESTS_PER_HOUR = env_int("TRINETRA_AI_REQUESTS_PER_HOUR", 20, 1, 500)
AI_DAILY_OUTPUT_TOKEN_LIMIT = env_int("TRINETRA_AI_DAILY_OUTPUT_TOKEN_LIMIT", 20000, 128, 1000000)
MAX_HTTP_BODY_BYTES = env_int("TRINETRA_MAX_HTTP_BODY_BYTES", 65536, 1024, 1000000)
# Scenario saves and route previews carry every generated route of every aircraft, so they get their own, larger body limit
# (a 100-aircraft plan is about 1 MB). Every other request keeps the small limit above.
PLAN_MAX_HTTP_BODY_BYTES = env_int("TRINETRA_PLAN_MAX_HTTP_BODY_BYTES", 16_000_000, 65536, 64_000_000)
# SIMULATION CAPACITY: how many PX4 SITL + Gazebo aircraft this machine runs reliably. It limits only ACTIVATING and STARTING simulated
# aircraft. Planning and saving a mission with more aircraft is always allowed (the mission is then a plan only until enough aircraft run).
# The value is set from measurements on this machine (docs/FLEET_CAPACITY.md); do not raise it on assumption.
# MEASURED on this machine (8 logical CPUs, 7.6 GB WSL memory, WSL on D:): 6 aircraft started 3 of 3 times at about 48 s with Gazebo real-time factor 0.99; 7 aircraft only 1 of 3
# times (real-time factor 0.84-0.97). docs/FLEET_CAPACITY.md has the table. Do not raise it on assumption.
SIM_MAX_AIRCRAFT_DEFAULT = 6
SIM_MAX_AIRCRAFT = env_int("TRINETRA_SIM_MAX_AIRCRAFT", SIM_MAX_AIRCRAFT_DEFAULT, 1, 64)
# PX4 instances the staged start brings up together (each batch is waited for until its aircraft are ready). Measured in docs/FLEET_CAPACITY.md.
FLEET_START_BATCH = env_int("TRINETRA_FLEET_START_BATCH", 2, 1, 16)
# Threat monitor: the server refuses a handover to an aircraft below this battery level; live assessments are computed at most this often.
MIN_HANDOVER_BATTERY_PCT = env_int("TRINETRA_MIN_HANDOVER_BATTERY_PCT", 40, 0, 100)
LIVE_ASSESSMENT_INTERVAL_S = 1.0
PX4_UDP_ENABLED = env_bool("TRINETRA_PX4_UDP_ENABLED")
PX4_UDP_BIND_HOST = os.environ.get("TRINETRA_PX4_UDP_BIND_HOST", "127.0.0.1").strip() or "127.0.0.1"
PX4_UDP_BIND_PORT = env_int("TRINETRA_PX4_UDP_BIND_PORT", 14600, 1024, 65535)
PX4_UDP_ALLOWED_SOURCE_IP = os.environ.get("TRINETRA_PX4_UDP_ALLOWED_SOURCE_IP", "").strip()
PX4_UDP_ALLOWED_SOURCE_PORT = env_int("TRINETRA_PX4_UDP_ALLOWED_SOURCE_PORT", 14601, 1, 65535)
AUTO_START_DEFAULT_SITL = env_bool("TRINETRA_AUTO_START_DEFAULT_SITL", True)
GAZEBO_POSE_UDP_ENABLED = env_bool("TRINETRA_GAZEBO_POSE_UDP_ENABLED")
GAZEBO_POSE_UDP_BIND_HOST = os.environ.get("TRINETRA_GAZEBO_POSE_UDP_BIND_HOST", "127.0.0.1").strip() or "127.0.0.1"
GAZEBO_POSE_UDP_BIND_PORT = env_int("TRINETRA_GAZEBO_POSE_UDP_BIND_PORT", 14610, 1024, 65535)
GAZEBO_POSE_UDP_ALLOWED_SOURCE_IP = os.environ.get("TRINETRA_GAZEBO_POSE_UDP_ALLOWED_SOURCE_IP", "").strip()
GAZEBO_POSE_UDP_ALLOWED_SOURCE_PORT = env_int("TRINETRA_GAZEBO_POSE_UDP_ALLOWED_SOURCE_PORT", 14611, 1, 65535)

# Mission route planning limits (software-only SITL planning; enforced by the backend validator).
# Defaults (2026-11-14) allow long-distance simulated flights such as Bengaluru to Chennai (about 290 km, 580 km out
# and back). Every value below is served by GET /api/mission-plans/limits and the UI reads it from there.
# PX4 SITL stores up to CONFIG_NUM_MISSION_ITEMS_SUPPORTED=10000 mission items in this checkout (boards/px4/sitl/default.px4board).
MP_MAX_WAYPOINTS = env_int("TRINETRA_MP_MAX_WAYPOINTS", 500, 10, 5000)
MP_MAX_START_TO_AREA_M = env_int("TRINETRA_MP_MAX_START_TO_AREA_M", 1_000_000, 100, 20_000_000)
# Area generators (coverage, expanding square, sector search, perimeter patrol) work on a local flat plane that is only
# valid for a few km; an area further than this from the start, or wider than this, is rejected instead of drawn wrong.
MP_MAX_PLANAR_EXTENT_M = env_int("TRINETRA_MP_MAX_PLANAR_EXTENT_M", 20_000, 1000, 100_000)
# Point-to-point routes whose farthest point from the start is beyond this use geodesic (WGS84) lengths and the points' own coordinates.
MP_GEODESIC_ABOVE_M = env_int("TRINETRA_MP_GEODESIC_ABOVE_M", 20_000, 1000, 100_000)

# Long-range simulation profile: PX4 parameters applied to every SITL instance the fleet manager STARTS (never to a real
# vehicle) so the simulated X500 can actually fly the planned speeds and distances. Off restores PX4's own defaults.
LONG_RANGE_PROFILE_ENABLED = env_bool("TRINETRA_LONG_RANGE_PROFILE", True)
LONG_RANGE_ACCEL_MPS2 = env_int("TRINETRA_LONG_RANGE_ACCEL_MPS2", 10, 1, 30)
LONG_RANGE_TILT_DEG = env_int("TRINETRA_LONG_RANGE_TILT_DEG", 60, 20, 85)
# Gazebo's stock `default` world has no ground under an aircraft flown a few km from the origin (measured: it falls through and never
# lands; the ground plane's declared size is not honoured). The profile runs Gazebo with a runtime copy of the world (the PX4 tree is
# not edited) whose ground is a static box this many metres wide: +-1,050 km, enough for the 1,000 km route limit.
LONG_RANGE_GROUND_EXTENT_M = env_int("TRINETRA_LONG_RANGE_GROUND_EXTENT_M", 2_100_000, 1000, 20_000_000)
MP_MIN_SPEED_MPS = env_int("TRINETRA_MP_MIN_SPEED_MPS", 1, 1, 150)
MP_MAX_SPEED_MPS = env_int("TRINETRA_MP_MAX_SPEED_MPS", 90, 1, 150)  # measured ceiling ~100 m/s on the simulated X500 (tilt cap 60 deg), minus ~10%
# Quick mission defaults (mission_planning/quick.py). 8 m/s: the live threat-detector baseline is validated only at that speed.
# The long-range profile's waypoint acceptance radius (PX4 switches to the next leg inside it): the tightest corner radius the speed rule assumes.
LONG_RANGE_ACCEPTANCE_M = env_int("TRINETRA_LONG_RANGE_ACCEPTANCE_M", 25, 1, 500)
QUICK_SPEED_MPS = env_int("TRINETRA_QUICK_SPEED_MPS", 8, 1, 150)
QUICK_ALTITUDE_M = env_int("TRINETRA_QUICK_ALTITUDE_M", 50, 1, 500)
MP_MIN_ALTITUDE_M = env_int("TRINETRA_MP_MIN_ALTITUDE_M", 2, 1, 100)
MP_MAX_ALTITUDE_M = env_int("TRINETRA_MP_MAX_ALTITUDE_M", 120, 5, 500)
MP_MIN_HOVER_S = env_int("TRINETRA_MP_MIN_HOVER_S", 1, 0, 600)
MP_MAX_HOVER_S = env_int("TRINETRA_MP_MAX_HOVER_S", 600, 1, 3600)
# Simulated X500 endurance used by every route check (generation preview, save, review, deploy). It is a planning
# limit for the software simulation, not a measured battery model: one flight may take at most this long and cover
# at most this distance, whether or not the plan sets its own (stricter) max_duration_minutes.
MP_MAX_FLIGHT_MINUTES = env_int("TRINETRA_MP_MAX_FLIGHT_MINUTES", 300, 1, 1440)
MP_MAX_FLIGHT_DISTANCE_M = env_int("TRINETRA_MP_MAX_FLIGHT_DISTANCE_M", 1_000_000, 100, 20_000_000)
