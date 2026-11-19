"""Capacity guard: would this computer cope with the simulated aircraft that are about to be started?

Planning any number of aircraft is always allowed; only STARTING simulated aircraft is limited. The guard compares the request with (1) the measured safe maximum for this
machine (config.SIM_MAX_AIRCRAFT, 6 on the reference laptop: docs/FLEET_CAPACITY.md) and (2) the memory Windows still has free, because on a 15.4 GB laptop running a browser the
host runs out of memory before the simulator runs out of CPU (measured: Windows free memory fell to 119-346 MiB with Mission Control open and 5 aircraft, API calls then took
tens of seconds). It only advises; the caller decides.
"""
from __future__ import annotations

import os
from typing import Any

from . import config

# Estimated memory of the simulation in WSL (measured: the WSL VM peaked at 1.5-2.7 GB with 3-5 aircraft) and what Windows itself and the browser need left over.
BASE_MB = config.env_int("TRINETRA_SIM_MEMORY_BASE_MB", 900, 100, 20000)
PER_AIRCRAFT_MB = config.env_int("TRINETRA_SIM_MEMORY_PER_AIRCRAFT_MB", 250, 50, 5000)
HEADROOM_MB = config.env_int("TRINETRA_SIM_MEMORY_HEADROOM_MB", 1500, 0, 20000)


def host_memory_mb() -> tuple[int, int] | None:
    """(total, available) physical memory of this computer in MiB, or None when it cannot be read."""
    if os.name != "nt":
        try:
            values = {}
            for line in open("/proc/meminfo", encoding="utf-8"):
                key, _, rest = line.partition(":")
                values[key] = int(rest.split()[0])
            return values["MemTotal"] // 1024, values["MemAvailable"] // 1024
        except (OSError, KeyError, ValueError, IndexError):
            return None
    import ctypes

    class Status(ctypes.Structure):
        _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong), ("total", ctypes.c_ulonglong), ("avail", ctypes.c_ulonglong), ("pt", ctypes.c_ulonglong),
                    ("pa", ctypes.c_ulonglong), ("vt", ctypes.c_ulonglong), ("va", ctypes.c_ulonglong), ("ext", ctypes.c_ulonglong)]
    status = Status()
    status.length = ctypes.sizeof(Status)
    try:
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return None
    except (AttributeError, OSError):
        return None
    return int(status.total // 2**20), int(status.avail // 2**20)


def assess(requested: int, *, running: int = 0, memory: tuple[int, int] | None = None) -> dict[str, Any]:
    """`level`: ok | warn | overload. `reasons` are plain sentences; `advice` says what to do. `running` aircraft already use memory, so they are not counted again."""
    memory = host_memory_mb() if memory is None else memory
    safe = config.SIM_MAX_AIRCRAFT
    new = max(0, requested - running)
    needed = (BASE_MB if new else 0) + PER_AIRCRAFT_MB * new
    reasons: list[str] = []
    level = "ok"
    if requested > safe:
        level = "overload"
        reasons.append(f"{requested} simulated aircraft were requested; this computer was measured to run {safe} reliably.")
    free = None if memory is None else memory[1]
    if free is not None and new:
        if free < needed:
            level = "overload"
            reasons.append(f"Windows has only {free} MiB of memory free; starting {new} more aircraft needs about {needed} MiB.")
        elif free < needed + HEADROOM_MB:
            level = "warn" if level == "ok" else level
            reasons.append(f"Windows has {free} MiB free; the simulation needs about {needed} MiB and Windows and the browser need about {HEADROOM_MB} MiB more, so the computer may become slow.")
    advice = ("Close other programs (browsers use the most memory) or start fewer aircraft. A .wslconfig sized to this machine is printed by backend/tools/trinetra_doctor.py. "
              "The mission plan itself can have any number of aircraft.") if level != "ok" else ""
    return {"level": level, "requested": requested, "running": running, "safe_maximum": safe, "host_free_mb": free, "host_total_mb": None if memory is None else memory[0],
            "estimated_needed_mb": needed, "headroom_mb": HEADROOM_MB, "reasons": reasons, "advice": advice}
