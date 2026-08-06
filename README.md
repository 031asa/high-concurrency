# 易达 Python API 人工测试

本分支以用户提供的 `scripts/order.py` 为基线做最小增量修改。程序只调用压缩包内的真实 `pyyd/YDApi`，没有 FakeYDApi、模拟成交或内置合约、价格、Tick、涨跌停、委托数量等测试数据。

字段语义以 `yd_c++_api_programming_guide.pdf` 为准，Python 方法签名以 `docs/pyyd_api.txt` 和官方 wheel 为准。

## Git 审查

当前分支：

```text
codex/real-api-minimal
```

提交顺序：

```text
4a8a311  导入用户提供的 order.py 基线
72916e2  添加易达 Python 运行依赖和敏感配置忽略
850cd01  最小增量接入真实 pyyd 人工测试
```

查看相对用户基线的全部改动：

```powershell
git diff 4a8a311..HEAD -- scripts/order.py
```

查看每个提交：

```powershell
git log --oneline --decorate
git show 850cd01
```

## 环境准备

必须使用 CPython 3.9 64 位：

```powershell
python --version
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install .\vendor\wheels\pyyd-1.486.96.99-cp39-cp39-win_amd64.whl
```

本机私密文件：

```text
config/account.json
config/ydClient.ini
```

这两个文件已经被 `.gitignore` 排除。禁止提交、打包或发送账号、密码、服务器、AppID 和 AuthCode。

## 第一步：只连接，不报单

```powershell
.\.venv\Scripts\python.exe scripts\order.py --startup-timeout 60 --wait-seconds 2
```

该命令只调用 `YDApi.start()` 并等待真实 `login`、交易连接信息和 `caughtup` 回调。不会调用 `insert_order` 或 `cancel_order`。

成功日志必须包含：

```text
YDApi.start() = True
登录成功
历史数据同步完成
REAL_API_READY ... data_source=YDApi
TEST_MODE CONNECT_ONLY
```

## 第二步：查询真实合约数据

合约代码必须由测试人员从仿真客户端或业务测试清单中选择，程序不提供默认值：

```powershell
.\.venv\Scripts\python.exe scripts\order.py --instrument <实际合约代码> --wait-seconds 2
```

程序调用 `YDApi.get_instrument()` 和 `YDApi.get_marketdata()`，日志中的 `REAL_INSTRUMENT_DATA` 包含柜台返回的 Tick、委托量限制、涨跌停和当前行情。不存在的合约会直接失败。

## 第三步：只校验订单，不发送到交易所

所有参数必须由测试人员明确填写，不允许依赖脚本默认值：

```powershell
.\.venv\Scripts\python.exe scripts\order.py --check-only `
  --instrument <实际合约代码> `
  --action <0买或1卖> `
  --open-close <0开1平3平今4平昨> `
  --volume <数量> `
  --price <价格> `
  --order-type <0限价1FAK2市价3FOK> `
  --hedge <1投机2套利3套保> `
  --wait-seconds 2
```

程序先使用真实 `get_instrument/get_marketdata` 数据校验，再调用官方 `insert_order(..., checked=2)`。根据易达 Python API 文档，`checked=2` 只检查、不发送订单到交易所。

## 第四步：真实报单

只有负责人确认测试合约、方向、开平、数量、价格和交易时段后才能执行。真实报单必须增加 `--send` 并再次输入与本地配置完全一致的账号：

```powershell
.\.venv\Scripts\python.exe scripts\order.py --send `
  --confirm-account <本地测试账号> `
  --instrument <实际合约代码> `
  --action <方向> `
  --open-close <开平> `
  --volume <数量> `
  --price <价格> `
  --order-type <订单类型> `
  --hedge <投保类型>
```

如果测试目标是“订单已报后立即撤单”，在负责人确认后额外添加：

```text
--auto-cancel
```

程序只会撤销本次进程在 `caughtup` 后识别到的自有订单。撤单使用真实回报中的 `exchange/order_sysid`，不使用 `order_localid` 作为订单身份。

## 日志核对

正式日志：

```text
logs/trader.log
```

真实数据对应的固定标记：

```text
REAL_API_READY
REAL_INSTRUMENT_DATA
REAL_ORDER_CHECK_REQUEST
REAL_ORDER_CHECK_RETURN
REAL_ORDER_SEND_REQUEST
REAL_ORDER_CALLBACK
REAL_TRADE_CALLBACK
REAL_CANCEL_REQUEST
FAILED_CANCEL_CALLBACK
API_RESPONSE
```

只有实际收到真实 API 回调才会产生对应日志。脚本不会生成模拟的订单、成交、撤单或错误结果。
