import struct
from types import SimpleNamespace

import pytest

from aeron_mvp.multi_source_mux import HEADER
from aeron_mvp.multi_source_mux import PACKET_SIZES
from aeron_mvp.multi_source_mux import parse_input_specs
from aeron_mvp.multi_source_mux import tag_packet
from aeron_mvp.multi_source_mux import InputSpec, InputState
from aeron_mvp.multi_source_mux import idle_timeout_message
from aeron_mvp import multi_source_mux as mux


@pytest.mark.parametrize("second_sequence,expected_sent", [(2, 2), (3, 1)])
def test_mux_independent_counts(monkeypatch, tmp_path, capsys, second_sequence, expected_sent):
    packets = iter([packet(3, 1, 1), packet(3, second_sequence, 1)])
    channel = SimpleNamespace(
        setblocking=lambda value: None, bind=lambda address: None,
        close=lambda: None, recvfrom=lambda size: (next(packets), None),
        sendto=lambda payload, address: len(payload),
    )
    registered = []
    selector = SimpleNamespace(
        register=lambda sock, event, state: registered.append(state),
        select=lambda timeout: [(SimpleNamespace(data=registered[0]), None)],
        close=lambda: None,
    )
    monkeypatch.setattr(mux.socket, "socket", lambda *args: channel)
    monkeypatch.setattr(mux.selectors, "DefaultSelector", lambda: selector)
    args = SimpleNamespace(input=["ctp-live=24001"], output_host="127.0.0.1",
        output_port=24002, bind_host="127.0.0.1", count_per_source=2,
        source_timeout_seconds=60, ready_file=tmp_path / "ready")
    if second_sequence == 3:
        with pytest.raises(RuntimeError, match="sequence discontinuity"):
            mux.run(args)
    else:
        assert mux.run(args) == 0
    assert registered[0].received_packets == 2
    assert registered[0].source_ticks == expected_sent
    assert f"udp_received_packets=2 udp_sent_packets={expected_sent}" in capsys.readouterr().out


def packet(version: int, sequence: int = 7, repeat: int = 100) -> bytes:
    payload = bytearray(PACKET_SIZES[version])
    HEADER.pack_into(payload, 0, 0x43545031, version, 1, repeat, sequence, 10, 20)
    return bytes(payload)


@pytest.mark.parametrize("version", [1, 2, 3])
def test_tag_packet_preserves_layout_and_adds_source(version):
    tagged, source_sequence, expanded = tag_packet(
        packet(version), "ydapi", global_sequence=3, remaining=25
    )

    magic, tagged_version, flags, repeat, sequence, market_ns, receive_ns = HEADER.unpack_from(tagged)
    assert magic == 0x43545031
    assert tagged_version == 4
    assert flags == 1
    assert repeat == 25
    assert sequence == 3
    assert source_sequence == 7
    assert expanded == 25
    assert market_ns == 10
    assert receive_ns == 20
    assert len(tagged) == PACKET_SIZES[version] + 32
    assert tagged[-32:].split(b"\0", 1)[0] == b"ydapi"


def test_input_list_accepts_one_or_more_sources():
    assert [item.source for item in parse_input_specs(["ydapi=24001"])] == ["ydapi"]
    assert [item.source for item in parse_input_specs(["ctp-live=24001", "ydapi=24002"])] == [
        "ctp-live",
        "ydapi",
    ]
    with pytest.raises(ValueError, match="at least one"):
        parse_input_specs([])


def test_input_list_requires_unique_sources_and_ports():
    assert [item.source for item in parse_input_specs(["ctp-live=24001", "ydapi=24002"])] == [
        "ctp-live",
        "ydapi",
    ]
    with pytest.raises(ValueError, match="duplicate source"):
        parse_input_specs(["ydapi=24001", "ydapi=24002"])
    with pytest.raises(ValueError, match="duplicate UDP port"):
        parse_input_specs(["ydapi=24001", "ctp=24001"])


def test_idle_diagnostic_reports_progress():
    state = InputState(InputSpec("ydapi-main", 24001), None, 99)
    state.source_ticks = 1
    state.published = 1

    assert idle_timeout_message(state, 60) == (
        "source ydapi-main idle for 60 seconds after "
        "source_ticks=1 published=1 remaining=99"
    )
