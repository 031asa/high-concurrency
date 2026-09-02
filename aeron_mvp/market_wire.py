"""Independent decoder for the versioned YD Market Wire contract."""

from __future__ import annotations

import struct
from typing import Any


TOPIC = b"snapshot"
SCHEMA_ID = 701
TEMPLATE_ID = 1
HEADER = struct.Struct("<HHHH")
V2_BLOCK_LENGTH = 194
V3_BLOCK_LENGTH = 324
V3_DOUBLE_FIELDS = (
    "Turnover",
    "OpenInterest",
    "PreSettlementPrice",
    "PreClosePrice",
    "PreOpenInterest",
    "OpenPrice",
    "HighestPrice",
    "LowestPrice",
    "ClosePrice",
    "SettlementPrice",
    "UpperLimitPrice",
    "LowerLimitPrice",
    "PreDelta",
    "CurrDelta",
    "AveragePrice",
)


class SbeDecodeError(ValueError):
    pass


def _unpack(fmt: str, payload: bytes, offset: int):
    try:
        return struct.unpack_from(fmt, payload, offset)[0]
    except struct.error as exc:
        raise SbeDecodeError(f"truncated SBE frame at offset {offset}") from exc


def _decode_var_string(payload: bytes, offset: int) -> tuple[str, int]:
    length = _unpack("<H", payload, offset)
    offset += 2
    end = offset + length
    if end > len(payload):
        raise SbeDecodeError(f"truncated SBE var-data at offset {offset}")
    return payload[offset:end].decode("utf-8"), end


def decode_market_quote(payload: bytes) -> dict[str, Any]:
    if len(payload) < HEADER.size:
        raise SbeDecodeError("SBE frame is shorter than the message header")
    block_length, template_id, schema_id, version = HEADER.unpack_from(payload)
    if schema_id != SCHEMA_ID or template_id != TEMPLATE_ID:
        raise SbeDecodeError(
            f"unexpected SBE schema={schema_id} template={template_id}"
        )
    if version < 2 or block_length < V2_BLOCK_LENGTH:
        raise SbeDecodeError(
            f"unsupported SBE version={version} block_length={block_length}"
        )
    fixed = HEADER.size
    sequence = _unpack("<Q", payload, fixed)
    market_timestamp_ns = _unpack("<Q", payload, fixed + 8)
    local_receive_ns = _unpack("<Q", payload, fixed + 16)
    last_price = _unpack("<d", payload, fixed + 24)
    bid_prices = [_unpack("<d", payload, fixed + 32)]
    ask_prices = [_unpack("<d", payload, fixed + 40)]
    bid_volumes = [_unpack("<q", payload, fixed + 48)]
    ask_volumes = [_unpack("<q", payload, fixed + 56)]
    timestamp_valid = bool(_unpack("<B", payload, fixed + 64))
    depth_levels = _unpack("<B", payload, fixed + 65)
    for level in range(2, 6):
        level_offset = fixed + 66 + (level - 2) * 32
        bid_prices.append(_unpack("<d", payload, level_offset))
        ask_prices.append(_unpack("<d", payload, level_offset + 8))
        bid_volumes.append(_unpack("<q", payload, level_offset + 16))
        ask_volumes.append(_unpack("<q", payload, level_offset + 24))

    quote_fields: dict[str, Any] = {
        "Volume": 0,
        **{name: 0.0 for name in V3_DOUBLE_FIELDS},
        "UpdateMillisec": 0,
    }
    if version >= 3:
        if block_length < V3_BLOCK_LENGTH:
            raise SbeDecodeError(f"invalid v3 block length: {block_length}")
        quote_fields["Volume"] = _unpack("<q", payload, fixed + 194)
        for index, name in enumerate(V3_DOUBLE_FIELDS):
            quote_fields[name] = _unpack("<d", payload, fixed + 202 + index * 8)
        quote_fields["UpdateMillisec"] = _unpack("<H", payload, fixed + 322)

    var_offset = fixed + block_length
    session_id, var_offset = _decode_var_string(payload, var_offset)
    instrument, var_offset = _decode_var_string(payload, var_offset)
    trading_day, var_offset = _decode_var_string(payload, var_offset)
    market_timestamp_raw, var_offset = _decode_var_string(payload, var_offset)
    exchange_id = action_day = update_time = source = ""
    if version >= 3:
        exchange_id, var_offset = _decode_var_string(payload, var_offset)
        action_day, var_offset = _decode_var_string(payload, var_offset)
        update_time, var_offset = _decode_var_string(payload, var_offset)
        source, var_offset = _decode_var_string(payload, var_offset)
    if var_offset != len(payload):
        raise SbeDecodeError(
            f"unexpected trailing SBE bytes: decoded={var_offset} total={len(payload)}"
        )

    tick: dict[str, Any] = {
        "type": "snapshot",
        "Contract": instrument,
        "Datetime": market_timestamp_raw,
        "TradingDay": trading_day,
        "InstrumentID": instrument,
        "ExchangeID": exchange_id,
        "ActionDay": action_day,
        "UpdateTime": update_time,
        "LastPrice": last_price,
        "TimestampValid": timestamp_valid,
        "DepthLevels": depth_levels,
        "AeronSessionID": session_id,
        "AeronSequence": sequence,
        "SchemaVersion": version,
        "MarketSource": source,
        "MarketTimestampNs": market_timestamp_ns,
        "LocalReceiveNs": local_receive_ns,
        **quote_fields,
    }
    for level in range(1, 6):
        index = level - 1
        tick[f"BidPrice{level}"] = bid_prices[index]
        tick[f"AskPrice{level}"] = ask_prices[index]
        tick[f"BidVolume{level}"] = bid_volumes[index]
        tick[f"AskVolume{level}"] = ask_volumes[index]
    return tick
