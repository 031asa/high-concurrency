import argparse
import json
import sys
import threading
import time

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

    def __init__(self, account, password, ini_path, order_threshold, order_cancel_threshold):
        self.account = account
        self.listener = MonitorListener()
        self.listener.on_login = self._on_login
        self.listener.on_order = self._on_order
        self.listener.on_caughtup = self._on_caughtup
        self.listener.on_failed_cancel_order = self._on_failed_cancel_order
        self.api = YDApi(self.listener, account, password, ini_path)

        self.last_status = {}
        self.counted_live_orders = set()
        self.counted_live_cancels = set()
        self.counted_cancel_successes = set()
        self.order_count = 0
        self.cancel_count = 0
        self.cancel_success_count = 0
        self.thresholds = {
            "order_count": order_threshold,
            "order_cancel_count": order_cancel_threshold,
        }
        self.threshold_alerted = set()
        self.lock = threading.Lock()

        monitor_logger.info(
            "MONITOR_CONFIG process=INDEPENDENT scope=ACCOUNT_LIVE "
            "order_threshold=%s order_cancel_threshold=%s duplicate_monitoring=DISABLED",
            order_threshold,
            order_cancel_threshold,
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

            if not was_known and key not in self.counted_live_orders:
                self.counted_live_orders.add(key)
                self.order_count += 1
                self._snapshot("ORDER_CALLBACK")

            if status == 2 and previous_status != 2 and key not in self.counted_live_cancels:
                self.counted_live_cancels.add(key)
                self.cancel_count += 1
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
            self._snapshot("CANCEL_FAILED_CALLBACK")

    def start(self, timeout):
        result = self.api.start()
        runtime_logger.info("ACCOUNT_MONITOR_START process=INDEPENDENT result=%s", result)
        if result is False:
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
            time.sleep(seconds)
            return
        while True:
            time.sleep(1)


def parse_args():
    monitor_config = load_json(MONITOR_CONFIG_FILE) if MONITOR_CONFIG_FILE.exists() else {}
    parser = argparse.ArgumentParser(description="易达独立报撤单监控进程（不发送订单）")
    parser.add_argument("--account-config", default=str(PROJECT_ROOT / "config" / "account.json"))
    parser.add_argument("--api-config", default="config/ydClient.ini")
    parser.add_argument("--startup-timeout", type=int, default=60)
    parser.add_argument("--wait-seconds", type=non_negative_int, default=0, help="0 表示持续运行到 Ctrl+C")
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
    try:
        config = load_json(args.account_config)
        monitor = AccountOrderMonitor(
            config["name"],
            config["password"],
            args.api_config,
            args.order_threshold,
            args.order_cancel_threshold,
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


if __name__ == "__main__":
    sys.exit(main())
