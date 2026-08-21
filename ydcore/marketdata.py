import argparse
import logging
import re
import sys
import threading
import time
from datetime import datetime, timedelta, timezone

from .trading import (
    PROJECT_ROOT,
    create_ydapi,
    error_logger,
    get_error_msg,
    load_json,
    mask_account,
)


CHINA_STANDARD_TIME = timezone(timedelta(hours=8))
YD_TRADING_DAY_START_HOUR = 17
MILLISECONDS_PER_DAY = 24 * 60 * 60 * 1000
marketdata_logger = logging.getLogger("Trader.MarketData")
YD_CLOCK_PATTERN = re.compile(
    r"^(?P<hour>\d{1,2}):(?P<minute>\d{2}):(?P<second>\d{2})(?:\.(?P<fraction>\d{1,9}))?$"
)


def yd_cycle_timestamp_ms(value):
    """Convert a UTC+8 wall-clock time to YD milliseconds since 17:00."""
    clock_ms = (
        (value.hour * 60 * 60 + value.minute * 60 + value.second) * 1000
        + value.microsecond // 1000
    )
    start_ms = YD_TRADING_DAY_START_HOUR * 60 * 60 * 1000
    return (clock_ms - start_ms) % MILLISECONDS_PER_DAY


def format_yd_timestamp(timestamp_ms):
    clock_ms = (
        int(timestamp_ms)
        + YD_TRADING_DAY_START_HOUR * 60 * 60 * 1000
    ) % MILLISECONDS_PER_DAY
    hours, remainder = divmod(clock_ms, 60 * 60 * 1000)
    minutes, remainder = divmod(remainder, 60 * 1000)
    seconds, milliseconds = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{milliseconds:03d}"


def parse_yd_timestamp_ms(value):
    """Normalize the real Python API HH:MM:SS.mmm timestamp to YD milliseconds."""
    if not isinstance(value, str):
        raise TypeError(f"Python YDApi timestamp must be a string: {value!r}")
    match = YD_CLOCK_PATTERN.fullmatch(value.strip())
    if not match:
        raise ValueError(f"invalid Python YDApi timestamp: {value!r}")
    hour = int(match.group("hour"))
    minute = int(match.group("minute"))
    second = int(match.group("second"))
    if hour > 23 or minute > 59 or second > 59:
        raise ValueError(f"invalid Python YDApi timestamp: {value!r}")
    fraction = match.group("fraction") or ""
    milliseconds = int((fraction + "000")[:3])
    clock_timestamp_ms = (
        (hour * 60 * 60 + minute * 60 + second) * 1000
        + milliseconds
    )
    start_ms = YD_TRADING_DAY_START_HOUR * 60 * 60 * 1000
    return (clock_timestamp_ms - start_ms) % MILLISECONDS_PER_DAY


def signed_timestamp_difference_ms(local_timestamp_ms, market_timestamp_ms):
    """Return local minus market time, corrected across the 24-hour boundary."""
    difference = int(local_timestamp_ms) - int(market_timestamp_ms)
    half_day_ms = MILLISECONDS_PER_DAY // 2
    if difference > half_day_ms:
        difference -= MILLISECONDS_PER_DAY
    elif difference < -half_day_ms:
        difference += MILLISECONDS_PER_DAY
    return difference


def positive_int(value):
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("must be an integer greater than 0")
    return number


def non_negative_int(value):
    number = int(value)
    if number < 0:
        raise argparse.ArgumentTypeError("must be an integer greater than or equal to 0")
    return number


def positive_float(value):
    number = float(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("must be a number greater than 0")
    return number


class MarketDataListener:
    def __init__(self, max_quotes):
        self.max_quotes = max_quotes
        self.login_error = None
        self.caughtup_event = threading.Event()
        self.quote_limit_event = threading.Event()
        self.lock = threading.Lock()
        self.quote_count = 0
        self.differences_ms = []

    def login(self, error, max_order_ref, is_monitor):
        self.login_error = error
        if error == 0:
            marketdata_logger.info(
                "MARKETDATA_LOGIN result=SUCCESS max_order_ref=%s is_monitor=%s",
                max_order_ref,
                is_monitor,
            )
        else:
            error_logger.error(
                "MARKETDATA_LOGIN result=FAILED error=%s message=%s",
                error,
                get_error_msg(error),
            )

    def caughtup(self):
        self.caughtup_event.set()
        marketdata_logger.info("MARKETDATA_CAUGHTUP data_source=YDApi")

    def marketdata(self, market_data):
        received_ns = time.time_ns()
        seconds, nanoseconds = divmod(received_ns, 1_000_000_000)
        received_at = datetime.fromtimestamp(seconds, CHINA_STANDARD_TIME).replace(
            microsecond=nanoseconds // 1000
        )
        local_timestamp_ms = yd_cycle_timestamp_ms(received_at)

        with self.lock:
            self.quote_count += 1
            sequence = self.quote_count

        instrument = str(getattr(market_data, "instrument", ""))
        raw_timestamp = getattr(market_data, "timestamp", None)
        try:
            market_timestamp_ms = parse_yd_timestamp_ms(raw_timestamp)
        except (TypeError, ValueError) as exc:
            error_logger.error(
                "MARKETDATA_TIMESTAMP_UNAVAILABLE sequence=%s instrument=%s "
                "raw_timestamp=%r reason=%s",
                sequence,
                instrument,
                raw_timestamp,
                exc,
            )
            self._set_quote_limit(sequence)
            return

        difference_ms = signed_timestamp_difference_ms(
            local_timestamp_ms,
            market_timestamp_ms,
        )
        absolute_difference_ms = abs(difference_ms)
        if difference_ms > 0:
            direction = "LOCAL_AFTER_MARKET"
        elif difference_ms < 0:
            direction = "LOCAL_BEFORE_MARKET"
        else:
            direction = "SAME_MILLISECOND"

        with self.lock:
            self.differences_ms.append(difference_ms)

        marketdata_logger.info(
            "MARKETDATA_TIMESTAMP sequence=%s instrument=%s tradingday=%s "
            "last_price=%s bid_price=%s bid_volume=%s ask_price=%s ask_volume=%s "
            "market_timestamp_ms=%s market_time=%s "
            "local_receive_time=%s local_cycle_timestamp_ms=%s "
            "has_difference=%s difference_ms=%+d absolute_difference_ms=%s direction=%s",
            sequence,
            instrument,
            getattr(market_data, "tradingday", ""),
            getattr(market_data, "last_price", ""),
            getattr(market_data, "bid_price", ""),
            getattr(market_data, "bid_volume", ""),
            getattr(market_data, "ask_price", ""),
            getattr(market_data, "ask_volume", ""),
            market_timestamp_ms,
            format_yd_timestamp(market_timestamp_ms),
            received_at.isoformat(timespec="milliseconds"),
            local_timestamp_ms,
            "YES" if difference_ms != 0 else "NO",
            difference_ms,
            absolute_difference_ms,
            direction,
        )
        self._set_quote_limit(sequence)

    def _set_quote_limit(self, sequence):
        if self.max_quotes > 0 and sequence >= self.max_quotes:
            self.quote_limit_event.set()

    def summary(self, instrument):
        with self.lock:
            quote_count = self.quote_count
            differences = list(self.differences_ms)
        if not differences:
            error_logger.error(
                "MARKETDATA_TIMESTAMP_SUMMARY instrument=%s quotes=%s comparable_quotes=0 "
                "result=FAILED reason=NO_VALID_TIMESTAMP",
                instrument,
                quote_count,
            )
            print(
                "\n=== 行情延迟测试汇总 ===\n"
                f"合约: {instrument}\n"
                f"收到行情: {quote_count} 条\n"
                "有效时间戳: 0 条\n"
                "结果: 失败（没有可比较的行情时间戳）",
                flush=True,
            )
            return False

        absolute_differences = [abs(value) for value in differences]
        average_ms = sum(differences) / len(differences)
        average_absolute_ms = sum(absolute_differences) / len(absolute_differences)
        marketdata_logger.info(
            "MARKETDATA_TIMESTAMP_SUMMARY instrument=%s quotes=%s comparable_quotes=%s "
            "has_difference=%s min_difference_ms=%s average_difference_ms=%.3f "
            "max_difference_ms=%s min_absolute_difference_ms=%s "
            "average_absolute_difference_ms=%.3f max_absolute_difference_ms=%s "
            "result=SUCCESS",
            instrument,
            quote_count,
            len(differences),
            "YES" if any(value != 0 for value in differences) else "NO",
            min(differences),
            average_ms,
            max(differences),
            min(absolute_differences),
            average_absolute_ms,
            max(absolute_differences),
        )
        print(
            "\n=== 行情延迟测试汇总 ===\n"
            f"合约: {instrument}\n"
            f"收到行情: {quote_count} 条\n"
            f"有效时间戳: {len(differences)} 条\n"
            f"平均延迟（绝对值）: {average_absolute_ms:.3f} ms\n"
            f"最小延迟（绝对值）: {min(absolute_differences)} ms\n"
            f"最大延迟（绝对值）: {max(absolute_differences)} ms\n"
            f"平均时间差（本机减行情）: {average_ms:+.3f} ms\n"
            "说明: 该结果包含本机与行情源的时钟偏差，不等同于纯网络单向延迟。",
            flush=True,
        )
        return True


class MarketDataProbe:
    def __init__(self, account, password, api_config, max_quotes):
        self.account = account
        self.listener = MarketDataListener(max_quotes)
        self.api = create_ydapi(self.listener, account, password, api_config)
        self.subscribed_instrument = None

    def start(self, timeout):
        result = self.api.start()
        marketdata_logger.info(
            "MARKETDATA_API_START account=%s result=%s",
            mask_account(self.account),
            result,
        )
        if result is False:
            raise RuntimeError("YDApi.start() returned False")

        deadline = time.time() + timeout
        while not self.listener.caughtup_event.wait(0.2):
            if self.listener.login_error not in (None, 0):
                raise RuntimeError(
                    f"login failed: {get_error_msg(self.listener.login_error)}"
                )
            if time.time() >= deadline:
                raise TimeoutError(f"waiting for caughtup timed out after {timeout} seconds")

    def subscribe(self, instrument):
        if self.api.get_instrument(instrument) is None:
            raise ValueError(f"instrument not found in YDApi: {instrument}")
        result = self.api.subscribe(instrument)
        if result is False:
            raise RuntimeError(f"YDApi.subscribe() returned False: {instrument}")
        self.subscribed_instrument = instrument
        marketdata_logger.info(
            "MARKETDATA_SUBSCRIBE instrument=%s result=%s data_source=YDApi",
            instrument,
            result,
        )

    def wait(self, duration_seconds):
        self.listener.quote_limit_event.wait(duration_seconds)

    def unsubscribe(self):
        if not self.subscribed_instrument:
            return
        instrument = self.subscribed_instrument
        result = self.api.unsubscribe(instrument)
        self.subscribed_instrument = None
        marketdata_logger.info(
            "MARKETDATA_UNSUBSCRIBE instrument=%s result=%s",
            instrument,
            result,
        )


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Subscribe to real YDApi market data and compare timestamps"
    )
    parser.add_argument("--instrument", required=True, help="real instrument ID to subscribe")
    parser.add_argument(
        "--account-config",
        default=str(PROJECT_ROOT / "config" / "account.json"),
    )
    parser.add_argument("--api-config", default="config/ydClient.ini")
    parser.add_argument("--startup-timeout", type=positive_int, default=60)
    parser.add_argument("--duration-seconds", type=positive_float, default=10)
    parser.add_argument(
        "--max-quotes",
        type=non_negative_int,
        default=10,
        help="stop after this many callbacks; 0 waits for the full duration",
    )
    return parser.parse_args(argv)


def run(argv=None):
    args = parse_args(argv)
    probe = None
    try:
        account_config = load_json(args.account_config)
        probe = MarketDataProbe(
            account_config["name"],
            account_config["password"],
            args.api_config,
            args.max_quotes,
        )
        probe.start(args.startup_timeout)
        probe.subscribe(args.instrument)
        probe.wait(args.duration_seconds)
        if probe.listener.quote_count == 0:
            error_logger.error(
                "MARKETDATA_TEST_FAILED instrument=%s reason=NO_MARKETDATA_CALLBACK "
                "duration_seconds=%s",
                args.instrument,
                args.duration_seconds,
            )
            return 2
        return 0 if probe.listener.summary(args.instrument) else 3
    except KeyboardInterrupt:
        marketdata_logger.info("MARKETDATA_TEST_STOP reason=OPERATOR_CTRL_C")
        if probe and probe.listener.quote_count > 0:
            return 0 if probe.listener.summary(args.instrument) else 3
        return 130
    except Exception as exc:
        error_logger.exception(
            "MARKETDATA_TEST_FAILED type=%s message=%s",
            type(exc).__name__,
            exc,
        )
        return 1
    finally:
        if probe is not None:
            try:
                probe.unsubscribe()
            except Exception as exc:
                error_logger.exception(
                    "MARKETDATA_UNSUBSCRIBE_FAILED type=%s message=%s",
                    type(exc).__name__,
                    exc,
                )


main = run
