"""Forward read-only Gazebo pose snapshots from WSL to TRINETRA over UDP.

The bridge subscribes only to Gazebo's ``/world/<name>/pose/info`` topic. It
does not publish to Gazebo, PX4, or MAVLink and cannot control a vehicle.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import logging
import math
import re
import socket
import subprocess
import time
from collections.abc import Iterable, Iterator


LOGGER = logging.getLogger("trinetra.gazebo_wsl_pose_bridge")
_MODEL_NAME = re.compile(r"^x500_[0-9]{1,3}$")
_FIELD = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(-?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--windows-host", required=True, help="Current Windows WSL gateway address")
    parser.add_argument("--world", default="default", help="Gazebo world name")
    parser.add_argument("--forward-port", type=udp_port, default=14610, help="TRINETRA Gazebo UDP receiver port")
    parser.add_argument("--source-port", type=udp_port, default=14611, help="Fixed WSL UDP source port checked by the backend")
    parser.add_argument("--rate", type=rate_limit, default=10.0, help="Maximum forwarded snapshots per second")
    return parser


def udp_port(value: str) -> int:
    try:
        port = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("port must be an integer") from exc
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError("port must be between 1 and 65535")
    return port


def rate_limit(value: str) -> float:
    try:
        rate = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("rate must be a number") from exc
    if not math.isfinite(rate) or not 1 <= rate <= 30:
        raise argparse.ArgumentTypeError("rate must be between 1 and 30 snapshots per second")
    return rate


def iter_pose_messages(lines: Iterable[str]) -> Iterator[str]:
    """Frame text-protobuf messages emitted by ``gz topic -e``."""
    message: list[str] = []
    for line in lines:
        if re.match(r"^\s*header\s*\{", line):
            if message:
                yield "".join(message)
            message = [line]
        elif message:
            message.append(line)
    if message:
        yield "".join(message)


def _block_after(text: str, field_name: str, start: int = 0) -> tuple[str, int] | None:
    match = re.search(rf"\b{re.escape(field_name)}\s*\{{", text[start:])
    if not match:
        return None
    opening = start + match.end() - 1
    depth = 0
    quoted = False
    escaped = False
    for index in range(opening, len(text)):
        char = text[index]
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
            continue
        if char == '"':
            quoted = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[opening + 1:index], index + 1
    return None


def _scalar_fields(text: str) -> dict[str, float]:
    values = {name: float(value) for name, value in _FIELD.findall(text)}
    return {name: value for name, value in values.items() if math.isfinite(value)}


def parse_pose_message(message: str, *, world: str) -> dict | None:
    models = []
    cursor = 0
    while (pose := _block_after(message, "pose", cursor)) is not None:
        content, cursor = pose
        name_match = re.search(r'\bname\s*:\s*"([^"\\]{1,64})"', content)
        if not name_match or not _MODEL_NAME.fullmatch(name_match.group(1)):
            continue
        position_block = _block_after(content, "position")
        orientation_block = _block_after(content, "orientation")
        if position_block is None or orientation_block is None:
            continue
        position = _scalar_fields(position_block[0])
        orientation = _scalar_fields(orientation_block[0])
        xyz = {axis: position.get(axis, 0.0) for axis in ("x", "y", "z")}
        xyzw = {axis: orientation.get(axis, 0.0) for axis in ("x", "y", "z")}
        xyzw["w"] = orientation.get("w", 1.0)
        if any(abs(value) > 100_000 for value in xyz.values()):
            continue
        norm = math.sqrt(sum(value * value for value in xyzw.values()))
        if not math.isfinite(norm) or not 0.5 <= norm <= 1.5:
            continue
        models.append({
            "model_name": name_match.group(1),
            "position_m": xyz,
            "orientation_quaternion": {axis: value / norm for axis, value in xyzw.items()},
        })
    if not models:
        return None

    sim_time_seconds = None
    header = _block_after(message, "header")
    stamp = _block_after(header[0], "stamp") if header else None
    if stamp:
        timestamp = _scalar_fields(stamp[0])
        sec = timestamp.get("sec")
        nsec = timestamp.get("nsec", 0.0)
        if sec is not None and nsec is not None:
            sim_time_seconds = sec + nsec / 1_000_000_000
    return {
        "source": "gazebo_pose_info",
        "world": world,
        "sim_time_seconds": sim_time_seconds,
        "models": models,
    }


def run(args: argparse.Namespace) -> None:
    host = str(ipaddress.IPv4Address(args.windows_host))
    world = re.fullmatch(r"[A-Za-z0-9_-]{1,64}", args.world)
    if not world:
        raise ValueError("Gazebo world name contains invalid characters")
    topic = f"/world/{args.world}/pose/info"
    target = (host, args.forward_port)
    sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sender.bind(("0.0.0.0", args.source_port))
    minimum_interval = 1.0 / args.rate
    last_sent = 0.0
    forwarded = 0
    last_report = time.monotonic()

    while True:
        LOGGER.info("Subscribing read-only to Gazebo topic %s", topic)
        process = subprocess.Popen(
            ["gz", "topic", "-e", "-t", topic],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        try:
            assert process.stdout is not None
            for message in iter_pose_messages(process.stdout):
                now = time.monotonic()
                if now - last_sent < minimum_interval:
                    continue
                snapshot = parse_pose_message(message, world=args.world)
                if snapshot is None:
                    continue
                payload = json.dumps(snapshot, separators=(",", ":"), allow_nan=False).encode("utf-8")
                sender.sendto(payload, target)
                forwarded += 1
                last_sent = now
                if now - last_report >= 10:
                    LOGGER.info("Forwarded %s Gazebo pose snapshots", forwarded)
                    last_report = now
        except KeyboardInterrupt:
            process.terminate()
            process.wait(timeout=5)
            return
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
        stderr = process.stderr.read() if process.stderr else ""
        if stderr.strip():
            LOGGER.warning("Gazebo topic subscriber exited (%s)", stderr.strip()[:400])
        time.sleep(2)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    run(build_parser().parse_args())
