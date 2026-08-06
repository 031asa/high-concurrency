import os
import logging
import time
import json
import csv

from pyyd import *

# ---------- 日志配置 ----------
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('./logs/trader.log'),
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
def load_error_dict(path="./error_code.csv"):
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
        # 可动态绑定的回调
        self.on_login = None
        self.on_order = None
        self.on_trade = None
        self.on_caughtup = None

    def login(self, error, maxorderref, ismonitor):
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

        self.orders = {}      # local_id -> order
        self.trades = []      # 成交记录
        self.positions = {}   # 持仓
        self._running = False

    def start(self):
        r = self.api.start()
        logger.info(f"YDApi.start() = {r}")
        while not self.listener.has_caughtup:
            time.sleep(0.2)
        self._running = True
        logger.info("交易系统已就绪")

    def send_order(self, instrument, action, open_close, volume, price=0.0, order_type=0, hedge=1):
        params = {
            "instrument": instrument,
            "action": action,
            "open_close": open_close,
            "volume": volume,
            "price": price,
            "type": order_type,
            "hedge": hedge,
        }
        logger.info(f"发送订单: {params}")
        self.api.insert_order(**params)

    def cancel_order(self, local_id):
        logger.info(f"撤单 local_id={local_id}")
        order_sysid = self.orders.get(local_id).order_sysid
        self.api.cancel_order(exchange="CFFEX", order_sysid=order_sysid)
        # self.api.cancel_order(exchange="CFFEX", order_ref=local_id, order_group=-1)

    # ---------- 内部回调处理 ----------
    def _on_login(self, error, maxorderref, ismonitor):
        if error == 0:
            logger.info(f"登录成功，max_order_ref={maxorderref}, is_admin={ismonitor}")
        else:
            logger.error(f"登录失败: {get_error_msg(error)}")

    def _on_caughtup(self):
        logger.info("历史数据同步完成")

    def _on_order(self, o):
        self.orders[o.order_localid] = o
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
        logger.info(msg)

        # 自动撤单测试（可按需取消注释）
        if o.status == 1 and o.price == 4400.0:
            logger.info("触发自动撤单")
            self.cancel_order(o.order_localid)
            # self.cancel_order(o.order_sysid)

    def _on_trade(self, t):
        self.trades.append(t)
        direction = f"{'买' if t.action==0 else '卖'}{'开' if t.open_close==0 else '平'}"
        logger.info(f"成交 {t.instrument} {direction} 价:{t.price} 量:{t.volume} "
                    f"手续费:{t.commission:.2f} 时间:{t.time}")

    def get_order(self, local_id):
        return self.orders.get(local_id)

    def get_all_orders(self):
        return list(self.orders.values())

    def get_trades(self):
        return self.trades

    def stop(self):
        self._running = False
        logger.info("交易系统停止")

# ---------- 测试入口 ----------
def main():
    config = load_json("./config/account.json")
    trader = Trader(config["name"], config["password"], "./config/ydClient.ini")
    trader.start()

    instrument = "IF2609"
    print("测试：发送一个不会成交的限价单，观察回调")
    trader.send_order(instrument, action=0, open_close=0, volume=1, price=4400.0, order_type=0)

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        trader.stop()

if __name__ == "__main__":
    main()
