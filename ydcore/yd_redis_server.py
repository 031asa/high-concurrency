#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
易达 YD API 对接 Redis 的下单服务
参考 QMT server.py 设计，实现通过 Redis 队列接收交易指令并回报执行结果。
"""

import os
import sys
import json
import time
import threading
import traceback
from decimal import Decimal
from pathlib import Path
from ydcore.runtime_diagnostics import RuntimeDiagnostics

# 导入现有 order.py 中的核心组件
PROJECT_ROOT = Path(__file__).resolve().parents[1]
from ydcore.trading import Trader, Listener, get_error_msg, mask_account, load_json, PAUSE_FILE, strategy_execution_allowed


# ==================== Redis 配置 ====================
REDIS_HOST = os.environ.get("REDIS_HOST", "127.0.0.1")
REDIS_PORT = int(os.environ.get("REDIS_PORT", 6379))
REDIS_DB = int(os.environ.get("REDIS_DB", 0))

# ==================== Redis 客户端 ====================
class YdRedisClient:
    """管理易达交易服务的 Redis 队列与映射"""

    def __init__(self, account_id, host=REDIS_HOST, port=REDIS_PORT, db=REDIS_DB):
        import redis
        self.r = redis.Redis(host=host, port=port, db=db, decode_responses=True)
        self.account_id = account_id
        self.order_queue = f"order_queue:{account_id}"      # 策略 → 本服务
        self.callback_queue = f"callback_queue:{account_id}"  # 本服务 → 策略
        self.order_map_key = f"order_map:{account_id}"      # local_id → 订单信息 (Hash)

    def fetch_order(self, timeout=1):
        """阻塞式获取一条指令，超时返回 None"""
        item = self.r.brpop(self.order_queue, timeout=timeout)
        if item is None:
            return None
        _, order_json = item
        try:
            return json.loads(order_json)
        except json.JSONDecodeError:
            return None

    def push_callback(self, callback_type, data):
        """推送回报到回调队列"""
        msg = {"type": callback_type, "data": data}
        self.r.lpush(self.callback_queue, json.dumps(msg, default=str))

    def save_order_mapping(self, local_id, info):
        """保存 local_id → {order_group, order_ref, order_sysid, ...} 到 Redis Hash"""
        self.r.hset(self.order_map_key, local_id, json.dumps(info))

    def get_order_mapping(self, local_id):
        """查询 local_id 对应的订单信息"""
        raw = self.r.hget(self.order_map_key, local_id)
        if raw:
            try:
                return json.loads(raw)
            except:
                return None
        return None

    def delete_order_mapping(self, local_id):
        """删除映射（可选）"""
        self.r.hdel(self.order_map_key, local_id)

# ==================== 订单对象转字典 ====================
def yd_order_to_dict(order, local_id=None):
    """将易达 Order 对象转为可 JSON 序列化的字典"""
    data = {
        "account": getattr(order, "account", ""),
        "instrument": getattr(order, "instrument", ""),
        "exchange": getattr(order, "exchange", ""),
        "action": getattr(order, "action", 0),               # 0买 1卖
        "open_close": getattr(order, "open_close", 0),       # 0开 1平
        "volume": getattr(order, "volume", 0),
        "price": float(getattr(order, "price", 0)),
        "type": getattr(order, "type", 0),
        "hedge": getattr(order, "hedge", 1),
        "status": getattr(order, "status", 0),               # 0已提交 1已报 2已撤 3全成 4拒单
        "order_ref": getattr(order, "order_ref", 0),
        "order_group": getattr(order, "order_group", 0),
        "order_sysid": getattr(order, "order_sysid", -1),
        "trade_volume": getattr(order, "trade", 0),          # 已成数量
        "errno": getattr(order, "errno", 0),
        "error_msg": get_error_msg(getattr(order, "errno", 0)) if getattr(order, "errno", 0) else "",
        "time": getattr(order, "time", ""),
        "cancel_time": getattr(order, "cancel_time", ""),
        "local_id": local_id or "",                          # 策略侧自定义 ID
    }
    return data

def yd_trade_to_dict(trade, local_id=None):
    """将易达 Trade 对象转为字典"""
    data = {
        "account": getattr(trade, "account", ""),
        "instrument": getattr(trade, "instrument", ""),
        "exchange": getattr(trade, "exchange", ""),
        "action": getattr(trade, "action", 0),
        "open_close": getattr(trade, "open_close", 0),
        "volume": getattr(trade, "volume", 0),
        "price": float(getattr(trade, "price", 0)),
        "commission": float(getattr(trade, "commission", 0)),
        "trade_time": getattr(trade, "time", ""),
        "trade_id": getattr(trade, "trade_id"),
        "order_ref": getattr(trade, "order_ref", 0),
        "order_group": getattr(trade, "order_group", 0),
        "order_sysid": getattr(trade, "order_sysid", -1),
        "local_id": local_id or "",
    }
    return data

# ==================== 交易服务主类 ====================
class YdRedisTraderService:
    def __init__(self, account_config_path=None, api_config_path=None):
        # 加载账户配置
        if account_config_path is None:
            account_config_path = PROJECT_ROOT / "config" / "account.json"
        if api_config_path is None:
            api_config_path = "config/ydClient.ini"

        config = load_json(account_config_path)
        self.account_id = str(config["name"])          # 易达账号
        self.password = config["password"]
        self.api_config = api_config_path

        # 初始化 Trader（现有类，已包含日志、订单管理、验证等）
        self.trader = Trader(
            account=self.account_id,
            password=self.password,
            ini_path=self.api_config,
        )

        # 初始化 Redis 客户端
        self.redis_client = YdRedisClient(self.account_id)

        # local_id → (order_group, order_ref) 映射（进程内缓存）
        self.local_order_map = {}   # local_id -> {"order_group": int, "order_ref": int}
        self._map_lock = threading.Lock()

        # 绑定回调：在原有回调基础上增加 Redis 推送
        self._wrap_callbacks()

        # 启动标志
        self._running = False

    def _wrap_callbacks(self):
        """包装 Trader 的监听器，在原有处理基础上推送 Redis"""
        original_on_order = self.trader.listener.on_order
        original_on_trade = self.trader.listener.on_trade
        original_on_response = self.trader.listener.on_response

        def on_order_wrapper(order):
            # 原有处理
            original_on_order(order)

            # 查找 local_id
            local_id = self._find_local_id_by_order(order)

            # 更新映射，补充 exchange 和 order_sysid
            if local_id:
                with self._map_lock:
                    info = self.local_order_map.get(local_id, {})
                    info["exchange"] = getattr(order, "exchange", "")
                    info["order_sysid"] = getattr(order, "order_sysid", -1)
                    self.local_order_map[local_id] = info
                    # 持久化到 Redis
                    self.redis_client.save_order_mapping(local_id, info)

            print(f"[YdRedisTraderService] on_order_wrapper: {order}")

            # 推送回报
            data = yd_order_to_dict(order, local_id)
            self.redis_client.push_callback("on_order", data)

            if getattr(order, "errno", 0) != 0:
                self.redis_client.push_callback("on_order_error", data)

        def on_trade_wrapper(trade):
            original_on_trade(trade)
            local_id = self._find_local_id_by_order(trade)

            print(f"[YdRedisTraderService] on_trade_wrapper: {trade}")
            data = yd_trade_to_dict(trade, local_id)
            self.redis_client.push_callback("on_trade", data)

        def on_response_wrapper(errorno, request_type, request_id):
            original_on_response(errorno, request_type, request_id)
            # 推送 API 响应（可用于交易权限变更等）
            self.redis_client.push_callback("on_response", {
                "errorno": errorno,
                "message": get_error_msg(errorno),
                "request_type": request_type,
                "request_id": request_id,
            })

        self.trader.listener.on_order = on_order_wrapper
        self.trader.listener.on_trade = on_trade_wrapper
        self.trader.listener.on_response = on_response_wrapper

    def _find_local_id_by_order(self, order):
        """根据 order_group + order_ref 查找 local_id"""
        key = (int(getattr(order, "order_group", 0)), int(getattr(order, "order_ref", 0)))
        with self._map_lock:
            for local_id, info in self.local_order_map.items():
                if info["order_group"] == key[0] and info["order_ref"] == key[1]:
                    return local_id
        return None

    def start(self, startup_timeout=60):
        """启动易达 API 并同步历史数据"""
        self.trader.start(startup_timeout)
        self._running = True
        print(f"易达交易服务已启动，账号：{mask_account(self.account_id)}")

    def stop(self):
        self._running = False
        self.trader.stop()

    def _push_error(self, msg, error):
        """推送错误回报到 Redis"""
        self.redis_client.push_callback("on_order_error", {
            "req_id": msg.get("req_id"),
            "local_id": msg.get("local_id", ""),
            "error": str(error),
        })

    # ==================== 处理 Redis 指令 ====================
    def process_order_message(self, msg):
        """处理一条订单/撤单指令"""
        msg_type = msg.get("type")
        if msg_type == "order":
            self._handle_order(msg)
        elif msg_type == "cancel":
            self._handle_cancel(msg)
        elif msg_type == "cancel_all":
            self._handle_cancel_all(msg)
        else:
            print(f"未知消息类型: {msg_type}")

    def _handle_order(self, msg):
        """处理普通下单"""
        print(f"[YdRedisTraderService] _handle_order origin msg: {msg}")

        local_id = msg.get("local_id")
        if not local_id:
            self.redis_client.push_callback("on_order_error", {
                "req_id": msg.get("req_id"),
                "error": "local_id is required",
            })
            return

        # 订单格式转换为易达的订单格式
        msg["instrument"] = msg["Contract"].split(".")[0]
        msg["action"] = 0 if msg["direction"] == 1 else 1
        msg["open_close"] = 0 if msg["open_or_close"] == 1 else 1
        if msg["price"] is None:
            msg["price"] = 0
        msg["hedge"] = msg.get("hedge", 1)
        msg["type"] = 0 if msg["price_type"] == "limit" else 2

        print(f"[YdRedisTraderService] _handle_order: {msg}")

        # 解析易达下单参数（直接从消息中获取）
        required_fields = ["instrument", "action", "open_close", "volume", "price", "type", "hedge"]
        missing = [f for f in required_fields if f not in msg]
        if missing:
            err = f"缺少必要字段: {missing}"
            self.redis_client.push_callback("on_order_error", {
                "req_id": msg.get("req_id"),
                "error": err,
            })
            return

        # 参数校验（使用 Trader 的验证方法）
        try:
            params = self.trader.order_params(
                instrument=msg["instrument"],
                action=msg["action"],
                open_close=msg["open_close"],
                volume=msg["volume"],
                price=msg["price"],
                order_type=msg["type"],
                hedge=msg["hedge"],
            )
        except Exception as e:
            self.redis_client.push_callback("on_order_error", {
                "req_id": msg.get("req_id"),
                "local_id": local_id,
                "error": str(e),
            })
            return

        # 检查暂停标志（复用原有逻辑）
        if PAUSE_FILE.exists():
            err = f"策略已暂停，拒绝下单（{PAUSE_FILE}）"
            self.redis_client.push_callback("on_order_error", {
                "req_id": msg.get("req_id"),
                "local_id": local_id,
                "error": err,
            })
            return

        # 获取 order_ref
        order_ref = self.trader.api.next_order_ref()
        order_group = 0
        send_params = dict(params, order_ref=order_ref)

        # 记录映射
        with self._map_lock:
            self.local_order_map[local_id] = {
                "order_group": order_group,
                "order_ref": order_ref,
            }
            # 保存到 Redis 持久化
            self.redis_client.save_order_mapping(local_id, {
                "order_group": order_group,
                "order_ref": order_ref,
                "account": self.account_id,
            })

        # 发送真实订单
        try:
            result = self.trader.api.insert_order(**send_params, checked=0)
            if result is not True:
                raise RuntimeError("insert_order 未接受请求")
        except Exception as e:
            # 发送失败，清理映射
            with self._map_lock:
                self.local_order_map.pop(local_id, None)
            self.redis_client.delete_order_mapping(local_id)
            self.redis_client.push_callback("on_order_error", {
                "req_id": msg.get("req_id"),
                "local_id": local_id,
                "error": str(e),
            })
            return

        # 可选：记录日志
        print(f"下单已受理: local_id={local_id}, order_ref={order_ref}, instrument={msg['instrument']}")

        # 推送受理回报（可选）
        self.redis_client.push_callback("on_order", {
            "local_id": local_id,
            "order_ref": order_ref,
            "order_group": order_group,
            "status": 0,   # 已提交
            "instrument": msg["instrument"],
            "action": msg["action"],
            "open_close": msg["open_close"],
            "volume": msg["volume"],
            "price": msg["price"],
            "type": msg["type"],
            "hedge": msg["hedge"],
            "req_id": msg.get("req_id"),
        })

    def _handle_cancel(self, msg):
        local_id = msg.get("local_id")
        if not local_id:
            self._push_error(msg, "local_id is required")
            return

        # 从本地缓存或 Redis 获取映射
        with self._map_lock:
            info = self.local_order_map.get(local_id)
        if not info:
            info = self.redis_client.get_order_mapping(local_id)
            if info:
                with self._map_lock:
                    self.local_order_map[local_id] = info

        if not info:
            self._push_error(msg, f"未找到 local_id={local_id} 的订单映射")
            return

        order_group = info.get("order_group", 0)
        order_ref = info.get("order_ref", 0)
        exchange = info.get("exchange", "")
        order_sysid = info.get("order_sysid", -1)

        # 如果缺少必要信息，尝试用 get_order 查询（尽量避免 find_orders）
        if not exchange or order_sysid == -1:
            try:
                ord_obj = self.trader.api.get_order(
                    order_ref=order_ref,
                    order_group=order_group,
                    account=self.account_id
                )
                if ord_obj:
                    exchange = getattr(ord_obj, "exchange", "")
                    order_sysid = getattr(ord_obj, "order_sysid", -1)
                    # 更新映射
                    info["exchange"] = exchange
                    info["order_sysid"] = order_sysid
                    with self._map_lock:
                        self.local_order_map[local_id] = info
                    self.redis_client.save_order_mapping(local_id, info)
            except Exception as e:
                self._push_error(msg, f"查询订单失败: {e}")
                return

        if not exchange:
            self._push_error(msg, "订单交易所信息缺失，无法撤单")
            return

        try:
            if order_sysid and order_sysid != -1:
                # 优先使用 order_sysid 撤单，更安全
                result = self.trader.api.cancel_order(
                    exchange=exchange,
                    order_sysid=order_sysid,
                    account=self.account_id,
                )
            else:
                result = self.trader.api.cancel_order(
                    exchange=exchange,
                    order_sysid=-1,
                    order_ref=order_ref,
                    order_group=order_group,
                    account=self.account_id,
                )
            if result is True:
                self.redis_client.push_callback("on_order", {
                    "local_id": local_id,
                    "order_status": "CANCEL_REQUESTED",
                    "req_id": msg.get("req_id"),
                })
            else:
                self._push_error(msg, "cancel_order 未接受请求")
        except Exception as e:
            self._push_error(msg, str(e))

    def _handle_cancel_all(self, msg):
        """处理全撤指令"""
        try:
            self.trader.batch_cancel_pending(limit=0)
            self.redis_client.push_callback("on_order", {
                "order_status": "CANCEL_ALL_SENT",
                "req_id": msg.get("req_id"),
            })
        except Exception as e:
            self.redis_client.push_callback("on_order_error", {
                "req_id": msg.get("req_id"),
                "error": str(e),
            })

    def _handle_query_positions(self, msg):
        positions = self.get_positions()
        self.redis_client.push_callback("on_positions", {
            "req_id": msg.get("req_id"),
            "positions": positions or [],
        })

    # ==================== 持仓更新 ====================
    def update_positions_to_redis(self):
        """查询持仓并写入 Redis 键 positions:{account_id}"""
        if self.redis_client is None:
            return
        positions = self.get_positions()
        if positions is None:
            return
        key = f"positions:{self.account_id}"
        # 直接保存列表，与 QMT 版本格式一致
        self.redis_client.r.set(key, json.dumps(positions))
        # print(f"持仓已更新，共 {len(positions)} 条，写入键 {key}")

    def get_positions(self):
        """
        获取当前账户持仓，返回 QMT 风格字典列表。
        每个字典包含：Contract, direction, yesterday_position, today_position
        """
        # print([m for m in dir(self.trader.api) if 'position' in m.lower() or 'sync' in m.lower()])
        positions = []
        try:
            raw = self.trader.api.find_positions(account=self.account_id)
            # print(f"[DEBUG] 获取到 {len(raw)} 条原始持仓")
            for pos in raw:
                # print("[DEBUG] --- 持仓对象字段 ---")
                # for attr in dir(pos):
                #     if attr.startswith('_'):
                #         continue
                #     try:
                #         value = getattr(pos, attr)
                #         if not callable(value):
                #             print(f"{attr}: {value}")
                #     except Exception as e:
                #         print(f"{attr}: 获取失败 ({e})")
                # print("[DEBUG] --- 结束 ---")
                instrument = getattr(pos, "instrument", "")
                long_short = getattr(pos, "long_short", 0)  # 1=多，2=空
                position = int(getattr(pos, "position", 0))  # 总持仓
                yd_position = int(getattr(pos, "ydPosition", 0))  # 昨仓

                # 获取交易所（对象中无 exchange 字段，需通过合约信息查询）
                exchange = ""
                try:
                    info = self.trader.api.get_instrument(instrument)
                    exchange = getattr(info, "exchange", "")
                except:
                    pass

                contract = f"{instrument}.{exchange}" if instrument and exchange else instrument

                if position <= 0:
                    continue  # 无持仓跳过

                # 判断方向
                if long_short == 1:
                    direction = 1   # 多
                elif long_short == 2:
                    direction = -1  # 空
                else:
                    # 如果无法判断，可根据 position 正负号推断（但这里 position 总是正数）
                    direction = 1

                today_position = position - yd_position

                positions.append({
                    "Contract": contract,
                    "direction": direction,
                    "yesterday_position": yd_position,
                    "today_position": today_position,
                })
            return positions
        except Exception as e:
            print(f"获取持仓失败: {e}")
            traceback.print_exc()
            return None

    # ==================== 主循环 ====================
    def run_forever(self):
        """持续从 Redis 队列消费消息"""
        threading.Thread(target=self._position_updater, daemon=True).start()

        print("开始监听 Redis 订单队列:", self.redis_client.order_queue)
        while self._running:
            try:
                msg = self.redis_client.fetch_order(timeout=1)
                diagnostics = getattr(self, "_diagnostics", None)
                if diagnostics is not None:
                    diagnostics.tick()
                if msg:
                    self.process_order_message(msg)
            except KeyboardInterrupt:
                break
            except Exception as e:
                diagnostics = getattr(self, "_diagnostics", None)
                if diagnostics is not None:
                    diagnostics.error("redis_loop_error", e)
                time.sleep(1)

    def _position_updater(self):
        while self._running:
            try:
                self.update_positions_to_redis()
            except Exception as e:
                diagnostics = getattr(self, "_diagnostics", None)
                if diagnostics is not None:
                    diagnostics.error("position_update_error", e)
            time.sleep(5)

# ==================== 入口 ====================
def main(argv=None):
    # 可选：从命令行覆盖配置路径
    import argparse
    parser = argparse.ArgumentParser(description="易达 Redis 下单服务")
    parser.add_argument("--account-config", default=str(PROJECT_ROOT / "config" / "account.json"))
    parser.add_argument("--api-config", default="config/ydClient.ini")
    parser.add_argument("--startup-timeout", type=int, default=60)
    parser.add_argument("--heartbeat-seconds", type=int, default=60,
                        help="process diagnostic interval (default: 60; not a broker-health check)")
    args = parser.parse_args(argv)
    if args.heartbeat_seconds < 1:
        parser.error("--heartbeat-seconds must be positive")
    try:
        with RuntimeDiagnostics(args.heartbeat_seconds) as diagnostics:
            service = None
            try:
                service = YdRedisTraderService(
                    account_config_path=args.account_config,
                    api_config_path=args.api_config,
                )
                service._diagnostics = diagnostics
                service.start(args.startup_timeout)
                diagnostics.phase = "running"
                diagnostics.tick()
                diagnostics.emit("ready")
                service.run_forever()
            finally:
                diagnostics.phase = "stopping"
                diagnostics.emit("stopping")
                if service is not None:
                    pending = sys.exc_info()[0] is not None
                    try:
                        service.stop()
                    except Exception as error:
                        diagnostics.error("cleanup_error", error)
                        if not pending:
                            raise
    except Exception:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
