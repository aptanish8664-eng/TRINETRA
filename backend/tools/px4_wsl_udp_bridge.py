"""Forward only PX4 SITL's existing loopback GCS telemetry to TRINETRA on Windows.

This bridge does not send packets back to PX4 and does not alter PX4 configuration.
Run it inside the existing WSL Ubuntu distribution while the TRINETRA receiver is
enabled. The caller must supply the current Windows host/gateway address.
"""

from __future__ import annotations

import argparse
import logging
import socket
import time


LOGGER = logging.getLogger("trinetra.px4_wsl_udp_bridge")
MAVLINK_FRAME_STARTS = {0xFE, 0xFD}


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
            if time.monotonic() - last_report >= 10:
                LOGGER.info("Forwarded %s MAVLink datagrams; rejected %s", forwarded, rejected)
                last_report = time.monotonic()
    finally:
        receiver.close()
        sender.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    run(build_parser().parse_args())
