import socket
import sys
from types import ModuleType
from types import SimpleNamespace


class _StubMdSpi:
    pass


stub_mdapi = ModuleType("openctp_ctp.thostmduserapi")
stub_mdapi.CThostFtdcMdSpi = _StubMdSpi
stub_package = ModuleType("openctp_ctp")
stub_package.thostmduserapi = stub_mdapi
sys.modules.setdefault("openctp_ctp", stub_package)
sys.modules.setdefault("openctp_ctp.thostmduserapi", stub_mdapi)

from aeron_mvp import ctp_bridge


def decode_text(value):
    return value.split(b"\0", 1)[0].decode("utf-8")


def test_ctp_v3_packet_contains_leader_fields():
    receiver = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    receiver.bind(("127.0.0.1", 0))
    receiver.settimeout(1)
    bridge = ctp_bridge.CtpMarketBridge(
        SimpleNamespace(
            udp_host="127.0.0.1",
            udp_port=receiver.getsockname()[1],
            repeat=25,
        )
    )
    tick = SimpleNamespace(
        InstrumentID="IF2609",
        ExchangeID="CFFEX",
        TradingDay="20260827",
        ActionDay="20260827",
        UpdateTime="14:08:00",
        UpdateMillisec=125,
        LastPrice=4512.8,
        Volume=1234,
        Turnover=5_000_100.25,
        OpenInterest=98_765.5,
        PreSettlementPrice=4480.0,
        PreClosePrice=4490.0,
        PreOpenInterest=98_000.0,
        OpenPrice=4500.0,
        HighestPrice=4520.0,
        LowestPrice=4475.0,
        ClosePrice=4512.8,
        SettlementPrice=4510.0,
        UpperLimitPrice=4950.0,
        LowerLimitPrice=4050.0,
        PreDelta=0.1,
        CurrDelta=0.2,
        AveragePrice=4505.5,
        **{
            f"{side}{kind}{level}": (
                4512.8 - level * 0.2
                if side == "Bid" and kind == "Price"
                else 4512.8 + level * 0.2
                if side == "Ask" and kind == "Price"
                else level * 10
            )
            for level in range(1, 6)
            for side in ("Bid", "Ask")
            for kind in ("Price", "Volume")
        },
    )

    try:
        bridge.OnRtnDepthMarketData(tick)
        packet, _ = receiver.recvfrom(ctp_bridge.PACKET.size)
        values = ctp_bridge.PACKET.unpack(packet)
    finally:
        bridge.close()
        receiver.close()

    assert len(packet) == 462
    assert values[0] == ctp_bridge.MAGIC
    assert values[1] == 3
    assert values[3] == 25
    assert values[4] == 1
    assert values[7] == 4512.8
    assert values[28] == 1234
    assert values[29:34] == (5_000_100.25, 98_765.5, 4480.0, 4490.0, 98_000.0)
    assert values[43] == 4505.5
    assert values[44] == 125
    assert decode_text(values[45]) == "IF2609"
    assert decode_text(values[46]) == "20260827"
    assert decode_text(values[48]) == "CFFEX"
    assert decode_text(values[49]) == "20260827"
    assert decode_text(values[50]) == "14:08:00"
