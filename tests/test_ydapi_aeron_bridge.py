import socket
import unittest
from datetime import datetime
from types import SimpleNamespace

from aeron_mvp import ydapi_bridge


def decode_text(value):
    return value.split(b"\0", 1)[0].decode("utf-8")


class YdApiAeronBridgeTests(unittest.TestCase):
    def setUp(self):
        self.receiver = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.receiver.bind(("127.0.0.1", 0))
        self.receiver.settimeout(1)
        self.publisher = ydapi_bridge.YdApiUdpPublisher(
            "127.0.0.1", self.receiver.getsockname()[1], 25
        )

    def tearDown(self):
        self.publisher.close()
        self.receiver.close()

    def test_quote_is_encoded_with_real_fields_and_latency(self):
        received = datetime(2026, 8, 27, 14, 8, 0, 125000, tzinfo=ydapi_bridge.CHINA_TZ)
        received_ns = int(received.timestamp() * 1_000_000_000)
        tick = SimpleNamespace(
            instrument="IF2609",
            tradingday="20260827",
            timestamp="14:08:00.120",
            last_price=4512.8,
            bid_price=4512.6,
            bid_volume=7,
            ask_price=4513.0,
            ask_volume=9,
        )

        self.assertTrue(self.publisher.publish(tick, received_ns))
        packet, _ = self.receiver.recvfrom(ydapi_bridge.PACKET.size)
        values = ydapi_bridge.PACKET.unpack(packet)

        self.assertEqual(values[0], ydapi_bridge.MAGIC)
        self.assertEqual(values[2] & ydapi_bridge.TIMESTAMP_VALID, ydapi_bridge.TIMESTAMP_VALID)
        self.assertEqual(values[2] >> 8, 1)
        self.assertEqual(values[3], 25)
        self.assertEqual(values[4], 1)
        self.assertEqual(values[6] - values[5], 5_000_000)
        self.assertEqual(values[7:12], (4512.8, 4512.6, 4513.0, 7, 9))
        self.assertEqual(values[12:28], (0,) * 16)
        self.assertEqual(decode_text(values[28]), "IF2609")
        self.assertEqual(decode_text(values[29]), "20260827")
        self.assertEqual(decode_text(values[30]), "14:08:00.120")

    def test_invalid_timestamp_is_forwarded_as_invalid(self):
        tick = SimpleNamespace(instrument="rb2610", timestamp="bad-time")
        self.assertTrue(self.publisher.publish(tick, 1_800_000_000_000_000_000))
        packet, _ = self.receiver.recvfrom(ydapi_bridge.PACKET.size)
        values = ydapi_bridge.PACKET.unpack(packet)
        self.assertEqual(values[2] & ydapi_bridge.TIMESTAMP_VALID, 0)
        self.assertEqual(values[2] >> 8, 1)
        self.assertEqual(values[5], 0)

    def test_night_session_wrap_uses_nearest_trading_cycle(self):
        received = datetime(2026, 8, 27, 0, 0, 0, 10000, tzinfo=ydapi_bridge.CHINA_TZ)
        received_ns = int(received.timestamp() * 1_000_000_000)
        market_ns, valid = ydapi_bridge.market_timestamp_ns("23:59:59.995", received_ns)
        self.assertTrue(valid)
        self.assertEqual(received_ns - market_ns, 15_000_000)


if __name__ == "__main__":
    unittest.main()
