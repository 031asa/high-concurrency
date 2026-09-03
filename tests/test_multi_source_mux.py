import struct

import pytest

from aeron_mvp.multi_source_mux import HEADER
from aeron_mvp.multi_source_mux import PACKET_SIZES
from aeron_mvp.multi_source_mux import parse_input_specs
from aeron_mvp.multi_source_mux import tag_packet


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


def test_input_list_requires_unique_sources_and_ports():
    assert [item.source for item in parse_input_specs(["ctp-live=24001", "ydapi=24002"])] == [
        "ctp-live",
        "ydapi",
    ]
    with pytest.raises(ValueError, match="duplicate source"):
        parse_input_specs(["ydapi=24001", "ydapi=24002"])
    with pytest.raises(ValueError, match="duplicate UDP port"):
        parse_input_specs(["ydapi=24001", "ctp=24001"])
    with pytest.raises(ValueError, match="at least two"):
        parse_input_specs(["ydapi=24001"])
