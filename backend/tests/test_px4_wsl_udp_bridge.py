import pytest

from tools import px4_wsl_udp_bridge as bridge
from tools.px4_wsl_udp_bridge import build_parser, forward_targets, px4_source_ports, udp_port


def test_relay_accepts_multiple_configured_px4_instances_and_keeps_safe_default():
    assert px4_source_ports(None) == frozenset({18570, 18571, 18572, 18573, 18574})
    assert px4_source_ports([18570, 18572]) == frozenset({18570, 18572})
    assert build_parser().parse_args(["--windows-host", "172.23.80.1"]).px4_source_ports is None


def test_relay_preserves_primary_target_and_deduplicates_optional_mirror_targets():
    args = build_parser().parse_args([
        "--windows-host", "172.23.80.1",
        "--forward-port", "14600",
        "--additional-forward-port", "14620",
        "--additional-forward-port", "14600",
    ])

    assert forward_targets(args.windows_host, args.forward_port, args.additional_forward_ports) == (
        ("172.23.80.1", 14600),
        ("172.23.80.1", 14620),
    )


def test_relay_sends_each_accepted_datagram_to_primary_and_mirror(monkeypatch):
    class Receiver:
        def __init__(self):
            self.reads = 0
            self.closed = False

        def bind(self, _address):
            pass

        def settimeout(self, _seconds):
            pass

        def recvfrom(self, _size):
            self.reads += 1
            if self.reads == 1:
                return b"\xfd\x01", ("127.0.0.1", 18570)
            raise KeyboardInterrupt

        def close(self):
            self.closed = True

    class Sender:
        def __init__(self):
            self.sent = []
            self.closed = False

        def bind(self, _address):
            pass

        def sendto(self, payload, target):
            self.sent.append((payload, target))

        def close(self):
            self.closed = True

    receiver = Receiver()
    sender = Sender()
    sockets = iter((receiver, sender))
    monkeypatch.setattr(bridge.socket, "socket", lambda *_args: next(sockets))
    args = build_parser().parse_args([
        "--windows-host", "172.23.80.1",
        "--forward-port", "14600",
        "--additional-forward-port", "14620",
    ])

    with pytest.raises(KeyboardInterrupt):
        bridge.run(args)

    assert sender.sent == [
        (b"\xfd\x01", ("172.23.80.1", 14600)),
        (b"\xfd\x01", ("172.23.80.1", 14620)),
    ]
    assert receiver.closed is True
    assert sender.closed is True


@pytest.mark.parametrize("value", ["0", "65536", "abc"])
def test_relay_rejects_invalid_px4_udp_ports(value):
    with pytest.raises(Exception):
        udp_port(value)

