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
MP_MAX_WAYPOINTS = env_int("TRINETRA_MP_MAX_WAYPOINTS", 200, 10, 2000)
MP_MAX_START_TO_AREA_M = env_int("TRINETRA_MP_MAX_START_TO_AREA_M", 20000, 100, 200000)
MP_MIN_SPEED_MPS = env_int("TRINETRA_MP_MIN_SPEED_MPS", 1, 1, 50)
MP_MAX_SPEED_MPS = env_int("TRINETRA_MP_MAX_SPEED_MPS", 20, 1, 50)
MP_MIN_ALTITUDE_M = env_int("TRINETRA_MP_MIN_ALTITUDE_M", 2, 1, 100)
MP_MAX_ALTITUDE_M = env_int("TRINETRA_MP_MAX_ALTITUDE_M", 120, 5, 500)
MP_MIN_HOVER_S = env_int("TRINETRA_MP_MIN_HOVER_S", 1, 0, 600)
MP_MAX_HOVER_S = env_int("TRINETRA_MP_MAX_HOVER_S", 600, 1, 3600)
# Simulated X500 endurance used by every route check (generation preview, save, review, deploy). It is a planning
# limit for the software simulation, not a measured battery model: one flight may take at most this long and cover
# at most this distance, whether or not the plan sets its own (stricter) max_duration_minutes.
MP_MAX_FLIGHT_MINUTES = env_int("TRINETRA_MP_MAX_FLIGHT_MINUTES", 20, 1, 240)
MP_MAX_FLIGHT_DISTANCE_M = env_int("TRINETRA_MP_MAX_FLIGHT_DISTANCE_M", 15000, 100, 500000)
