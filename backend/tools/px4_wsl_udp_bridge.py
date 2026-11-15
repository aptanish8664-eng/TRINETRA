"""Forward only PX4 SITL's existing loopback GCS telemetry to TRINETRA on Windows.

The only packets it sends back to PX4 are fixed MAV_CMD_SET_MESSAGE_INTERVAL requests (the STREAM_REQUESTS table below) so
that this same telemetry link carries the messages the threat detector needs. They leave from the socket PX4 already sends
to, so PX4's link is not re-pointed; nothing else is ever sent, no parameter is changed and nothing persists in PX4.
Run it inside the existing WSL Ubuntu distribution while the TRINETRA receiver is
enabled. The caller must supply the current Windows host/gateway address.
"""

from __future__ import annotations

import argparse
import logging
import socket
import struct
import time


LOGGER = logging.getLogger("trinetra.px4_wsl_udp_bridge")
MAVLINK_FRAME_STARTS = {0xFE, 0xFD}


# message id -> requested rate (Hz) on the telemetry link. Fixed; not configurable from the API.
STREAM_REQUESTS = {105: 25.0,   # HIGHRES_IMU
                   24: 10.0,    # GPS_RAW_INT (position, speed, course)
                   32: 25.0,    # LOCAL_POSITION_NED (estimator velocity)
                   230: 2.0,    # ESTIMATOR_STATUS
                   241: 1.0}    # VIBRATION
STREAM_REQUEST_PERIOD_S = 20.0   # repeated so a restarted PX4 instance gets the rates again
_COMMAND_LONG, _COMMAND_LONG_CRC_EXTRA, _SET_MESSAGE_INTERVAL = 76, 152, 511
_REQUESTER = (255, 191)          # sender system/component id of these requests


def _x25(data: bytes) -> int:
    crc = 0xFFFF
    for byte in data:
        tmp = byte ^ (crc & 0xFF)
        tmp = (tmp ^ (tmp << 4)) & 0xFF
        crc = ((crc >> 8) ^ (tmp << 8) ^ (tmp << 3) ^ (tmp >> 4)) & 0xFFFF
    return crc


def message_interval_frame(sequence: int, target_system: int, message_id: int, rate_hz: float) -> bytes:
    """One MAVLink 2 COMMAND_LONG(MAV_CMD_SET_MESSAGE_INTERVAL) frame for an allowlisted message."""
    if message_id not in STREAM_REQUESTS or not 0.1 <= rate_hz <= 100.0 or not 1 <= target_system <= 255:
        raise ValueError("stream request is not in the fixed table")
    payload = struct.pack("<7fHBBB", float(message_id), 1e6 / rate_hz, 0.0, 0.0, 0.0, 0.0, 0.0, _SET_MESSAGE_INTERVAL, target_system, 1, 0)
    payload = payload.rstrip(b"\0") or b"\0"
    body = bytes((len(payload), 0, 0, sequence & 0xFF, _REQUESTER[0], _REQUESTER[1])) + _COMMAND_LONG.to_bytes(3, "little") + payload
    return bytes((0xFD,)) + body + struct.pack("<H", _x25(body + bytes((_COMMAND_LONG_CRC_EXTRA,))))


def frame_system_id(payload: bytes) -> int | None:
    if len(payload) >= 10 and payload[0] == 0xFD:
        return payload[5]
    if len(payload) >= 6 and payload[0] == 0xFE:
        return payload[3]
    return None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--windows-host", required=True, help="Current Windows host IP reachable from WSL")
    parser.add_argument("--receiver-port", type=int, default=14550, help="PX4's existing local GCS destination port")
    parser.add_argument("--forward-port", type=udp_port, default=14600, help="Primary TRINETRA backend UDP receiver port")
    parser.add_argument(
        "--additional-forward-port",
        type=udp_port,
        action="append",
        dest="additional_forward_ports",
        help="Optional second backend UDP receiver port; repeat to mirror one-way telemetry",
    )
    parser.add_argument("--source-port", type=int, default=14601, help="Fixed WSL UDP source port checked by the backend")
    parser.add_argument(
        "--px4-source-port",
        type=udp_port,
        action="append",
        dest="px4_source_ports",
        help="Expected PX4 local GCS source port; repeat for additional SITL instances (defaults to 18570-18574)",
    )
    parser.add_argument("--no-stream-requests", action="store_true", help="Do not ask PX4 for the detector's message rates")
    return parser


def udp_port(value: str) -> int:
    try:
        port = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("port must be an integer") from exc
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError("port must be between 1 and 65535")
    return port


def px4_source_ports(configured: list[int] | None) -> frozenset[int]:
    ports = configured if configured else list(range(18570, 18575))
    if not ports or any(not 1 <= port <= 65535 for port in ports):
        raise ValueError("At least one valid PX4 UDP source port is required")
    return frozenset(ports)


def forward_targets(
    windows_host: str,
    primary_port: int,
    additional_ports: list[int] | None = None,
) -> tuple[tuple[str, int], ...]:
    """Return stable, deduplicated Windows UDP targets for a received frame."""
    ports = dict.fromkeys([primary_port, *(additional_ports or [])])
    return tuple((windows_host, port) for port in ports)


def run(args: argparse.Namespace) -> None:
    receiver = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    receiver.bind(("127.0.0.1", args.receiver_port))
    sender.bind(("0.0.0.0", args.source_port))
    receiver.settimeout(0.5)
    targets = forward_targets(args.windows_host, args.forward_port, args.additional_forward_ports)
    expected_px4_ports = px4_source_ports(args.px4_source_ports)
    forwarded = 0
    rejected = 0
    last_report = time.monotonic()
    requested_at: dict[tuple[str, int], float] = {}
    sequence = 0
    LOGGER.info(
        "Listening on 127.0.0.1:%s; accepting PX4 source ports %s; forwarding to %s from UDP source port %s",
        args.receiver_port, ",".join(map(str, sorted(expected_px4_ports))),
        ",".join(str(port) for _host, port in targets), args.source_port,
    )

    try:
        while True:
            try:
                payload, peer = receiver.recvfrom(65535)
            except socket.timeout:
                payload = b""
                peer = None
            if payload:
                if peer[0] != "127.0.0.1" or peer[1] not in expected_px4_ports or payload[0] not in MAVLINK_FRAME_STARTS:
                    rejected += 1
                else:
                    for target in targets:
                        sender.sendto(payload, target)
                    forwarded += 1
                    system_id = frame_system_id(payload)
                    if (not args.no_stream_requests and system_id and
                            time.monotonic() - requested_at.get(peer, -1e9) >= STREAM_REQUEST_PERIOD_S):
                        requested_at[peer] = time.monotonic()
                        for message_id, rate_hz in STREAM_REQUESTS.items():   # replies go out of the socket PX4 already targets
                            receiver.sendto(message_interval_frame(sequence, system_id, message_id, rate_hz), peer)
                            sequence += 1
            if time.monotonic() - last_report >= 10:
                LOGGER.info("Forwarded %s MAVLink datagrams; rejected %s", forwarded, rejected)
                last_report = time.monotonic()
    finally:
        receiver.close()
        sender.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    run(build_parser().parse_args())
