import importlib
import struct
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MVP_DIR = PROJECT_ROOT / "aeron_mvp"
sys.path.insert(0, str(MVP_DIR))
market_wire = importlib.import_module("market_wire")
zmq_market_probe = importlib.import_module("zmq_market_probe")


def _var_string(value):
    encoded = value.encode("utf-8")
    return struct.pack("<H", len(encoded)) + encoded


def market_quote_frame(sequence=7):
    block = bytearray(market_wire.V3_BLOCK_LENGTH)
    struct.pack_into("<Q", block, 0, sequence)
    struct.pack_into("<Q", block, 8, 1_000_000_000)
    struct.pack_into("<Q", block, 16, 1_012_000_000)
    struct.pack_into("<d", block, 24, 5_000.1)
    struct.pack_into("<d", block, 32, 5_000.0)
    struct.pack_into("<d", block, 40, 5_000.2)
    struct.pack_into("<q", block, 48, 10)
    struct.pack_into("<q", block, 56, 11)
    struct.pack_into("<B", block, 64, 1)
    struct.pack_into("<B", block, 65, 5)
    for level in range(2, 6):
        offset = 66 + (level - 2) * 32
        struct.pack_into("<d", block, offset, 5_000.0 - level * 0.2)
        struct.pack_into("<d", block, offset + 8, 5_000.2 + level * 0.2)
        struct.pack_into("<q", block, offset + 16, level * 10)
        struct.pack_into("<q", block, offset + 24, level * 10 + 1)
    struct.pack_into("<q", block, 194, 1234)
    for index, value in enumerate(
        (
            5_000_100.25,
            98_765.5,
            4_980.0,
            4_990.0,
            98_000.0,
            4_995.0,
            5_010.0,
            4_985.0,
            5_000.1,
            5_001.0,
            5_500.0,
            4_500.0,
            0.1,
            0.2,
            4_999.5,
        )
    ):
        struct.pack_into("<d", block, 202 + index * 8, value)
    struct.pack_into("<H", block, 322, 123)
    header = struct.pack(
        "<HHHH",
        len(block),
        market_wire.TEMPLATE_ID,
        market_wire.SCHEMA_ID,
        3,
    )
    strings = b"".join(
        _var_string(value)
        for value in (
            "session-1",
            "IC2609",
            "20260827",
            "20260827 14:00:00.123",
            "CFFEX",
            "20260827",
            "14:00:00",
            "ctp-live",
        )
    )
    return header + block + strings


def test_decode_sbe_v3_to_complete_tick():
    tick = market_wire.decode_market_quote(market_quote_frame())

    assert tick["type"] == "snapshot"
    assert tick["Contract"] == "IC2609"
    assert tick["InstrumentID"] == "IC2609"
    assert tick["ExchangeID"] == "CFFEX"
    assert tick["AeronSessionID"] == "session-1"
    assert tick["AeronSequence"] == 7
    assert tick["SchemaVersion"] == 3
    assert tick["MarketSource"] == "ctp-live"
    assert tick["LocalReceiveNs"] - tick["MarketTimestampNs"] == 12_000_000
    assert tick["Volume"] == 1234
    assert tick["OpenInterest"] == 98_765.5
    assert tick["UpdateMillisec"] == 123
    assert tick["BidPrice5"] == 4_999.0
    assert tick["AskVolume5"] == 51


def test_probe_summary_keeps_complete_first_and_last_ticks():
    first = market_wire.decode_market_quote(market_quote_frame(1))
    last = market_wire.decode_market_quote(market_quote_frame(10))

    summary = zmq_market_probe.build_summary(first, last, 10)

    assert summary["count"] == 10
    assert summary["first_tick"] == first
    assert summary["last_tick"] == last
    assert summary["last_tick"]["LastPrice"] == 5_000.1
    assert summary["last_tick"]["Volume"] == 1234
    assert summary["last_tick"]["BidPrice5"] == 4_999.0
    assert summary["last_tick"]["AskVolume5"] == 51


def test_wire_tools_have_no_downstream_project_dependency():
    decoder = (MVP_DIR / "market_wire.py").read_text(encoding="utf-8")
    probe = (MVP_DIR / "zmq_market_probe.py").read_text(encoding="utf-8")

    combined = decoder + probe
    assert "hpquant" not in combined
    assert "sitecustomize" not in combined
    assert "leader_integration" not in combined
