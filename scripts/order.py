import os
import argparse
import logging
import time
import json
import csv
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

# ---------- 日志配置 ----------
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_DIR / 'trader.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("Trader")

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

def get_error_msg(error_code, source="易达"):
    return ERROR_DICT.get((str(error_code), source), f"未知错误({error_code})")

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
        # 可动态绑定的回调
        self.on_login = None
        self.on_order = None
        self.on_trade = None
        self.on_caughtup = None

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
        logger.error(
            "FAILED_CANCEL_CALLBACK exchange=%s order_ref=%s order_group=%s "
            "order_sysid=%s error=%s message=%s",
            getattr(failed, "exchange", ""),
            getattr(failed, "order_ref", ""),
            getattr(failed, "order_group", ""),
            getattr(failed, "order_sysid", ""),
            getattr(failed, "errno", ""),
            get_error_msg(getattr(failed, "errno", "")),
        )

    def response(self, errorno, request_type, request_id=0):
        level = logging.INFO if errorno == 0 else logging.ERROR
        logger.log(
            level,
            "API_RESPONSE error=%s message=%s request_type=%s request_id=%s",
            errorno,
            "" if errorno == 0 else get_error_msg(errorno),
            request_type,
            request_id,
        )

    def trading_segment(self, exchange, segment_time):
        logger.info("TRADING_SEGMENT exchange=%s time=%s", exchange, segment_time)

    def exchange_conn_info(self, info):
        logger.info(
            "EXCHANGE_CONNECTION exchange=%s conn=%s status=%s order_limit=%s cancel_limit=%s",
            getattr(info, "exchange", ""),
            getattr(info, "conn", ""),
            getattr(info, "conn_status", ""),
            getattr(info, "order_limit", ""),
            getattr(info, "cancel_limit", ""),
        )

# ---------- 交易核心类 ----------
class Trader:
    def __init__(self, account, password, ini_path):
        self.account = account
        # 1. 先创建监听器并绑定回调
        self.listener = Listener()
        self.listener.on_login = self._on_login
        self.listener.on_order = self._on_order
        self.listener.on_trade = self._on_trade
        self.listener.on_caughtup = self._on_caughtup

        # 2. 再创建 API 实例（传入已绑好回调的监听器）
        self.api = YDApi(self.listener, account, password, ini_path)

        self.orders = {}      # (account, order_group, order_ref) -> order
        self.orders_by_local = {}  # 仅供查询，不作为订单身份
        self.owned_orders = set()
        self.pending_signature = None
        self.auto_cancel = False
        self.cancel_requested = set()
        self.trades = []      # 成交记录
        self.positions = {}   # 持仓
        self._running = False

    def start(self, timeout=60):
        r = self.api.start()
        logger.info(f"YDApi.start() = {r}")
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
        logger.info("REAL_API_READY account=%s data_source=YDApi", mask_account(self.account))

    def get_real_instrument_data(self, instrument):
        info = self.api.get_instrument(instrument)
        if info is None:
            raise ValueError(f"YDApi.get_instrument 未找到合约: {instrument}")
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
        logger.info("REAL_INSTRUMENT_DATA %s", json.dumps(result, ensure_ascii=False, default=str))
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
            raise ValueError(f"委托数量 {volume} 小于真实 API 下限 {minimum}")
        if maximum and volume > maximum:
            raise ValueError(f"委托数量 {volume} 超过真实 API 上限 {maximum}")
        if order_type != 2:
            price = Decimal(str(params["price"]))
            tick = Decimal(str(getattr(info, "tick", 0)))
            if tick > 0 and price % tick != 0:
                raise ValueError(f"价格 {price} 不是真实 API Tick {tick} 的整数倍")
            if market:
                upper = getattr(market, "upper_limit_price", None)
                lower = getattr(market, "lower_limit_price", None)
                if upper is not None and price > Decimal(str(upper)):
                    raise ValueError(f"价格 {price} 高于真实涨停价 {upper}")
                if lower is not None and price < Decimal(str(lower)):
                    raise ValueError(f"价格 {price} 低于真实跌停价 {lower}")

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
        logger.info("REAL_ORDER_CHECK_REQUEST %s", json.dumps(params, ensure_ascii=False))
        result = self.api.insert_order(**params, checked=2)
        logger.info("REAL_ORDER_CHECK_RETURN result=%s", result)
        return result

    def send_order(self, auto_cancel=False, **params):
        self.pending_signature = (
            params["instrument"], params["action"], params["open_close"],
            params["volume"], float(params["price"]), params["type"], params["hedge"]
        )
        self.auto_cancel = auto_cancel
        logger.warning("REAL_ORDER_SEND_REQUEST %s", json.dumps(params, ensure_ascii=False))
        result = self.api.insert_order(**params, checked=1)
        logger.warning("REAL_ORDER_SEND_RETURN result=%s", result)
        return result

    def cancel_order(self, order):
        key = self._order_key(order)
        exchange = getattr(order, "exchange", "")
        order_group = int(getattr(order, "order_group", 0))
        order_ref = int(getattr(order, "order_ref", 0))
        order_sysid = getattr(order, "order_sysid", -1)
        logger.warning(
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

    # ---------- 内部回调处理 ----------
    def _on_login(self, error, maxorderref, ismonitor):
        if error == 0:
            logger.info(f"登录成功，max_order_ref={maxorderref}, is_admin={ismonitor}")
        else:
            logger.error(f"登录失败: {get_error_msg(error)}")

    def _on_caughtup(self):
        logger.info("历史数据同步完成")

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
            msg += f" [错误:{get_error_msg(o.errno)}]"
        if o.status == 2 and o.cancel_time:
            msg += f" 撤单时间:{o.cancel_time}"
        source = "LIVE" if self.listener.has_caughtup else "HISTORY"
        logger.info("REAL_ORDER_CALLBACK source=%s key=%s %s", source, key, msg)

        if self.auto_cancel and key in self.owned_orders and o.status == 1 and key not in self.cancel_requested:
            self.cancel_requested.add(key)
            logger.warning("真实订单已报，按 --auto-cancel 明确指令触发撤单 key=%s", key)
            self.cancel_order(o)

    def _on_trade(self, t):
        self.trades.append(t)
        direction = f"{'买' if t.action==0 else '卖'}{'开' if t.open_close==0 else '平'}"
        logger.info(f"REAL_TRADE_CALLBACK 成交 {t.instrument} {direction} 价:{t.price} 量:{t.volume} "
                    f"手续费:{t.commission:.2f} 时间:{t.time}")

    def get_order(self, account, order_group, order_ref):
        return self.orders.get((account, order_group, order_ref))

    def get_all_orders(self):
        return list(self.orders.values())

    def get_trades(self):
        return self.trades

    def stop(self):
        self._running = False
        logger.info("交易系统停止")

# ---------- 测试入口 ----------
def parse_args():
    parser = argparse.ArgumentParser(description="易达真实 Python API 人工测试")
    parser.add_argument("--account-config", default=str(PROJECT_ROOT / "config" / "account.json"))
    # 原生 yd.dll 对包含中文的绝对配置路径兼容性差；保持与官方示例一致，使用相对路径。
    parser.add_argument("--api-config", default="config/ydClient.ini")
    parser.add_argument("--startup-timeout", type=int, default=60)
    parser.add_argument("--wait-seconds", type=int, default=0, help="0 表示持续运行到 Ctrl+C")

    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check-only", action="store_true", help="调用 checked=2，只校验而不报至交易所")
    mode.add_argument("--send", action="store_true", help="调用真实 insert_order，可能产生真实委托")

    parser.add_argument("--instrument")
    parser.add_argument("--action", type=int, choices=(0, 1))
    parser.add_argument("--open-close", type=int, choices=(0, 1, 3, 4))
    parser.add_argument("--volume", type=int)
    parser.add_argument("--price", type=float)
    parser.add_argument("--order-type", type=int, choices=(0, 1, 2, 3))
    parser.add_argument("--hedge", type=int, choices=(1, 2, 3))
    parser.add_argument("--confirm-account", default="")
    parser.add_argument("--auto-cancel", action="store_true", help="仅撤销本次程序识别到的已报订单")
    return parser.parse_args()

def require_manual_order_input(args):
    names = ("instrument", "action", "open_close", "volume", "price", "order_type", "hedge")
    missing = [name for name in names if getattr(args, name) is None]
    if missing:
        flags = ", ".join("--" + name.replace("_", "-") for name in missing)
        raise ValueError(f"订单参数必须由测试人员明确提供，缺少: {flags}")

def main():
    args = parse_args()
    config = load_json(args.account_config)
    trader = Trader(config["name"], config["password"], args.api_config)
    try:
        trader.start(args.startup_timeout)

        if args.check_only or args.send:
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
            logger.info("TEST_INPUT source=OPERATOR %s", json.dumps(params, ensure_ascii=False))
            if args.check_only:
                logger.info("TEST_MODE CHECK_ONLY data_source=YDApi")
                trader.check_order(**params)
            else:
                if args.confirm_account != config["name"]:
                    raise ValueError("--confirm-account 必须与本地账号配置一致")
                logger.warning("TEST_MODE REAL_ORDER data_source=YDApi auto_cancel=%s", args.auto_cancel)
                trader.send_order(auto_cancel=args.auto_cancel, **params)
        elif args.instrument:
            logger.info("TEST_MODE INSPECT_INSTRUMENT data_source=YDApi")
            trader.get_real_instrument_data(args.instrument)
        else:
            logger.info("TEST_MODE CONNECT_ONLY：仅登录并接收真实 API 数据，不报单")

        if args.wait_seconds > 0:
            time.sleep(args.wait_seconds)
        else:
            while True:
                time.sleep(1)
    except KeyboardInterrupt:
        logger.info("收到 Ctrl+C，结束人工测试")
    finally:
        trader.stop()

if __name__ == "__main__":
    main()
