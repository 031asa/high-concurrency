import importlib
import queue
import struct
import sys
from pathlib import Path
from types import ModuleType

import pytest
import zmq


PROJECT_ROOT = Path(__file__).resolve().parents[1]
INTEGRATION_DIR = PROJECT_ROOT / "aeron_mvp" / "leader_integration"


stub_hpquant = ModuleType("hpquant")
stub_message = ModuleType("hpquant.message")
stub_bus = ModuleType("hpquant.message.zmq_bus")


class _StubZmqSubscriber:
    pass


stub_bus.ZmqSubscriber = _StubZmqSubscriber
stub_message.zmq_bus = stub_bus
stub_hpquant.message = stub_message
sys.modules.setdefault("hpquant", stub_hpquant)
sys.modules.setdefault("hpquant.message", stub_message)
sys.modules.setdefault("hpquant.message.zmq_bus", stub_bus)
sys.path.insert(0, str(INTEGRATION_DIR))
hpquant_aeron_source = importlib.import_module("hpquant_aeron_source")


def _var_string(value):
    encoded = value.encode("utf-8")
    return struct.pack("<H", len(encoded)) + encoded


def market_quote_frame(sequence=7):
    block = bytearray(hpquant_aeron_source.V3_BLOCK_LENGTH)
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
        hpquant_aeron_source.TEMPLATE_ID,
        hpquant_aeron_source.SCHEMA_ID,
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


def test_decode_sbe_v3_to_ctp_shaped_tick():
    tick = hpquant_aeron_source.decode_market_quote(market_quote_frame())

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


def test_subscriber_writes_directly_to_supplied_queue_and_detects_gap():
    context = zmq.Context.instance()
    publisher = context.socket(zmq.PUSH)
    publisher.setsockopt(zmq.LINGER, 0)
    port = publisher.bind_to_random_port("tcp://127.0.0.1")
    output = queue.Queue()
    subscriber = hpquant_aeron_source.AeronTickSubscriber(
        f"tcp://127.0.0.1:{port}", output
    )
    try:
        publisher.send_multipart([b"snapshot", market_quote_frame(1)])
        received = subscriber.receive_once()
        assert received["AeronSequence"] == 1
        assert output.get_nowait()["Contract"] == "IC2609"

        publisher.send_multipart([b"snapshot", market_quote_frame(3)])
        with pytest.raises(hpquant_aeron_source.SequenceGapError):
            subscriber.receive_once()
    finally:
        subscriber.close()
        publisher.close(linger=0)
