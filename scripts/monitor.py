import argparse
import json
import socket
import sys
import threading
import time
from pathlib import Path

from order import (
    MONITOR_CONFIG_FILE,
    PROJECT_ROOT,
    Listener,
    YDApi,
    error_logger,
    get_error_msg,
    load_json,
    mask_account,
    monitor_logger,
    non_negative_int,
    runtime_logger,
)


class MonitorListener(Listener):
    def __init__(self):
        super().__init__()
        self.process_name = "INDEPENDENT"
        self.on_failed_cancel_order = None

    def failed_cancel_order(self, failed):
        super().failed_cancel_order(failed)
        if self.on_failed_cancel_order:
            self.on_failed_cancel_order(failed)


class AccountOrderMonitor:
    """独立监听当前账号的真实订单回报，不发送任何交易指令。"""

    def __init__(
        self,
        account,
        password,
        ini_path,
        order_threshold,
        order_cancel_threshold,
        heartbeat_seconds,
        connection_probe_timeout,
        connection_failure_threshold,
    ):
        self.account = account
        self.api_config_path, self.server_host, self.server_port = load_server_endpoint(ini_path)
        self.listener = MonitorListener()
        self.listener.on_login = self._on_login
        self.listener.on_order = self._on_order
        self.listener.on_caughtup = self._on_caughtup
        self.listener.on_failed_cancel_order = self._on_failed_cancel_order
        # pyyd 原生模块在含中文的绝对路径下可能创建失败；保持官方示例使用的相对路径。
        self.api = YDApi(self.listener, account, password, str(ini_path))

        self.last_status = {}
        self.counted_live_orders = set()
        self.counted_live_cancels = set()
        self.counted_cancel_successes = set()
        self.order_count = 0
        self.cancel_count = 0
        self.cancel_success_count = 0
        self.heartbeat_seconds = heartbeat_seconds
        self.connection_probe_timeout = connection_probe_timeout
        self.connection_failure_threshold = connection_failure_threshold
        self.api_start_state = "NOT_STARTED"
        self.api_session_ready = False
        self.server_connection_state = "UNKNOWN"
        self.transport_reachable = None
        self.transport_failure_count = 0
        self.caughtup_count = 0
        self.last_server_connection_event = "UNKNOWN"
        self.last_server_connection_at = ""
        self.last_order_event_at = ""
        self.last_cancel_event_at = ""
        self.thresholds = {
            "order_count": order_threshold,
            "order_cancel_count": order_cancel_threshold,
        }
        self.threshold_alerted = set()
        self.lock = threading.Lock()
        self.heartbeat_stop = threading.Event()
        self.heartbeat_thread = None

        monitor_logger.info(
            "MONITOR_CONFIG process=INDEPENDENT scope=ACCOUNT_LIVE "
            "order_threshold=%s order_cancel_threshold=%s heartbeat_seconds=%s "
            "connection_probe_timeout=%s connection_failure_threshold=%s "
            "duplicate_monitoring=DISABLED",
            order_threshold,
            order_cancel_threshold,
            heartbeat_seconds,
            connection_probe_timeout,
            connection_failure_threshold,
        )

    @staticmethod
    def _order_key(order):
        return (
            str(getattr(order, "account", "")),
            int(getattr(order, "order_group", 0)),
            int(getattr(order, "order_ref", 0)),
        )

    def _check_threshold(self, metric, current):
        threshold = self.thresholds[metric]
        if threshold > 0 and current >= threshold and metric not in self.threshold_alerted:
            self.threshold_alerted.add(metric)
            monitor_logger.warning(
                "MONITOR_ALERT process=INDEPENDENT scope=ACCOUNT_LIVE "
                "metric=%s current=%s threshold=%s",
                metric,
                current,
                threshold,
            )

    def _snapshot(self, reason):
        order_cancel_count = self.order_count + self.cancel_count
        monitor_logger.info(
            "MONITOR_STATS process=INDEPENDENT scope=ACCOUNT_LIVE reason=%s "
            "order_count=%s cancel_count=%s order_cancel_count=%s cancel_success_count=%s",
            reason,
            self.order_count,
            self.cancel_count,
            order_cancel_count,
            self.cancel_success_count,
        )
        self._check_threshold("order_count", self.order_count)
        self._check_threshold("order_cancel_count", order_cancel_count)

    def _on_login(self, error, max_order_ref, is_monitor):
        if error == 0:
            runtime_logger.info(
                "ACCOUNT_MONITOR_LOGIN process=INDEPENDENT account=%s "
                "max_order_ref=%s is_monitor=%s",
                mask_account(self.account),
                max_order_ref,
                is_monitor,
            )
        else:
            error_logger.error(
                "ACCOUNT_MONITOR_LOGIN_FAILED process=INDEPENDENT error=%s message=%s",
                error,
                get_error_msg(error),
            )

    def _on_caughtup(self):
        with self.lock:
            previous = self.server_connection_state
            event = "RECONNECTED" if self.caughtup_count > 0 or previous in {"DISCONNECTED", "RECOVERING"} else "CONNECTED"
            self.caughtup_count += 1
            self.api_start_state = "READY"
            self.api_session_ready = True
            self.server_connection_state = "CONNECTED"
            self.transport_reachable = True
            self.transport_failure_count = 0
            self.last_server_connection_event = event
            self.last_server_connection_at = time.strftime("%Y-%m-%dT%H:%M:%S%z")
            caughtup_count = self.caughtup_count
        monitor_logger.warning(
            "TRADING_SERVER_CONNECTION event=%s process=INDEPENDENT source=YDAPI_CAUGHTUP "
            "previous=%s current=CONNECTED caughtup_count=%s",
            event,
            previous,
            caughtup_count,
        )
        monitor_logger.warning(
            "ACCOUNT_MONITOR_READY process=INDEPENDENT scope=ACCOUNT_LIVE "
            "account=%s historical_orders=%s",
            mask_account(self.account),
            len(self.last_status),
        )

    def _on_order(self, order):
        key = self._order_key(order)
        status = int(getattr(order, "status", -1))
        instrument = str(getattr(order, "instrument", ""))

        with self.lock:
            was_known = key in self.last_status
            previous_status = self.last_status.get(key)
            self.last_status[key] = status

            if not self.listener.has_caughtup:
                return

            monitor_logger.info(
                "ACCOUNT_MONITOR_ORDER process=INDEPENDENT source=LIVE "
                "key=%s instrument=%s previous_status=%s current_status=%s",
                key,
                instrument,
                previous_status,
                status,
            )
            self.last_order_event_at = time.strftime("%Y-%m-%dT%H:%M:%S%z")

            if not was_known and key not in self.counted_live_orders:
                self.counted_live_orders.add(key)
                self.order_count += 1
                self._snapshot("ORDER_CALLBACK")

            if status == 2 and previous_status != 2 and key not in self.counted_live_cancels:
                self.counted_live_cancels.add(key)
                self.cancel_count += 1
                self.last_cancel_event_at = time.strftime("%Y-%m-%dT%H:%M:%S%z")
            if status == 2 and previous_status != 2 and key not in self.counted_cancel_successes:
                self.counted_cancel_successes.add(key)
                self.cancel_success_count += 1
                self._snapshot("CANCEL_CALLBACK")

    def _on_failed_cancel_order(self, failed):
        if not self.listener.has_caughtup:
            return
        key = self._order_key(failed)
        with self.lock:
            if key in self.counted_live_cancels:
                return
            self.counted_live_cancels.add(key)
            self.cancel_count += 1
            self.last_cancel_event_at = time.strftime("%Y-%m-%dT%H:%M:%S%z")
            self._snapshot("CANCEL_FAILED_CALLBACK")

    def _probe_server(self):
        try:
            with socket.create_connection(
                (self.server_host, self.server_port),
                timeout=self.connection_probe_timeout,
            ):
                return True, "NONE"
        except OSError as exc:
            error_code = getattr(exc, "winerror", None) or getattr(exc, "errno", None) or type(exc).__name__
            return False, str(error_code)

    def _record_transport_probe(self, reachable, error_code):
        log_event = None
        with self.lock:
            self.transport_reachable = reachable
            if reachable:
                self.transport_failure_count = 0
                if self.server_connection_state == "DISCONNECTED":
                    previous = self.server_connection_state
                    self.server_connection_state = "RECOVERING"
                    log_event = ("TRANSPORT_RESTORED", previous, "RECOVERING", 0, error_code)
            else:
                self.transport_failure_count += 1
                if (
                    self.transport_failure_count >= self.connection_failure_threshold
                    and self.server_connection_state not in {"DISCONNECTED", "UNKNOWN"}
                ):
                    previous = self.server_connection_state
                    self.server_connection_state = "DISCONNECTED"
                    self.api_session_ready = False
                    self.listener.has_caughtup = False
                    self.last_server_connection_event = "DISCONNECTED"
                    self.last_server_connection_at = time.strftime("%Y-%m-%dT%H:%M:%S%z")
                    log_event = (
                        "DISCONNECTED",
                        previous,
                        "DISCONNECTED",
                        self.transport_failure_count,
                        error_code,
                    )

        if log_event:
            event, previous, current, failures, code = log_event
            monitor_logger.warning(
                "TRADING_SERVER_CONNECTION event=%s process=INDEPENDENT source=TCP_PROBE "
                "previous=%s current=%s consecutive_failures=%s error_code=%s",
                event,
                previous,
                current,
                failures,
                code,
            )

    def _heartbeat(self):
        transport_reachable, probe_error = self._probe_server()
        self._record_transport_probe(transport_reachable, probe_error)

        with self.lock:
            api_start_state = self.api_start_state
            api_session_ready = self.api_session_ready
            connection_state = self.server_connection_state
            transport_reachable = self.transport_reachable
            transport_failure_count = self.transport_failure_count
            last_connection_event = self.last_server_connection_event
            last_connection_at = self.last_server_connection_at or "NONE"
            order_count = self.order_count
            cancel_count = self.cancel_count
            cancel_success_count = self.cancel_success_count
            last_order_at = self.last_order_event_at or "NONE"
            last_cancel_at = self.last_cancel_event_at or "NONE"

        monitor_logger.info(
            "MONITOR_HEARTBEAT process=INDEPENDENT state=RUNNING api_start_state=%s api_ready=%s "
            "connection_monitor=RUNNING connection_state=%s "
            "connection_source=YDAPI_CAUGHTUP+TCP_PROBE transport_reachable=%s "
            "transport_failure_count=%s "
            "order_monitor=RUNNING cancel_monitor=RUNNING "
            "threshold_monitor=RUNNING order_count=%s cancel_count=%s "
            "cancel_success_count=%s last_connection_event=%s last_connection_at=%s "
            "last_order_at=%s last_cancel_at=%s",
            api_start_state,
            int(api_session_ready),
            connection_state,
            "YES" if transport_reachable else "NO",
            transport_failure_count,
            order_count,
            cancel_count,
            cancel_success_count,
            last_connection_event,
            last_connection_at,
            last_order_at,
            last_cancel_at,
        )

    def _heartbeat_loop(self):
        while not self.heartbeat_stop.wait(self.heartbeat_seconds):
            self._heartbeat()

    def start_heartbeat(self):
        if self.heartbeat_thread and self.heartbeat_thread.is_alive():
            return
        self.heartbeat_stop.clear()
        self._heartbeat()
        self.heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop,
            name="yd-monitor-heartbeat",
            daemon=True,
        )
        self.heartbeat_thread.start()

    def stop_heartbeat(self):
        self.heartbeat_stop.set()
        if self.heartbeat_thread and self.heartbeat_thread.is_alive():
            self.heartbeat_thread.join(timeout=1)

    def start(self, timeout):
        with self.lock:
            self.api_start_state = "CALLING"
            self.server_connection_state = "CONNECTING"
        self.start_heartbeat()
        try:
            result = self.api.start()
        except Exception:
            with self.lock:
                self.api_start_state = "FAILED"
            raise
        with self.lock:
            if not self.listener.has_caughtup:
                self.api_start_state = "RETURNED"
        runtime_logger.info("ACCOUNT_MONITOR_START process=INDEPENDENT result=%s", result)
        if result is False:
            with self.lock:
                self.api_start_state = "FAILED"
            raise RuntimeError("YDApi.start() 返回 False")

        deadline = time.time() + timeout
        while not self.listener.has_caughtup:
            if self.listener.login_error not in (None, 0):
                raise RuntimeError(f"登录失败: {get_error_msg(self.listener.login_error)}")
            if time.time() >= deadline:
                raise TimeoutError(f"等待 caughtup 超时: {timeout} 秒")
            time.sleep(0.2)

    def wait(self, seconds):
        if seconds > 0:
            self.heartbeat_stop.wait(seconds)
            return
        while not self.heartbeat_stop.wait(1):
            pass


def positive_int(value):
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("必须是大于 0 的整数")
    return number


def positive_float(value):
    number = float(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("必须是大于 0 的数字")
    return number


def load_server_endpoint(ini_path):
    path = Path(ini_path)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    path = path.resolve()
    if not path.exists():
        raise FileNotFoundError(f"易达配置文件不存在: {path}")

    values = {}
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith(("#", ";")) or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()

    host = values.get("TradingServerIP", "")
    port_text = values.get("TradingServerPort", "")
    if not host or not port_text:
        raise ValueError("ydClient.ini 缺少 TradingServerIP 或 TradingServerPort")
    try:
        port = int(port_text)
    except ValueError as exc:
        raise ValueError("TradingServerPort 必须是整数") from exc
    if not 1 <= port <= 65535:
        raise ValueError("TradingServerPort 必须在 1 到 65535 之间")
    return path, host, port


def parse_args():
    monitor_config = load_json(MONITOR_CONFIG_FILE) if MONITOR_CONFIG_FILE.exists() else {}
    parser = argparse.ArgumentParser(description="易达独立报撤单监控进程（不发送订单）")
    parser.add_argument("--account-config", default=str(PROJECT_ROOT / "config" / "account.json"))
    parser.add_argument("--api-config", default="config/ydClient.ini")
    parser.add_argument("--startup-timeout", type=int, default=60)
    parser.add_argument("--wait-seconds", type=non_negative_int, default=0, help="0 表示持续运行到 Ctrl+C")
    parser.add_argument(
        "--heartbeat-seconds",
        type=positive_int,
        default=positive_int(monitor_config.get("heartbeat_seconds", 5)),
        help="心跳日志间隔秒数；默认读取 config/monitor.json",
    )
    parser.add_argument(
        "--connection-probe-timeout",
        type=positive_float,
        default=positive_float(monitor_config.get("connection_probe_timeout", 2)),
        help="期货公司交易服务器 TCP 探测超时秒数；默认读取 config/monitor.json",
    )
    parser.add_argument(
        "--connection-failure-threshold",
        type=positive_int,
        default=positive_int(monitor_config.get("connection_failure_threshold", 2)),
        help="连续探测失败多少次后判定断开；默认读取 config/monitor.json",
    )
    parser.add_argument(
        "--order-threshold",
        type=non_negative_int,
        default=non_negative_int(monitor_config.get("order_threshold", 0)),
        help="账号实时报单回报阈值；默认读取 config/monitor.json",
    )
    parser.add_argument(
        "--order-cancel-threshold",
        type=non_negative_int,
        default=non_negative_int(monitor_config.get("order_cancel_threshold", 0)),
        help="账号实时报单加已撤回报阈值；默认读取 config/monitor.json",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    monitor = None
    try:
        config = load_json(args.account_config)
        monitor = AccountOrderMonitor(
            config["name"],
            config["password"],
            args.api_config,
            args.order_threshold,
            args.order_cancel_threshold,
            args.heartbeat_seconds,
            args.connection_probe_timeout,
            args.connection_failure_threshold,
        )
        monitor.start(args.startup_timeout)
        monitor.wait(args.wait_seconds)
        return 0
    except KeyboardInterrupt:
        runtime_logger.info("ACCOUNT_MONITOR_STOP process=INDEPENDENT reason=OPERATOR_CTRL_C")
        return 0
    except Exception as exc:
        error_logger.exception(
            "ACCOUNT_MONITOR_FAILED process=INDEPENDENT type=%s message=%s",
            type(exc).__name__,
            exc,
        )
        return 1
    finally:
        if monitor is not None:
            monitor.stop_heartbeat()


if __name__ == "__main__":
    sys.exit(main())
