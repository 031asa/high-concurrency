"""Offline contract checks. No broker SDK or external Redis connections."""
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace as NS

import pytest

from ydcore import trading
from ydcore import yd_redis_server as service


class MemoryRedis:
    def __init__(self, **kwargs):
        self.options = kwargs
        self.values = {}
        self.lists = {}
        self.hashes = {}

    def lpush(self, key, value):
        self.lists.setdefault(key, []).insert(0, value)

    def brpop(self, key, timeout=1):
        values = self.lists.get(key, [])
        return (key, values.pop()) if values else None

    def hset(self, key, field, value):
        self.hashes.setdefault(key, {})[field] = value

    def hget(self, key, field):
        return self.hashes.get(key, {}).get(field)

    def hdel(self, key, field):
        self.hashes.get(key, {}).pop(field, None)

    def set(self, key, value):
        self.values[key] = value


class Broker:
    def __init__(self, listener, *args):
        self.listener = listener
        self.requests = []
        self.accept = True
        self.ref = 100
        self.positions = []

    def next_order_ref(self):
        self.ref += 1
        return self.ref

    def get_instrument(self, instrument):
        return NS(exchange='CFFEX', tick=1, min_limit_order_volume=1,
                  max_limit_order_volume=100, min_market_order_volume=1,
                  max_market_order_volume=100)

    def get_marketdata(self, instrument):
        return NS(upper_limit_price=1000, lower_limit_price=1)

    def insert_order(self, **params):
        self.requests.append(('order', params))
        return self.accept

    def cancel_order(self, **params):
        self.requests.append(('cancel', params))
        return self.accept

    def find_orders(self, **params):
        return []

    def find_positions(self, **params):
        assert params == {'account': 'offline-test'}
        return self.positions

    def get_order(self, **params):
        return NS(exchange='CFFEX', order_sysid=88)


@pytest.fixture
def gateway(monkeypatch, tmp_path):
    import redis
    monkeypatch.setattr(redis, 'Redis', MemoryRedis)
    monkeypatch.setattr(trading, 'create_ydapi', Broker)
    monkeypatch.setattr(service, 'PAUSE_FILE', tmp_path / 'pause')
    monkeypatch.setattr(trading, 'PAUSE_FILE', tmp_path / 'pause')
    account = tmp_path / 'account.json'
    account.write_text(json.dumps({'name': 'offline-test', 'password': 'test-only'}))
    result = service.YdRedisTraderService(account, 'unused.ini')
    result.trader.listener.has_caughtup = True
    return result


def order(**changes):
    msg = dict(type='order', local_id='L1', req_id='R1', Contract='IFTEST.CFFEX',
               direction=1, open_or_close=1, volume=2, price=100, price_type='limit')
    msg.update(changes)
    return msg


def callbacks(gateway):
    client = gateway.redis_client
    return [json.loads(item) for item in reversed(client.r.lists.get(client.callback_queue, []))]


def test_queue_names_fifo_and_mapping(gateway):
    client = gateway.redis_client
    assert client.order_queue == 'order_queue:offline-test'
    assert client.callback_queue == 'callback_queue:offline-test'
    assert client.order_map_key == 'order_map:offline-test'
    for index in (1, 2):
        client.r.lpush(client.order_queue, json.dumps({'index': index}))
    assert client.fetch_order() == {'index': 1}
    assert client.fetch_order() == {'index': 2}
    assert client.fetch_order() is None
    client.save_order_mapping('L', {'order_ref': 9})
    assert client.get_order_mapping('L') == {'order_ref': 9}
    client.delete_order_mapping('L')
    assert client.get_order_mapping('L') is None


@pytest.mark.parametrize('direction,opening,kind,price,expected', [
    (1, 1, 'limit', 100, (0, 0, 0, 100)),
    (-1, -1, 'market', None, (1, 1, 2, 0)),
])
def test_order_conversion_and_accepted_payload(gateway, direction, opening, kind, price, expected):
    gateway.process_order_message(order(direction=direction, open_or_close=opening,
                                        price_type=kind, price=price))
    _, params = gateway.trader.api.requests[0]
    assert (params['action'], params['open_close'], params['type'], params['price']) == expected
    assert params['checked'] == 0
    assert params['instrument'] == 'IFTEST'
    assert gateway.local_order_map['L1'] == {'order_group': 0, 'order_ref': 101}
    payload = callbacks(gateway)[0]
    assert payload == {'type': 'on_order', 'data': dict(local_id='L1', order_ref=101,
        order_group=0, status=0, instrument='IFTEST', action=expected[0], open_close=expected[1],
        volume=2, price=expected[3], type=expected[2], hedge=1, req_id='R1')}


@pytest.mark.parametrize('reason', ['pause', 'reject', 'invalid_price', 'missing_local'])
def test_order_failure_and_no_false_acceptance(gateway, reason):
    msg = order()
    if reason == 'pause':
        service.PAUSE_FILE.touch()
    elif reason == 'reject':
        gateway.trader.api.accept = False
    elif reason == 'invalid_price':
        msg['price'] = 10000
    else:
        del msg['local_id']
    gateway.process_order_message(msg)
    assert callbacks(gateway)[-1]['type'] == 'on_order_error'
    assert gateway.local_order_map == {}
    assert gateway.redis_client.get_order_mapping('L1') is None
    if reason != 'reject':
        assert gateway.trader.api.requests == []


def quote_order(status=1, errno=0, trade=0):
    return NS(account='offline-test', instrument='IFTEST', exchange='CFFEX', action=0,
              open_close=0, volume=2, price=100, type=0, hedge=1, status=status,
              order_ref=101, order_group=0, order_sysid=88, trade=trade, errno=errno,
              time='10:00:00', cancel_time='', order_localid=7)


@pytest.mark.parametrize('status,filled', [(1, 0), (1, 1), (3, 2), (2, 1), (4, 0)])
def test_order_callback_status_and_original_core(gateway, status, filled):
    gateway.process_order_message(order())
    event = quote_order(status, 9 if status == 4 else 0, filled)
    gateway.trader.listener.order(event)
    messages = callbacks(gateway)
    assert messages[1] == {'type': 'on_order', 'data': service.yd_order_to_dict(event, 'L1')}
    assert messages[1]['data']['trade_volume'] == filled
    assert gateway.trader.orders[('offline-test', 0, 101)] is event
    assert gateway.redis_client.get_order_mapping('L1')['order_sysid'] == 88
    if status == 4:
        assert messages[2]['type'] == 'on_order_error'


def test_trade_and_response_forwarding(gateway):
    gateway.process_order_message(order())
    event = NS(account='offline-test', instrument='IFTEST', exchange='CFFEX', action=0,
               open_close=0, volume=1, price=100, commission=1.0, time='10:00:01',
               trade_id='T1', order_ref=101, order_group=0, order_sysid=88)
    gateway.trader.listener.trade(event)
    assert gateway.trader.trades == [event]
    assert callbacks(gateway)[1] == {'type': 'on_trade', 'data': service.yd_trade_to_dict(event, 'L1')}
    gateway.trader.listener.response(0, 3, 99)
    assert callbacks(gateway)[2]['data']['request_id'] == 99


@pytest.mark.parametrize('persisted', [False, True])
def test_cancel_recovers_mapping_and_requests_by_exchange_id(gateway, persisted):
    gateway.process_order_message(order())
    if persisted:
        gateway.local_order_map.clear()
    gateway.process_order_message({'type': 'cancel', 'local_id': 'L1', 'req_id': 'C1'})
    assert gateway.trader.api.requests[-1] == ('cancel', dict(exchange='CFFEX', order_sysid=88, account='offline-test'))
    assert callbacks(gateway)[-1]['data']['order_status'] == 'CANCEL_REQUESTED'


def test_cancel_missing_and_rejected(gateway):
    gateway.process_order_message({'type': 'cancel', 'local_id': 'missing'})
    assert callbacks(gateway)[-1]['type'] == 'on_order_error'
    gateway.process_order_message(order())
    gateway.trader.api.accept = False
    gateway.process_order_message({'type': 'cancel', 'local_id': 'L1'})
    assert callbacks(gateway)[-1]['type'] == 'on_order_error'


def test_cancel_all_delegates_without_changing_wire(gateway, monkeypatch):
    calls = []
    monkeypatch.setattr(gateway.trader, 'batch_cancel_pending', lambda limit: calls.append(limit))
    gateway.process_order_message({'type': 'cancel_all', 'req_id': 'C2'})
    assert calls == [0]
    assert callbacks(gateway)[-1] == {'type': 'on_order', 'data': {'order_status': 'CANCEL_ALL_SENT', 'req_id': 'C2'}}


def test_positions_schema_period_and_query_remains_unwired(gateway, monkeypatch):
    gateway.trader.api.positions = [NS(instrument='IFTEST', long_short=direction, position=3, ydPosition=1) for direction in (1, 2)]
    gateway._running = True
    sleeps = []
    def stop_after_tick(seconds):
        sleeps.append(seconds)
        gateway._running = False
    monkeypatch.setattr(service.time, 'sleep', stop_after_tick)
    gateway._position_updater()
    assert sleeps == [5]
    assert json.loads(gateway.redis_client.r.values['positions:offline-test']) == [
        dict(Contract='IFTEST.CFFEX', direction=d, yesterday_position=1, today_position=2) for d in (1, -1)]
    gateway.process_order_message({'type': 'query_positions', 'req_id': 'P1'})
    assert callbacks(gateway) == []


def test_main_preserves_config_arguments(gateway, monkeypatch):
    calls = []
    class StubService:
        def __init__(self, **kwargs): calls.append(kwargs)
        def start(self, timeout): calls.append(timeout)
        def run_forever(self): calls.append('run')
        def stop(self): pass
    monkeypatch.setattr(service, 'YdRedisTraderService', StubService)
    assert service.main(['--account-config', 'a', '--api-config', 'b', '--startup-timeout', '4']) == 0
    assert calls == [dict(account_config_path='a', api_config_path='b'), 4, 'run']


@pytest.mark.parametrize('args', [['main.py', 'order'], ['main.py', 'redis-trader'], ['main.py', 'yd-redis-server'], ['scripts/yd_redis_server.py']])
def test_help_never_connects_or_loads_sdk(tmp_path, args):
    root = Path(__file__).resolve().parents[1]
    (tmp_path / 'sitecustomize.py').write_text('import socket\ndef blocked(*a, **k): raise AssertionError("network attempted")\nsocket.socket.connect = blocked\n')
    (tmp_path / 'pyyd.py').write_text('raise AssertionError("native SDK loaded")\n')
    env = dict(os.environ, PYTHONPATH=str(tmp_path))
    run = subprocess.run([sys.executable, *args, '--help'], cwd=root, env=env, capture_output=True, text=True, timeout=15)
    assert run.returncode == 0, run.stderr
    assert '--account-config' in run.stdout


def test_new_order_ref_property_and_original_return(gateway):
    trader = gateway.trader
    assert trader.last_submitted_order_ref is None
    params = trader.order_params('IFTEST', 0, 0, 2, 100, 0, 1)
    assert trader.send_order('offline', **params) is True
    assert trader.last_submitted_order_ref == 101


def test_known_original_missing_field_and_restart_mapping_are_not_fixed(gateway):
    msg = order()
    del msg['Contract']
    with pytest.raises(KeyError):
        gateway.process_order_message(msg)
    assert callbacks(gateway) == []
    gateway.process_order_message(order())
    gateway.local_order_map.clear()
    assert gateway._find_local_id_by_order(quote_order()) is None
