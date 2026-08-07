import os
import sys
import argparse
import logging
import time
import json
import csv
import threading
from decimal import Decimal
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
VENDOR_DIR = PROJECT_ROOT / "vendor" / "win64"
_DLL_DIRECTORY = None
if os.name == "nt" and VENDOR_DIR.exists():
    _DLL_DIRECTORY = os.add_dll_directory(str(VENDOR_DIR))

from pyyd import *

LOG_DIR = PROJECT_ROOT / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
PAUSE_FILE = PROJECT_ROOT / "config" / "trading.pause"
MONITOR_CONFIG_FILE = PROJECT_ROOT / "config" / "monitor.json"

# ---------- 日志配置 ----------
LOG_FORMAT = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
logging.basicConfig(
    level=logging.INFO,
    format=LOG_FORMAT,
    handlers=[
        logging.FileHandler(LOG_DIR / 'trader.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("Trader")

def category_logger(name, filename):
    category = logging.getLogger(f"Trader.{name}")
    category.setLevel(logging.INFO)
    if not category.handlers:
        handler = logging.FileHandler(LOG_DIR / filename, encoding='utf-8')
        handler.setFormatter(logging.Formatter(LOG_FORMAT))
        category.addHandler(handler)
    return category

runtime_logger = category_logger("Runtime", "runtime.log")
trading_logger = category_logger("Trading", "trading.log")
monitor_logger = category_logger("Monitor", "monitor.log")
error_logger = category_logger("Error", "error.log")

def load_json(path):
    if not os.path.exists(path):
        raise FileNotFoundError(f"文件不存在: {path}")
    with open(path, 'r', encoding='utf-8') as f:
        try:
            return json.load(f)
        except json.JSONDecodeError as e:
            raise ValueError(f"JSON 解析失败: {e}")

# ---------- 错误码工具 ----------
def load_error_dict(path=PROJECT_ROOT / "error_code.csv"):
    error_dict = {}
    with open(path, newline='', encoding='utf-8-sig') as f:
        reader = csv.reader(f)
        header = next(reader)
        code_idx = header.index("ErrorCode")
        exch_idx = header.index("Exchange")
        msg_idx = header.index("ErrorMsg")
        for row in reader:
            if len(row) > max(code_idx, exch_idx, msg_idx):
                key = (row[code_idx], row[exch_idx])
                error_dict[key] = row[msg_idx]
    return error_dict

ERROR_DICT = load_error_dict()

EXCHANGE_ERROR_SOURCE = {
    "SHFE": "上期所/能源所",
    "INE": "上期所/能源所",
    "DCE": "大商所",
    "CZCE": "郑商所",
    "CFFEX": "中金所",
    "GFEX": "广期所",
    "SSE": "上交所",
    "SZSE": "深交所",
}

def get_error_msg(error_code, source="易达"):
    code = str(error_code)
    return ERROR_DICT.get((code, source), ERROR_DICT.get((code, "易达"), f"未知错误({error_code})"))

def error_source(exchange):
    return EXCHANGE_ERROR_SOURCE.get(str(exchange).upper(), "易达")

def reject_validation(rule, message, **context):
    error_logger.error(
        "VALIDATION_REJECT rule=%s message=%s context=%s",
        rule,
        message,
        json.dumps(context, ensure_ascii=False, default=str),
    )
    raise ValueError(message)

def mask_account(account):
    if len(account) <= 4:
        return "*" * len(account)
    return f"{account[:2]}{'*' * (len(account) - 4)}{account[-2:]}"

# ---------- 状态映射 ----------
STATUS_MAP = {
    0: "已提交",
    1: "已报",
    2: "已撤",
    3: "全部成交",
    4: "拒单",
    5: "其他",
}

# ---------- 监听器（支持回调转发）----------
class Listener:
    def __init__(self):
        self.has_caughtup = False
        self.login_error = None
        self.connection_status = {}
        # 可动态绑定的回调
        self.on_login = None
        self.on_order = None
        self.on_trade = None
        self.on_caughtup = None
        self.on_response = None

    def login(self, error, maxorderref, ismonitor):
        self.login_error = error
        if self.on_login:
            self.on_login(error, maxorderref, ismonitor)
        else:
            logger.warning("on_login 未绑定，使用默认处理")
            if error == 0:
                logger.info("登录成功")
            else:
                logger.error(f"登录失败: {get_error_msg(error)} (code={error})")

    def caughtup(self):
        self.has_caughtup = True
        if self.on_caughtup:
            self.on_caughtup()
        else:
            logger.info("初始化数据接收完毕")

    def order(self, o):
        if self.on_order:
            self.on_order(o)
        else:
            # 默认简单打印
            print(f"[订单] {o.instrument} 状态:{STATUS_MAP.get(o.status, '?')}")

    def trade(self, t):
        if self.on_trade:
            self.on_trade(t)
        else:
            print(f"[成交] {t.instrument} 价:{t.price} 量:{t.volume}")

    def failed_cancel_order(self, failed):
        exchange = getattr(failed, "exchange", "")
        error = getattr(failed, "errno", "")
        error_logger.error(
            "FAILED_CANCEL_CALLBACK exchange=%s order_ref=%s order_group=%s "
            "order_sysid=%s error=%s message=%s",
            exchange,
            getattr(failed, "order_ref", ""),
            getattr(failed, "order_group", ""),
            getattr(failed, "order_sysid", ""),
            error,
            get_error_msg(error, error_source(exchange)),
        )

    def response(self, errorno, request_type, request_id=0):
        target_logger = runtime_logger if errorno == 0 else error_logger
        target_logger.log(
            logging.INFO if errorno == 0 else logging.ERROR,
            "API_RESPONSE error=%s message=%s request_type=%s request_id=%s",
            errorno,
            "" if errorno == 0 else get_error_msg(errorno),
            request_type,
            request_id,
        )
        if self.on_response:
            self.on_response(errorno, request_type, request_id)

    def trading_segment(self, exchange, segment_time):
        runtime_logger.info("TRADING_SEGMENT exchange=%s time=%s", exchange, segment_time)

    def exchange_conn_info(self, info):
        exchange = getattr(info, "exchange", "")
        conn = getattr(info, "conn", "")
        status = int(getattr(info, "conn_status", 0))
        key = (exchange, conn)
        previous = self.connection_status.get(key)
        if previous == status:
            event = "UNCHANGED"
        elif status == 1 and previous == 0:
            event = "RECONNECTED"
        elif status == 1:
            event = "CONNECTED"
        else:
            event = "DISCONNECTED"
        self.connection_status[key] = status
        monitor_logger.info(
            "EXCHANGE_CONNECTION exchange=%s conn=%s status=%s order_limit=%s cancel_limit=%s",
            exchange,
            conn,
            status,
            getattr(info, "order_limit", ""),
            getattr(info, "cancel_limit", ""),
        )
        monitor_logger.warning(
            "CONNECTION_MONITOR event=%s exchange=%s conn=%s previous=%s current=%s",
            event,
            exchange,
            conn,
            previous,
            status,
        )

# ---------- 交易核心类 ----------
class Trader:
    def __init__(self, account, password, ini_path, order_threshold=0, order_cancel_threshold=0):
        self.account = account
        # 1. 先创建监听器并绑定回调
        self.listener = Listener()
        self.listener.on_login = self._on_login
        self.listener.on_order = self._on_order
        self.listener.on_trade = self._on_trade
        self.listener.on_caughtup = self._on_caughtup
        self.listener.on_response = self._on_response

        # 2. 再创建 API 实例（传入已绑好回调的监听器）
        self.api = YDApi(self.listener, account, password, ini_path)

        self.orders = {}      # (account, order_group, order_ref) -> order
        self.orders_by_local = {}  # 仅供查询，不作为订单身份
        self.owned_orders = set()
        self.pending_signature = None
        self.auto_cancel = False
        self.cancel_requested = set()
        self.cancel_succeeded = set()
        self.trades = []      # 成交记录
        self.positions = {}   # 持仓
        self.monitor_counts = {
            "order_count": 0,
            "cancel_count": 0,
            "cancel_success_count": 0,
        }
        self.thresholds = {
            "order_count": order_threshold,
            "order_cancel_count": order_cancel_threshold,
        }
        self.threshold_alerted = set()
        self.response_events = {}
        self.response_results = {}
        self._running = False
        monitor_logger.info(
            "MONITOR_CONFIG order_threshold=%s order_cancel_threshold=%s duplicate_monitoring=DISABLED",
            order_threshold,
            order_cancel_threshold,
        )

    def start(self, timeout=60):
        r = self.api.start()
        runtime_logger.info(f"YDApi.start() = {r}")
        if r is False:
            raise RuntimeError("YDApi.start() 返回 False")
        deadline = time.time() + timeout
        while not self.listener.has_caughtup:
            if self.listener.login_error not in (None, 0):
                raise RuntimeError(f"登录失败: {get_error_msg(self.listener.login_error)}")
            if time.time() >= deadline:
                raise TimeoutError(f"等待 caughtup 超时: {timeout} 秒")
            time.sleep(0.2)
        self._running = True
        runtime_logger.info("REAL_API_READY account=%s data_source=YDApi", mask_account(self.account))

    def get_real_instrument_data(self, instrument):
        info = self.api.get_instrument(instrument)
        if info is None:
            reject_validation(
                "INSTRUMENT_EXISTS",
                f"YDApi.get_instrument 未找到合约: {instrument}",
                instrument=instrument,
            )
        market = self.api.get_marketdata(instrument)
        result = {
            "instrument": instrument,
            "exchange": getattr(info, "exchange", ""),
            "tick": getattr(info, "tick", None),
            "max_market_order_volume": getattr(info, "max_market_order_volume", None),
            "min_market_order_volume": getattr(info, "min_market_order_volume", None),
            "max_limit_order_volume": getattr(info, "max_limit_order_volume", None),
            "min_limit_order_volume": getattr(info, "min_limit_order_volume", None),
            "upper_limit_price": getattr(market, "upper_limit_price", None) if market else None,
            "lower_limit_price": getattr(market, "lower_limit_price", None) if market else None,
            "last_price": getattr(market, "last_price", None) if market else None,
            "bid_price": getattr(market, "bid_price", None) if market else None,
            "ask_price": getattr(market, "ask_price", None) if market else None,
        }
        runtime_logger.info("REAL_INSTRUMENT_DATA %s", json.dumps(result, ensure_ascii=False, default=str))
        return info, market

    def validate_with_real_api_data(self, params):
        info, market = self.get_real_instrument_data(params["instrument"])
        order_type = params["type"]
        volume = params["volume"]
        if order_type == 2:
            minimum = getattr(info, "min_market_order_volume", 0)
            maximum = getattr(info, "max_market_order_volume", 0)
        else:
            minimum = getattr(info, "min_limit_order_volume", 0)
            maximum = getattr(info, "max_limit_order_volume", 0)
        if minimum and volume < minimum:
            reject_validation(
                "MIN_ORDER_VOLUME",
                f"委托数量 {volume} 小于真实 API 下限 {minimum}",
                instrument=params["instrument"],
                volume=volume,
                minimum=minimum,
            )
        if maximum and volume > maximum:
            reject_validation(
                "MAX_ORDER_VOLUME",
                f"委托数量 {volume} 超过真实 API 上限 {maximum}",
                instrument=params["instrument"],
                volume=volume,
                maximum=maximum,
            )
        if order_type != 2:
            price = Decimal(str(params["price"]))
            tick = Decimal(str(getattr(info, "tick", 0)))
            if tick > 0 and price % tick != 0:
                reject_validation(
                    "PRICE_TICK",
                    f"价格 {price} 不是真实 API Tick {tick} 的整数倍",
                    instrument=params["instrument"],
                    price=price,
                    tick=tick,
                )
            if market:
                upper = getattr(market, "upper_limit_price", None)
                lower = getattr(market, "lower_limit_price", None)
                if upper is not None and price > Decimal(str(upper)):
                    reject_validation(
                        "UPPER_LIMIT_PRICE",
                        f"价格 {price} 高于真实涨停价 {upper}",
                        instrument=params["instrument"],
                        price=price,
                        upper=upper,
                    )
                if lower is not None and price < Decimal(str(lower)):
                    reject_validation(
                        "LOWER_LIMIT_PRICE",
                        f"价格 {price} 低于真实跌停价 {lower}",
                        instrument=params["instrument"],
                        price=price,
                        lower=lower,
                    )

    def _monitor_snapshot(self, reason):
        order_count = self.monitor_counts["order_count"]
        cancel_count = self.monitor_counts["cancel_count"]
        order_cancel_count = order_count + cancel_count
        monitor_logger.info(
            "MONITOR_STATS scope=PROCESS_LIVE reason=%s order_count=%s cancel_count=%s "
            "order_cancel_count=%s cancel_success_count=%s",
            reason,
            order_count,
            cancel_count,
            order_cancel_count,
            self.monitor_counts["cancel_success_count"],
        )
        self._check_threshold("order_count", order_count)
        self._check_threshold("order_cancel_count", order_cancel_count)

    def _check_threshold(self, metric, current):
        threshold = self.thresholds[metric]
        if threshold > 0 and current >= threshold and metric not in self.threshold_alerted:
            self.threshold_alerted.add(metric)
            monitor_logger.warning(
                "MONITOR_ALERT metric=%s current=%s threshold=%s",
                metric,
                current,
                threshold,
            )

    def _record_order_request(self):
        self.monitor_counts["order_count"] += 1
        self._monitor_snapshot("ORDER_REQUEST")

    def _record_cancel_request(self, count, reason):
        self.monitor_counts["cancel_count"] += count
        self._monitor_snapshot(reason)

    @staticmethod
    def _order_signature(order):
        return (
            getattr(order, "instrument", ""),
            int(getattr(order, "action", 0)),
            int(getattr(order, "open_close", 0)),
            int(getattr(order, "volume", 0)),
            float(getattr(order, "price", 0)),
            int(getattr(order, "type", 0)),
            int(getattr(order, "hedge", 1)),
        )

    @staticmethod
    def _order_key(order):
        return (
            str(getattr(order, "account", "")),
            int(getattr(order, "order_group", 0)),
            int(getattr(order, "order_ref", 0)),
        )

    def order_params(self, instrument, action, open_close, volume, price, order_type, hedge):
        params = {
            "instrument": instrument,
            "action": action,
            "open_close": open_close,
            "volume": volume,
            "price": price,
            "type": order_type,
            "hedge": hedge,
        }
        self.validate_with_real_api_data(params)
        return params

    def check_order(self, **params):
        trading_logger.info("REAL_ORDER_CHECK_REQUEST %s", json.dumps(params, ensure_ascii=False))
        result = self.api.insert_order(**params, checked=2)
        trading_logger.info("REAL_ORDER_CHECK_RETURN result=%s", result)
        return result

    def send_order(self, auto_cancel=False, **params):
        if PAUSE_FILE.exists():
            message = f"策略已暂停，拒绝下达交易指令；恢复文件: {PAUSE_FILE}"
            error_logger.error(
                "TRADE_BLOCKED control=LOCAL_STRATEGY_PAUSE layer=LOCAL_SCRIPT message=%s",
                message,
            )
            raise RuntimeError(message)
        order_ref = self.api.next_order_ref()
        send_params = dict(params, order_ref=order_ref)
        self.pending_signature = (
            params["instrument"], params["action"], params["open_close"],
            params["volume"], float(params["price"]), params["type"], params["hedge"]
        )
        self.auto_cancel = auto_cancel
        self._record_order_request()
        trading_logger.warning(
            "REAL_ORDER_SEND_REQUEST checked=0 order_ref=%s %s",
            order_ref,
            json.dumps(params, ensure_ascii=False),
        )
        result = self.api.insert_order(**send_params, checked=0)
        trading_logger.warning("REAL_ORDER_SEND_RETURN checked=0 order_ref=%s result=%s", order_ref, result)
        if result is not True:
            message = (
                "YDApi.insert_order 未接受真实发送请求（checked=0）；"
                "交易所不会产生订单回报，请检查连接、会话和订单参数"
            )
            error_logger.error(
                "REAL_ORDER_SEND_NOT_ACCEPTED checked=0 order_ref=%s result=%s message=%s",
                order_ref,
                result,
                message,
            )
            raise RuntimeError(message)
        return result

    def cancel_order(self, order):
        key = self._order_key(order)
        exchange = getattr(order, "exchange", "")
        order_group = int(getattr(order, "order_group", 0))
        order_ref = int(getattr(order, "order_ref", 0))
        order_sysid = getattr(order, "order_sysid", -1)
        self.cancel_requested.add(key)
        self._record_cancel_request(1, "CANCEL_REQUEST")
        trading_logger.warning(
            "REAL_CANCEL_REQUEST key=%s exchange=%s order_sysid=%s",
            key, exchange, order_sysid,
        )
        if order_group == 0:
            return self.api.cancel_order(exchange=exchange, order_sysid=order_sysid)
        return self.api.cancel_order(
            exchange=exchange,
            order_sysid=-1,
            order_ref=order_ref,
            order_group=order_group,
        )

    def batch_cancel_pending(self, limit=0):
        pending = [
            order for order in self.api.find_orders(account=self.account, pending=True)
            if getattr(order, "account", self.account) in ("", self.account)
        ]
        pending.sort(key=lambda order: (
            getattr(order, "time", ""),
            getattr(order, "order_group", 0),
            getattr(order, "order_ref", 0),
        ))
        selected = pending[:limit] if limit > 0 else pending
        if not selected:
            monitor_logger.warning("BATCH_CANCEL_SELECTION pending=0 selected=0 limit=%s", limit)
            return []

        keys = [self._order_key(order) for order in selected]
        self.cancel_requested.update(keys)
        monitor_logger.warning(
            "BATCH_CANCEL_SELECTION pending=%s selected=%s limit=%s keys=%s",
            len(pending),
            len(selected),
            limit,
            keys,
        )
        results = []
        for offset in range(0, len(selected), 16):
            batch = selected[offset:offset + 16]
            self._record_cancel_request(len(batch), "BATCH_CANCEL_REQUEST")
            trading_logger.warning(
                "REAL_BATCH_CANCEL_REQUEST batch=%s count=%s",
                offset // 16 + 1,
                len(batch),
            )
            result = self.api.cancel_multi_orders(batch, account=self.account)
            trading_logger.warning(
                "REAL_BATCH_CANCEL_RETURN batch=%s result=%s",
                offset // 16 + 1,
                result,
            )
            results.append(result)
        return results

    def set_account_trading_right(self, trading_right, source=3, response_timeout=0):
        request_id = int(time.time() * 1000) & 0x7FFFFFFF
        response_event = threading.Event()
        if response_timeout > 0:
            self.response_events[request_id] = response_event
        monitor_logger.warning(
            "TRADING_RIGHT_REQUEST layer=YD_API account=%s trading_right=%s source=%s request_id=%s",
            mask_account(self.account),
            trading_right,
            source,
            request_id,
        )
        result = self.api.set_trading_right(
            account=self.account,
            trading_right=trading_right,
            request_id=request_id,
            trading_right_source=source,
        )
        monitor_logger.warning(
            "TRADING_RIGHT_RETURN layer=YD_API trading_right=%s request_id=%s result=%s",
            trading_right,
            request_id,
            result,
        )
        if result is not True:
            self.response_events.pop(request_id, None)
            raise RuntimeError("易达 set_trading_right 未接受请求，不能判定交易权限已变更")
        if response_timeout > 0:
            if not response_event.wait(response_timeout):
                self.response_events.pop(request_id, None)
                raise TimeoutError(
                    f"等待易达交易权限 API_RESPONSE 超时: request_id={request_id}, timeout={response_timeout} 秒"
                )
            errorno, request_type = self.response_results.pop(request_id)
            self.response_events.pop(request_id, None)
            if errorno != 0:
                raise RuntimeError(
                    f"易达交易权限变更失败: error={errorno}, message={get_error_msg(errorno)}, "
                    f"request_type={request_type}, request_id={request_id}"
                )
            monitor_logger.warning(
                "TRADING_RIGHT_CONFIRMED layer=YD_API trading_right=%s request_id=%s "
                "request_type=%s error=0",
                trading_right,
                request_id,
                request_type,
            )
        return result

    # ---------- 内部回调处理 ----------
    def _on_login(self, error, maxorderref, ismonitor):
        if error == 0:
            runtime_logger.info(f"登录成功，max_order_ref={maxorderref}, is_admin={ismonitor}")
        else:
            error_logger.error(f"登录失败: {get_error_msg(error)}")

    def _on_response(self, errorno, request_type, request_id):
        event = self.response_events.get(request_id)
        if event is None:
            return
        self.response_results[request_id] = (errorno, request_type)
        event.set()

    def _on_caughtup(self):
        runtime_logger.info("历史数据同步完成")

    def _on_order(self, o):
        key = self._order_key(o)
        self.orders[key] = o
        self.orders_by_local[getattr(o, "order_localid", -1)] = key
        if self.listener.has_caughtup and self.pending_signature == self._order_signature(o):
            self.owned_orders.add(key)
        status_text = STATUS_MAP.get(o.status, f"未知({o.status})")
        direction = f"{'买' if o.action==0 else '卖'}{'开' if o.open_close==0 else '平'}"
        msg = (f"[_on_order] local_id:{o.order_localid} instrument:{o.instrument} direction:{direction} price:{o.price} volume:{o.volume} status:{status_text}")
        if o.order_sysid != -1:
            msg += f" sysid:{o.order_sysid}"
        if o.trade > 0:
            msg += f" 已成:{o.trade}"
        if o.errno != 0:
            msg += f" [错误:{get_error_msg(o.errno, error_source(getattr(o, 'exchange', '')))}]"
        if o.status == 2 and o.cancel_time:
            msg += f" 撤单时间:{o.cancel_time}"
        source = "LIVE" if self.listener.has_caughtup else "HISTORY"
        trading_logger.info("REAL_ORDER_CALLBACK source=%s key=%s %s", source, key, msg)

        if self.listener.has_caughtup and o.status == 2 and key in self.cancel_requested and key not in self.cancel_succeeded:
            self.cancel_succeeded.add(key)
            self.monitor_counts["cancel_success_count"] += 1
            self._monitor_snapshot("CANCEL_SUCCESS")

        if o.errno != 0:
            exchange = getattr(o, "exchange", "")
            error_logger.error(
                "COUNTER_ERROR category=ORDER exchange=%s error=%s message=%s key=%s",
                exchange,
                o.errno,
                get_error_msg(o.errno, error_source(exchange)),
                key,
            )

        if self.auto_cancel and key in self.owned_orders and o.status == 1 and key not in self.cancel_requested:
            self.cancel_requested.add(key)
            trading_logger.warning("真实订单已报，按 --auto-cancel 明确指令触发撤单 key=%s", key)
            self.cancel_order(o)

    def _on_trade(self, t):
        self.trades.append(t)
        direction = f"{'买' if t.action==0 else '卖'}{'开' if t.open_close==0 else '平'}"
        trading_logger.info(f"REAL_TRADE_CALLBACK 成交 {t.instrument} {direction} 价:{t.price} 量:{t.volume} "
                            f"手续费:{t.commission:.2f} 时间:{t.time}")

    def get_order(self, account, order_group, order_ref):
        return self.orders.get((account, order_group, order_ref))

    def get_all_orders(self):
        return list(self.orders.values())

    def get_trades(self):
        return self.trades

    def stop(self):
        self._running = False
        runtime_logger.info("交易系统停止")

def set_local_trading_pause(paused):
    if paused:
        PAUSE_FILE.write_text(
            json.dumps({"paused_at": time.strftime("%Y-%m-%d %H:%M:%S")}, ensure_ascii=False),
            encoding="utf-8",
        )
        monitor_logger.warning(
            "TRADE_CONTROL state=PAUSED layer=LOCAL_SCRIPT method=LOCAL_STRATEGY_FILE file=%s",
            PAUSE_FILE,
        )
    else:
        if PAUSE_FILE.exists():
            PAUSE_FILE.unlink()
        monitor_logger.warning(
            "TRADE_CONTROL state=RUNNING layer=LOCAL_SCRIPT method=LOCAL_STRATEGY_FILE file=%s",
            PAUSE_FILE,
        )

def non_negative_int(value):
    number = int(value)
    if number < 0:
        raise argparse.ArgumentTypeError("必须是大于等于 0 的整数")
    return number

# ---------- 测试入口 ----------
def parse_args():
    monitor_config = load_json(MONITOR_CONFIG_FILE) if MONITOR_CONFIG_FILE.exists() else {}
    parser = argparse.ArgumentParser(description="易达真实 Python API 人工测试")
    parser.add_argument("--account-config", default=str(PROJECT_ROOT / "config" / "account.json"))
    # 原生 yd.dll 对包含中文的绝对配置路径兼容性差；保持与官方示例一致，使用相对路径。
    parser.add_argument("--api-config", default="config/ydClient.ini")
    parser.add_argument("--startup-timeout", type=int, default=60)
    parser.add_argument("--wait-seconds", type=int, default=0, help="0 表示持续运行到 Ctrl+C")

    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check-only", action="store_true", help="调用 checked=2，只校验而不报至交易所")
    mode.add_argument("--send", action="store_true", help="调用真实 insert_order，可能产生真实委托")
    mode.add_argument("--batch-cancel", action="store_true", help="批量撤销真实 API 返回的当前账号未完成订单")
    mode.add_argument("--set-trading-right", type=int, choices=(0, 1, 2), help="0允许交易，1只可平仓，2禁止交易")
    mode.add_argument("--pause-trading", action="store_true", help="创建本地暂停标志，阻止后续 --send")
    mode.add_argument("--resume-trading", action="store_true", help="清除本地暂停标志")
    mode.add_argument("--pause-two-layer", action="store_true", help="先经易达临时禁止交易，再启用本地脚本暂停")
    mode.add_argument("--resume-two-layer", action="store_true", help="先经易达恢复交易，再清除本地脚本暂停")
    mode.add_argument("--show-monitor-config", action="store_true", help="显示 2.4/2.6 监测阈值配置，不连接柜台")

    parser.add_argument("--instrument")
    parser.add_argument("--action", type=int, choices=(0, 1))
    parser.add_argument("--open-close", type=int, choices=(0, 1, 3, 4))
    parser.add_argument("--volume", type=int)
    parser.add_argument("--price", type=float)
    parser.add_argument("--order-type", type=int, choices=(0, 1, 2, 3))
    parser.add_argument("--hedge", type=int, choices=(1, 2, 3))
    parser.add_argument("--confirm-account", default="")
    parser.add_argument("--auto-cancel", action="store_true", help="仅撤销本次程序识别到的已报订单")
    parser.add_argument("--cancel-limit", type=non_negative_int, default=0, help="批量撤单最多选择数量；0 表示全部未完成订单")
    parser.add_argument("--trading-right-source", type=int, choices=(1, 3), default=3, help="1用户永久设置，3用户临时设置")
    parser.add_argument("--control-response-timeout", type=non_negative_int, default=10, help="等待易达交易权限 API_RESPONSE 的秒数")
    parser.add_argument("--order-threshold", type=non_negative_int, default=non_negative_int(monitor_config.get("order_threshold", 0)), help="当前进程报单笔数预警阈值；默认读取 config/monitor.json")
    parser.add_argument("--order-cancel-threshold", type=non_negative_int, default=non_negative_int(monitor_config.get("order_cancel_threshold", 0)), help="当前进程报单+撤单笔数预警阈值；默认读取 config/monitor.json")
    return parser.parse_args()

def require_manual_order_input(args):
    names = ("instrument", "action", "open_close", "volume", "price", "order_type", "hedge")
    missing = [name for name in names if getattr(args, name) is None]
    if missing:
        flags = ", ".join("--" + name.replace("_", "-") for name in missing)
        raise ValueError(f"订单参数必须由测试人员明确提供，缺少: {flags}")

def require_confirmed_account(args, config):
    if args.confirm_account != config["name"]:
        raise ValueError("--confirm-account 必须与本地账号配置一致")

def main():
    args = parse_args()
    try:
        if args.show_monitor_config:
            resolved_config = {
                "config_file": "config/monitor.json",
                "order_threshold": args.order_threshold,
                "order_cancel_threshold": args.order_cancel_threshold,
                "duplicate_monitoring": "DISABLED",
            }
            monitor_logger.info("MONITOR_CONFIG_EVIDENCE %s", json.dumps(resolved_config, ensure_ascii=False))
            print(json.dumps(resolved_config, ensure_ascii=False, indent=2))
            return 0

        if args.pause_trading:
            set_local_trading_pause(True)
            return 0
        if args.resume_trading:
            set_local_trading_pause(False)
            return 0
        if args.send and PAUSE_FILE.exists():
            message = f"策略已暂停，拒绝下达交易指令；请先执行 --resume-trading：{PAUSE_FILE}"
            error_logger.error(
                "TRADE_BLOCKED control=LOCAL_STRATEGY_PAUSE layer=LOCAL_SCRIPT message=%s",
                message,
            )
            raise RuntimeError(message)

        config = load_json(args.account_config)
        if (args.pause_two_layer or args.resume_two_layer) and args.control_response_timeout == 0:
            raise ValueError("双层交易控制必须等待真实 API_RESPONSE，--control-response-timeout 不能为 0")

        trader = Trader(
            config["name"],
            config["password"],
            args.api_config,
            order_threshold=args.order_threshold,
            order_cancel_threshold=args.order_cancel_threshold,
        )
        try:
            trader.start(args.startup_timeout)

            if args.pause_two_layer:
                require_confirmed_account(args, config)
                runtime_logger.warning("TEST_MODE TWO_LAYER_PAUSE data_source=YDApi")
                trader.set_account_trading_right(
                    2,
                    args.trading_right_source,
                    args.control_response_timeout,
                )
                set_local_trading_pause(True)
                monitor_logger.warning(
                    "TWO_LAYER_CONTROL state=PAUSED yd_api=CONFIRMED local_script=PAUSED"
                )
            elif args.resume_two_layer:
                require_confirmed_account(args, config)
                runtime_logger.warning("TEST_MODE TWO_LAYER_RESUME data_source=YDApi")
                trader.set_account_trading_right(
                    0,
                    args.trading_right_source,
                    args.control_response_timeout,
                )
                set_local_trading_pause(False)
                monitor_logger.warning(
                    "TWO_LAYER_CONTROL state=RUNNING yd_api=CONFIRMED local_script=RUNNING"
                )
            elif args.check_only or args.send:
                require_manual_order_input(args)
                params = trader.order_params(
                    instrument=args.instrument,
                    action=args.action,
                    open_close=args.open_close,
                    volume=args.volume,
                    price=args.price,
                    order_type=args.order_type,
                    hedge=args.hedge,
                )
                trading_logger.info("TEST_INPUT source=OPERATOR %s", json.dumps(params, ensure_ascii=False))
                if args.check_only:
                    runtime_logger.info("TEST_MODE CHECK_ONLY data_source=YDApi")
                    trader.check_order(**params)
                else:
                    require_confirmed_account(args, config)
                    runtime_logger.warning("TEST_MODE REAL_ORDER data_source=YDApi auto_cancel=%s", args.auto_cancel)
                    trader.send_order(auto_cancel=args.auto_cancel, **params)
            elif args.batch_cancel:
                require_confirmed_account(args, config)
                runtime_logger.warning("TEST_MODE REAL_BATCH_CANCEL data_source=YDApi limit=%s", args.cancel_limit)
                trader.batch_cancel_pending(args.cancel_limit)
            elif args.set_trading_right is not None:
                require_confirmed_account(args, config)
                runtime_logger.warning("TEST_MODE SET_TRADING_RIGHT data_source=YDApi")
                trader.set_account_trading_right(
                    args.set_trading_right,
                    args.trading_right_source,
                    args.control_response_timeout,
                )
            elif args.instrument:
                runtime_logger.info("TEST_MODE INSPECT_INSTRUMENT data_source=YDApi")
                trader.get_real_instrument_data(args.instrument)
            else:
                runtime_logger.info("TEST_MODE CONNECT_ONLY：仅登录并接收真实 API 数据，不报单")

            if args.wait_seconds > 0:
                time.sleep(args.wait_seconds)
            else:
                while True:
                    time.sleep(1)
        except KeyboardInterrupt:
            runtime_logger.info("收到 Ctrl+C，结束人工测试")
        finally:
            trader.stop()
        return 0
    except Exception as exc:
        error_logger.exception("TEST_FAILED type=%s message=%s", type(exc).__name__, exc)
        return 1

if __name__ == "__main__":
    sys.exit(main())
