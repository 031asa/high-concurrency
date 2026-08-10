# 易达 Python API 人工测试操作手册

如果你第一次接触 Python、API、Git 或交易回调，请先阅读 [小白技术指导手册.md](小白技术指导手册.md)，理解整体流程后再按本 README 执行命令。

## 1. 手册用途

本项目用于通过压缩包中的真实 `pyyd/YDApi` 测试易达交易接口，包括：

1. 真实账号连接和认证。
2. 接收历史数据并等待 `caughtup`。
3. 查询柜台返回的真实合约和行情参数。
4. 使用 `checked=2` 做报单前预检查；该结果不能代替正式测试。
5. 经负责人确认后把委托真实发送到仿真交易所，完成订单回报、成交回报和撤单测试。
6. 通过 `logs/trader.log` 留存真实 API 测试证据。

程序没有 FakeYDApi，不会生成模拟成交，也没有内置合约、价格、Tick、涨跌停、委托数量或错误回报。

字段语义以易达 C++ 手册 `yd_c++_api_programming_guide.pdf` 为准；Python 方法名称和参数以 [docs/pyyd_api.txt](docs/pyyd_api.txt) 为准。

## 2. 重要安全规则

执行任何命令前先记住：

- `CONNECT_ONLY` 只登录，不报单，可以优先执行。
- `--check-only` 使用 `checked=2`，只检查，不发送订单到交易所。
- `--send` 会调用真实 `insert_order`，可能产生真实委托、成交、资金和持仓变化。
- 每次真实 `--send` 必须提供脚本侧 `--strategy-id`；该字段只用于策略执行权限，不传入易达 API。
- `--auto-cancel` 只能与 `--send` 配合使用，会在识别到本次订单已报后发送真实撤单请求。
- 没有负责人确认时，禁止执行带 `--send` 的命令。
- 禁止把账号、密码、服务器地址、AppID、AuthCode 发到群聊或提交到 Git。
- 所有合约、方向、开平、数量、价格和订单类型必须由测试人员明确提供，不能照抄手册中的占位符。
- `--check-only` 只用于预检查；最终验收必须执行 `--send`，把订单发送到仿真交易所并取得真实回报。

尖括号内容，例如 `<实际合约代码>`，表示必须替换的占位符，不能原样输入。

## 3. 项目目录说明

```text
yd_trader_real_api
├── config
│   ├── account.example.json     账号配置示例，可提交
│   ├── account.json             本机真实账号配置，禁止提交
│   └── ydClient.ini             易达连接配置，禁止提交
├── docs
│   └── pyyd_api.txt             压缩包中的官方 Python API 说明
├── logs
│   └── trader.log               正式运行日志，禁止提交账号敏感信息
├── scripts
│   ├── monitor.py               独立报撤单监控进程，只监听不交易
│   └── order.py                 人工测试入口
├── vendor
│   ├── wheels                   官方 Python 3.9 x64 wheel
│   └── win64\yd.dll             易达 64 位运行库
├── .gitignore
└── README.md
```

`config/account.json`、`config/ydClient.ini`、`.venv` 和 `logs` 已被 `.gitignore` 排除。

## 4. 第一次环境安装

### 4.1 打开终端

进入 `yd_trader_real_api` 文件夹，在空白处按住 `Shift` 并点击鼠标右键，选择“在终端中打开”。

终端路径应类似：

```text
PS C:\...\yd_trader_real_api>
```

后续命令都必须在项目根目录执行，不能进入 `scripts` 文件夹后再运行。

### 4.2 检查 Python

```powershell
python --version
python -c "import sys; print(sys.version); print(sys.maxsize > 2**32)"
```

必须满足：

- Python 版本为 3.9.x。
- 第二条命令最后显示 `True`，表示 64 位。

其他 Python 版本不能安装本项目的 `cp39-win_amd64` wheel。

### 4.3 创建隔离环境

```powershell
python -m venv .venv
```

安装压缩包中的官方 `pyyd`：

```powershell
.\.venv\Scripts\python.exe -m pip install .\vendor\wheels\pyyd-1.486.96.99-cp39-cp39-win_amd64.whl
```

验证安装：

```powershell
.\.venv\Scripts\python.exe -c "import pyyd; print('pyyd import OK')"
```

成功时应显示：

```text
pyyd import OK
```

### 4.4 配置账号

复制：

```text
config/account.example.json
```

并重命名为：

```text
config/account.json
```

在本机填写测试账号和密码：

```json
{
  "name": "本机测试账号",
  "password": "本机测试密码"
}
```

不要把真实账号密码写进 `account.example.json`。

### 4.5 放置易达连接配置

把券商提供的 `ydClient.ini` 放入：

```text
config/ydClient.ini
```

至少应包含以下配置项：

```text
RunPosition
TradingServerIP
TradingServerPort
AppID
AuthCode
```

不要在 README、截图或 Git 中展示这些配置项的值。

### 4.6 检查敏感文件是否被 Git 忽略

```powershell
git check-ignore config/account.json
git check-ignore config/ydClient.ini
git status --short
```

前两条命令应分别输出对应文件路径；`git status` 不应显示这两个文件。

## 5. 查看命令帮助

```powershell
.\.venv\Scripts\python.exe scripts\order.py --help
```

如果中文乱码，可以先执行：

```powershell
chcp 65001
$env:PYTHONUTF8=1
```

正式判断以 UTF-8 编码的 `logs/trader.log` 为准，终端显示乱码不代表接口失败。

## 6. 测试一：只登录，不报单

这是首次测试必须执行的命令：

```powershell
.\.venv\Scripts\python.exe scripts\order.py --startup-timeout 60 --wait-seconds 2
```

程序流程：

```text
加载本机配置
  → 创建真实 YDApi
  → YDApi.start()
  → login 回调
  → 接收历史订单和成交
  → caughtup 回调
  → CONNECT_ONLY
  → 正常停止
```

这个模式不会调用：

```text
insert_order
cancel_order
```

成功日志应包含：

```text
YDApi.start() = True
登录成功
历史数据同步完成
REAL_API_READY ... data_source=YDApi
TEST_MODE CONNECT_ONLY
交易系统停止
```

还可能看到真实交易连接回调：

```text
EXCHANGE_CONNECTION
TRADING_SEGMENT
```

失败标志包括：

```text
登录失败
Failed to create ydAPI object
等待 caughtup 超时
Traceback
```

只有本步骤成功后才能继续查询合约。

## 7. 测试二：查询真实合约数据

先从快期仿真客户端或业务测试清单中选择实际合约代码，然后执行：

```powershell
.\.venv\Scripts\python.exe scripts\order.py `
  --instrument <实际合约代码> `
  --wait-seconds 2
```

PowerShell 中反引号 `` ` `` 必须位于每一行末尾，后面不能有空格。也可以把命令写在同一行。

程序实际调用：

```text
YDApi.get_instrument()
YDApi.get_marketdata()
```

成功日志标记：

```text
TEST_MODE INSPECT_INSTRUMENT data_source=YDApi
REAL_INSTRUMENT_DATA
```

`REAL_INSTRUMENT_DATA` 中的数据全部来自真实 API，包括：

- 最小变动价位 Tick。
- 市价单最小/最大数量。
- 限价单最小/最大数量。
- 涨停价和跌停价。
- 最新价、买价和卖价。

如果出现：

```text
YDApi.get_instrument 未找到合约
```

说明合约代码不存在、已到期或与当前柜台环境不匹配，不能继续报单。

## 8. 测试三：报单前预检查，不作为正式验收结果

执行前准备以下人工输入：

| 参数 | 允许值 | 含义 |
|---|---:|---|
| `--instrument` | 实际合约代码 | 必须来自仿真客户端或测试清单 |
| `--strategy-id` | 字母、数字、`_`、`.`、`-` | 脚本侧策略身份；正式 `--send` 必填 |
| `--action` | `0` / `1` | `0` 买，`1` 卖 |
| `--open-close` | `0` / `1` / `3` / `4` | 开仓、平仓、平今、平昨 |
| `--volume` | 正整数 | 委托数量 |
| `--price` | 数字 | 委托价格；市价单也必须由操作员明确输入 |
| `--order-type` | `0` / `1` / `2` / `3` | 限价、FAK、市价、FOK |
| `--hedge` | `1` / `2` / `3` | 投机、套利、套保 |

命令格式：

```powershell
.\.venv\Scripts\python.exe scripts\order.py --check-only `
  --instrument <实际合约代码> `
  --action <0或1> `
  --open-close <0或1或3或4> `
  --volume <人工确认的数量> `
  --price <人工确认的价格> `
  --order-type <0或1或2或3> `
  --hedge <1或2或3> `
  --wait-seconds 2
```

程序先使用真实 `get_instrument/get_marketdata` 数据检查数量、Tick 和涨跌停，再调用：

```python
insert_order(..., checked=2)
```

按照易达 Python API 文档，`checked=2` 表示只检查、不发送订单到交易所。

因此本步骤只能证明参数检查调用正常，不能证明交易所实际收到委托，也不能作为开仓、平仓或撤单测试的最终证据。预检查通过后仍必须执行下一节的 `--send`。

日志应包含：

```text
TEST_INPUT source=OPERATOR
TEST_MODE CHECK_ONLY data_source=YDApi
REAL_INSTRUMENT_DATA
REAL_ORDER_CHECK_REQUEST
REAL_ORDER_CHECK_RETURN
```

如果发生参数问题，程序应在调用真实报单前失败，例如：

```text
价格不是实际 Tick 的整数倍
价格高于真实涨停价
价格低于真实跌停价
委托数量超过真实 API 上限
```

## 9. 测试四：真实报单到仿真交易所（正式验收必做）

这一节会产生真实测试委托。必须同时满足：

1. 使用仿真环境，不是生产环境。
2. 负责人已经确认账号和交易权限。
3. 已经完成 CONNECT_ONLY。
4. 已经查询合约真实数据。
5. 同一组参数已经通过 `--check-only`。
6. 已确认方向、开平、数量、价格和订单类型。
7. 有人同时盯着快期委托、成交和持仓界面。

两张自评估表中的开仓、平仓、撤单、错误回报、报撤单计数和阈值等项目，都必须以本节产生的真实交易所回报为依据。只运行 `CONNECT_ONLY`、查询合约或 `--check-only` 均不能判定这些项目通过。

真实报单命令格式：

```powershell
.\.venv\Scripts\python.exe scripts\order.py --send `
  --confirm-account <本地测试账号> `
  --strategy-id <已确认策略ID> `
  --instrument <已确认合约> `
  --action <已确认方向> `
  --open-close <已确认开平> `
  --volume <已确认数量> `
  --price <已确认价格> `
  --order-type <已确认订单类型> `
  --hedge <已确认投保类型>
```

`--confirm-account` 必须与本机 `config/account.json` 中的账号完全一致，否则程序拒绝报单。`--strategy-id` 是本程序的策略身份，不是易达原生订单参数；脚本在调用 `YDApi.insert_order` 前检查该 ID 的执行权限。

策略权限保存在本机 `config/strategy_permissions.json`，该运行时文件已被 Git 忽略。脚本使用本次分配的 `order_ref` 将订单和成交回调关联回 `strategy_id`；历史订单或其他进程发出的订单因为易达回调没有该字段，会明确记录为 `strategy_id=UNKNOWN`，不会猜测归属。

发送前日志会出现醒目的：

```text
TEST_MODE REAL_ORDER
REAL_ORDER_SEND_REQUEST
```

发送后必须根据真实回调判断结果：

```text
REAL_ORDER_SEND_RETURN
REAL_ORDER_CALLBACK
REAL_TRADE_CALLBACK
API_RESPONSE
```

正式发送使用易达文档规定的 `checked=0`，即不在发送前做本地拦截，并通过 `next_order_ref()` 分配委托引用。这样资金不足、持仓不足等订单能够进入柜台/交易所回报链路。`insert_order` 返回 `True` 只表示请求已成功提交，不代表交易所已经接受或成交，最终结果必须看 `REAL_ORDER_CALLBACK` 中的状态和错误码。

如果出现：

```text
REAL_ORDER_SEND_RETURN ... result=False
REAL_ORDER_SEND_NOT_ACCEPTED
```

表示 API 本次没有接受发送请求，交易所没有收到订单，因此不能把它当作“持仓不足”等交易所回报。应检查连接状态、交易会话以及合约和订单类型后重新测试。

正式报单成功至少需要日志出现：

```text
REAL_ORDER_SEND_REQUEST
REAL_ORDER_SEND_RETURN checked=0 order_ref=<真实委托引用> result=True
REAL_ORDER_CALLBACK source=LIVE
```

开仓或平仓成交测试还必须出现：

```text
REAL_TRADE_CALLBACK
```

如果只有 `REAL_ORDER_CHECK_RETURN` 而没有 `REAL_ORDER_SEND_REQUEST`，说明订单没有发送到交易所，该项目不能计为通过。

## 10. 测试五：已报后自动撤单

只有明确要测试撤单时，才在真实报单命令最后增加：

```text
--auto-cancel
```

程序只会在以下条件全部满足时撤单：

- 已经收到 `caughtup`，不是历史订单回报。
- 订单字段与本次人工输入一致。
- 订单状态为已报。
- 本次进程尚未对该订单发过撤单。

撤单使用真实订单回报中的：

```text
account + order_group + order_ref
exchange + order_sysid
```

`order_localid` 仅作为查询辅助，不作为订单身份。

正常日志顺序：

```text
REAL_ORDER_SEND_REQUEST
REAL_ORDER_CALLBACK ... 状态:已报
REAL_CANCEL_REQUEST
REAL_ORDER_CALLBACK ... 状态:已撤
```

撤单失败时检查：

```text
FAILED_CANCEL_CALLBACK
API_RESPONSE
```

不要因为没有立即收到“已撤”就连续重复运行报单脚本。

撤单项目必须先有一笔真实发送到仿真交易所并处于可撤状态的订单。对本地虚构订单、历史订单或仅 `checked=2` 的请求不能执行撤单，也不能作为撤单验收证据。

## 11. 正式日志核对

正式日志路径：

```text
logs/trader.log
```

建议使用 VS Code、Notepad++ 或其他支持 UTF-8 的工具打开。

主要日志标记：

| 标记 | 含义 |
|---|---|
| `REAL_API_READY` | 真实账号完成登录和历史同步 |
| `REAL_INSTRUMENT_DATA` | 真实合约及行情数据 |
| `TEST_INPUT source=OPERATOR` | 订单参数来自人工输入 |
| `REAL_ORDER_CHECK_REQUEST` | 发起 `checked=2` 校验 |
| `REAL_ORDER_CHECK_RETURN` | 校验调用返回值 |
| `REAL_ORDER_SEND_REQUEST` | 准备发送真实委托 |
| `REAL_ORDER_SEND_RETURN` | 真实报单调用返回值 |
| `REAL_ORDER_CALLBACK` | 柜台真实订单状态回报 |
| `REAL_TRADE_CALLBACK` | 柜台真实成交回报 |
| `REAL_CANCEL_REQUEST` | 发送真实撤单请求 |
| `FAILED_CANCEL_CALLBACK` | 真实撤单失败回报 |
| `API_RESPONSE` | 易达异步请求响应 |

提交测试结果前检查：

1. 日志包含测试开始时间。
2. 日志包含 `REAL_API_READY`。
3. 订单参数可以与人工测试记录对应。
4. 报单测试必须同时有 `REAL_ORDER_SEND_REQUEST` 和真实 `REAL_ORDER_CALLBACK source=LIVE`。
5. 成交测试必须有真实 `REAL_TRADE_CALLBACK`。
6. 撤单测试必须有“已撤”回报或明确的撤单失败回报。
7. 日志中不能包含账号密码、AuthCode 或完整服务器配置。
8. 日志中的 `Traceback` 必须在提交前解释清楚。
9. 不能用 `CHECK_ONLY` 日志代替正式发送到仿真交易所的报撤单日志。

## 12. Git 操作与改动审查

当前开发分支：

```text
codex/real-api-minimal
```

查看提交：

```powershell
git log --oneline --decorate
```

用户提供的 `order.py` 基线提交：

```text
4a8a311
```

查看相对用户基线的代码改动：

```powershell
git diff 4a8a311..HEAD -- scripts/order.py
```

查看真实 API 代码提交：

```powershell
git show 850cd01
```

检查敏感文件没有被提交：

```powershell
git status --short
git ls-files config/account.json config/ydClient.ini logs/trader.log
```

第二条命令正常情况下不应输出任何内容。

不要执行：

```powershell
git add -f config/account.json
git add -f config/ydClient.ini
git add -f logs/trader.log
```

## 13. 常见问题

### 13.1 `python` 不是 3.9 64 位

现象：wheel 提示不支持当前平台。

处理：安装或选择 CPython 3.9 x64，再重新创建 `.venv`。

### 13.2 `ModuleNotFoundError: No module named 'pyyd'`

处理：

```powershell
.\.venv\Scripts\python.exe -m pip install .\vendor\wheels\pyyd-1.486.96.99-cp39-cp39-win_amd64.whl
```

确保运行脚本时也使用 `.venv\Scripts\python.exe`。

### 13.3 `Failed to create ydAPI object`

检查：

- 当前目录是否为项目根目录。
- `config/ydClient.ini` 是否存在。
- `vendor/win64/yd.dll` 是否存在。
- 是否把 `--api-config` 改成了含中文的绝对路径。

本项目默认使用官方示例一致的相对路径 `config/ydClient.ini`。原生 `yd.dll` 对包含中文的绝对配置路径兼容性较差。

### 13.4 `等待 caughtup 超时`

检查：

- 网络能否访问券商测试服务器。
- `ydClient.ini` 的服务器和端口是否正确。
- AppID/AuthCode 是否有效。
- 账号是否具有对应 YD 测试环境权限。
- 是否处于券商测试环境开放时间。

不要通过反复发送订单来验证连接。

### 13.5 登录失败

查看登录错误码和 `error_code.csv`。不要把密码打印到日志或截图里。

### 13.6 终端中文乱码

```powershell
chcp 65001
$env:PYTHONUTF8=1
```

日志文件本身使用 UTF-8，优先检查 `logs/trader.log`。

### 13.7 快期客户端能登录，但 Python API 不能登录

快期2显示的是 CTP 仿真站，Python 脚本使用的是压缩包中的 `pyyd/YDApi`。两者是否共用账号和接入权限必须以真实 `login/caughtup` 结果为准，不能仅凭客户端登录成功判断。

## 14. 测试结果提交清单

提交前整理：

- 本次执行的完整命令，账号部分脱敏。
- `logs/trader.log`，提交前检查敏感信息。
- 快期客户端对应委托/成交/撤单截图。
- 测试日期、时间和操作人员。
- 测试合约、方向、开平、数量、价格和订单类型。
- 如果失败，附错误码和上下约 30 至 50 行日志。
- 对应的 Git commit ID。

国投期货模拟交易软件网页仅提供软件下载，没有脚本或日志上传入口。测试材料应按照项目负责人指定的内部提交渠道交付。

## 15. 当前已验证状态

2026-08-06 本机已经完成真实 `CONNECT_ONLY` 验证：

- 官方 `pyyd` wheel 可以导入。
- `YDApi.start()` 返回 `True`。
- 真实账号登录成功。
- 收到交易连接和交易时段回调。
- 收到 `caughtup`，历史数据同步完成。
- 日志显示 `REAL_API_READY ... data_source=YDApi`。
- 本次验证没有调用报单或撤单接口。

当前验证只证明真实 Python API 连接和登录正常，尚未完成必须发送到仿真交易所的正式报撤单测试，不能填写开仓、平仓、撤单等项目为通过。

2026-08-07 复测时，`ydClient.ini` 中的仿真交易服务器 TCP 端口不可达。旧版和本分支均表现为 `YDApi.start()=True` 后收不到 `login/caughtup`、最终超时。此结果属于外部柜台当前不可用，不能记为连接成功，也不能进入真实报单步骤。

下一阶段必须先由测试人员确定实际合约和订单参数，然后依次执行“查询真实合约数据 → `checked=2` 预检查 → 负责人确认 → 使用 `--send` 真实发送到仿真交易所 → 核对真实订单/成交/撤单回报”。

## 16. 符合性测试 2.3 至 2.11 增量说明

本节对应《程序化交易系统功能标准符合性测试过程记录报告》。现有 2.1 登录和 2.2 基础交易流程保持不变，新增能力全部建立在原 `YDApi`、`Listener` 和 `Trader` 结构上。

| 报告章节 | 功能 | 本脚本实现 |
|---|---|---|
| 2.3 | 系统连接异常监测 | 首次/再次 `caughtup` 确认会话连接与重连，TCP 探测确认网络断开 |
| 2.4 | 报撤单笔数监测 | 独立 `monitor.py` 统计当前账号的实时订单和撤单回报 |
| 2.5 | 重复报单监测 | 按本次任务要求不实现 |
| 2.6 | 指标阈值与预警 | 独立监控进程读取配置并执行报单、报单加撤单阈值预警 |
| 2.7 | 交易指令检查 | 使用真实合约、Tick、最大/最小委托量和涨跌停数据拒绝错误指令 |
| 2.8 | 柜台错误提示 | 展示订单、撤单和 API response 的真实错误码及错误文本 |
| 2.9 | 暂停交易指令 | 易达控制账户交易权限；脚本按 `strategy_id` 控制策略执行权限 |
| 2.10 | 批量撤单 | 使用真实 `find_orders(pending=True)` 和 `cancel_multi_orders` |
| 2.11 | 日志记录 | 生成交易、运行、监测、错误四类日志，并保留汇总日志 |

2.4 和 2.6 的正式验收以独立 `monitor.py` 的 `scope=ACCOUNT_LIVE` 日志为准。它不发送订单，只统计 `caughtup` 之后当前账号收到的实时回报，并用 `(account, order_group, order_ref)` 去重。原 `order.py` 中的进程内统计继续保留，用于兼容此前 Git 版本。

### 16.1 章节 2.3：连接、断开和重连监测

启动独立监控进程。它同时负责 2.3 连接状态和 2.4/2.6 报撤单指标，本身不发送订单：

```powershell
.\.venv\Scripts\python.exe scripts\monitor.py
```

另开一个 PowerShell 窗口持续查看：

```powershell
Get-Content .\logs\monitor.log -Wait
```

日志事件：

```text
TRADING_SERVER_CONNECTION event=CONNECTED process=INDEPENDENT source=YDAPI_CAUGHTUP
TRADING_SERVER_CONNECTION event=DISCONNECTED process=INDEPENDENT source=TCP_PROBE
TRADING_SERVER_CONNECTION event=RECONNECTED process=INDEPENDENT source=YDAPI_CAUGHTUP
```

监控进程在调用 `YDApi.start()` 之前立即启动心跳线程，之后每 5 秒输出一条，证明连接监测、报单监测、撤单监测和阈值监测进程仍在运行。即使易达启动或登录阶段等待较久，心跳也不会消失：

```text
MONITOR_HEARTBEAT process=INDEPENDENT state=RUNNING api_start_state=READY api_ready=1 connection_monitor=RUNNING connection_state=CONNECTED connection_source=YDAPI_CAUGHTUP+TCP_PROBE transport_reachable=YES transport_failure_count=0 exchange_route_state=DISCONNECTED exchange_route_connected=0 exchange_route_disconnected=3 order_monitor=RUNNING cancel_monitor=RUNNING threshold_monitor=RUNNING order_count=0 cancel_count=0 cancel_success_count=0
```

`state=RUNNING` 只表示独立监控进程仍在运行。`connection_state` 才是2.3的程序到期货公司易达交易服务器会话状态：首次收到官方 `caughtup` 回调后为 `CONNECTED`；连续两次无法建立到 `TradingServerIP:TradingServerPort` 的 TCP 连接后为 `DISCONNECTED`；网络恢复时先显示 `RECOVERING`，只有易达再次触发 `caughtup` 后才判定 `RECONNECTED` 并恢复为 `CONNECTED`。官方 C++ 头文件说明 `caughtup` 会在首次成功登录以及断线重连后各触发一次。

`exchange_route_state` 是易达服务器到 CFFEX、SHFE、GFEX 等交易所席位的状态，不是本程序到期货公司交易系统的连接状态。原 `exchange_conn_info` 日志已明确改名为 `EXCHANGE_ROUTE_MONITOR`；其中 `conn_status=0` 表示该交易所席位断开，`conn_status=1` 表示该席位连接。即使 `exchange_route_state=DISCONNECTED`，只要 `connection_state=CONNECTED`，仍表示本程序已成功连接期货公司易达交易服务器。

人工测试步骤：

1. 柜台正常时启动 `monitor.py`，截图 `TRADING_SERVER_CONNECTION event=CONNECTED source=YDAPI_CAUGHTUP`。
2. 按测试负责人允许的方式断开测试网络，等待连续探测失败后截图 `event=DISCONNECTED source=TCP_PROBE`。
3. 恢复网络，先等待 `event=TRANSPORT_RESTORED`，再等待易达真实回调产生 `event=RECONNECTED source=YDAPI_CAUGHTUP` 并截图。

不能通过手工修改日志或构造回调代替真实断开和重连。只有 `TRANSPORT_RESTORED` 而没有第二次 `caughtup/RECONNECTED` 时，表示端口已经恢复但易达会话尚未完成重连，该测试不得判定通过。TCP 探测超时和连续失败次数分别由 `config/monitor.json` 的 `connection_probe_timeout`、`connection_failure_threshold` 控制。

### 16.2 章节 2.4 和 2.6：报撤单统计、阈值和预警

阈值已统一写入 `config/monitor.json`，正常测试不需要在命令末尾重复输入。先执行下面命令，截图控制台显示的配置；该命令不会连接柜台：

```powershell
.\.venv\Scripts\python.exe scripts\order.py --show-monitor-config
```

预期显示：

```json
{
  "config_file": "config/monitor.json",
  "order_threshold": 1,
  "order_cancel_threshold": 2,
  "heartbeat_seconds": 5,
  "duplicate_monitoring": "DISABLED"
}
```

打开第一个 PowerShell 窗口，启动独立监控，并保持窗口运行：

```powershell
.\.venv\Scripts\python.exe scripts\monitor.py
```

必须先看到下面的就绪日志，才能开始报撤单测试：

```text
ACCOUNT_MONITOR_READY process=INDEPENDENT scope=ACCOUNT_LIVE
```

再打开第二个 PowerShell 窗口，执行已有真实报单及自动撤单命令：

```powershell
$testStrategy = "strategy-monitor-01"
.\.venv\Scripts\python.exe scripts\order.py --send `
  --confirm-account $testAccount `
  --strategy-id $testStrategy `
  --instrument $testInstrument `
  --action $testAction `
  --open-close $testOpenClose `
  --volume $testVolume `
  --price $testPrice `
  --order-type $testOrderType `
  --hedge $testHedge `
  --auto-cancel
```

上例表示：独立监控收到第 1 笔新订单的真实回报时触发报单阈值预警；再收到该订单的真实已撤回报时，报单与撤单之和达到 2，并触发报撤单阈值预警。

重点日志：

```text
MONITOR_CONFIG process=INDEPENDENT scope=ACCOUNT_LIVE order_threshold=1 order_cancel_threshold=2
MONITOR_STATS process=INDEPENDENT scope=ACCOUNT_LIVE reason=ORDER_CALLBACK order_count=1 cancel_count=0 order_cancel_count=1 cancel_success_count=0
MONITOR_ALERT process=INDEPENDENT scope=ACCOUNT_LIVE metric=order_count current=1 threshold=1
MONITOR_STATS process=INDEPENDENT scope=ACCOUNT_LIVE reason=CANCEL_CALLBACK order_count=1 cancel_count=1 order_cancel_count=2 cancel_success_count=1
MONITOR_ALERT process=INDEPENDENT scope=ACCOUNT_LIVE metric=order_cancel_count current=2 threshold=2
```

截图口径必须注意：

- “报单笔数统计”截图应包含 `process=INDEPENDENT reason=ORDER_CALLBACK order_count=1`。
- “撤单笔数统计”截图必须包含 `process=INDEPENDENT reason=CANCEL_CALLBACK cancel_count=1`；只有 `cancel_count=0` 的截图不能证明撤单统计功能。
- 若委托已立即成交或被柜台拒绝，程序没有可撤订单，此次不能作为撤单测试证据，需换用能够挂单的测试参数重新执行。
- `cancel_success_count=1` 只有独立监控收到真实“已撤”回报后才成立。撤单失败回报会计入 `cancel_count`，并输出 `reason=CANCEL_FAILED_CALLBACK`，但不会增加 `cancel_success_count`。

字段口径：

- `order_count`：独立监控在 `caughtup` 后首次收到的真实订单回报数量。
- `cancel_count`：独立监控收到的真实已撤或撤单失败回报数量。
- `order_cancel_count`：`order_count + cancel_count`。
- `cancel_success_count`：独立监控收到真实 `status=已撤` 回调的订单数量。

监控进程重启后计数从 0 开始，因此同一轮截图测试期间不要关闭第一个窗口。`--order-threshold 0` 或 `--order-cancel-threshold 0` 表示关闭对应预警。2.5 的重复报单统计和重复报单阈值没有实现，也不会在日志中伪装为通过。

心跳间隔由 `config/monitor.json` 的 `heartbeat_seconds` 控制，必须是大于 0 的整数。临时测试时也可用 `scripts/monitor.py --heartbeat-seconds 10` 覆盖，但不会改写配置文件。

### 16.3 章节 2.7：错误交易指令检查

三个测试点都建议使用 `--check-only`，避免把故意错误的参数发送到交易所。参数仍然必须由测试人员提供。

1. 合约代码错误：输入柜台中不存在的合约，日志应出现：

```text
VALIDATION_REJECT rule=INSTRUMENT_EXISTS
```

2. 最小变动价位错误：先从 `REAL_INSTRUMENT_DATA` 读取真实 Tick，再输入不是 Tick 整数倍的价格，日志应出现：

```text
VALIDATION_REJECT rule=PRICE_TICK
```

3. 单笔数量超限：先读取真实 `max_limit_order_volume` 或 `max_market_order_volume`，再输入超过该值的数量，日志应出现：

```text
VALIDATION_REJECT rule=MAX_ORDER_VOLUME
```

出现上述 `VALIDATION_REJECT` 后，程序退出码为 1，且日志中不得出现 `REAL_ORDER_SEND_REQUEST`。

### 16.4 章节 2.8：真实柜台错误提示

系统从以下真实回调记录错误：

```text
REAL_ORDER_CALLBACK
FAILED_CANCEL_CALLBACK
API_RESPONSE
COUNTER_ERROR
```

错误文本来自随官方 API 提供的 `error_code.csv`。脚本根据 `SHFE/INE/DCE/CZCE/CFFEX/GFEX/SSE/SZSE` 选择对应交易所错误表；如果不是交易所错误，则回退到易达错误表。

资金不足、持仓不足和市场状态不允许三个测试点必须在仿真柜台在线后，由负责人设计真实测试订单并使用 `--send` 发送。最终证据必须同时包含：

- `REAL_ORDER_SEND_REQUEST`；
- `REAL_ORDER_CALLBACK source=LIVE`；
- 非零错误码；
- `COUNTER_ERROR` 中对应的真实错误文本。

不能在代码中写死错误码、构造假订单回报或手工修改日志。

#### 持仓不足如何真实触发

此项不能靠脚本猜测持仓。测试人员先在快期客户端确认某个合约、某一方向的可平持仓为 0，再向同一真实仿真账号提交 1 手平仓委托：

```powershell
# 示例口径：仅在快期中确认“卖平对应的多头可平持仓为 0”后使用。
# 合约和价格必须替换为测试当日真实有效值。
.\.venv\Scripts\python.exe scripts\order.py --send `
  --confirm-account $testAccount `
  --strategy-id strategy-2-8 `
  --instrument $testInstrument `
  --action 1 `
  --open-close 1 `
  --volume 1 `
  --price $testPrice `
  --order-type 0 `
  --hedge 1 `
  --wait-seconds 5
```

参数含义：`action=1` 为卖，`open_close=1` 为平；对于上期所或能源中心，如果柜台要求区分昨仓和今仓，应按实际零持仓类型把 `open_close` 改为 `3`（平今）或 `4`（平昨）。如果准备测试买平空头不足，则将 `action` 改为 `0`，并先确认空头可平持仓为 0。

判定时必须同时看到 `REAL_ORDER_SEND_REQUEST`、带非零错误码的 `REAL_ORDER_CALLBACK source=LIVE` 和 `COUNTER_ERROR`。若先出现合约、价格、市场状态等其他错误，本次没有触发“持仓不足”，应修正对应参数后重测。真实错误文字以柜台回报为准。

注意：持仓不足示例必须使用上面的 `--order-type 0`（限价单）。如果使用 `--order-type 2`（市价单），而该合约或交易所不支持市价单，订单可能先因订单类型被拒绝，无法得到持仓不足结果。

### 16.5 章节 2.9：暂停下达交易指令

2.9 按两个真实控制层级执行：第一层通过易达官方 `set_trading_right` 控制整个账户的交易权限；第二层由脚本按 `strategy_id` 控制某个策略的执行权限。易达 API 没有 `strategy_id` 字段，该 ID 只存在于本程序，不能写成易达原生功能。执行前明确账号和策略：

```powershell
$testAccount = (Get-Content .\config\account.json -Raw | ConvertFrom-Json).name
$testStrategy = "strategy-2-9"
```

负责人确认后启用双层暂停：

```powershell
.\.venv\Scripts\python.exe scripts\order.py --pause-two-layer `
  --confirm-account $testAccount `
  --strategy-id $testStrategy `
  --trading-right-source 3 `
  --control-response-timeout 10 `
  --wait-seconds 1
```

脚本先调用官方 `set_trading_right(..., trading_right=2, trading_right_source=3)`。只有同步返回 `True`，并收到同一 `request_id` 的真实 `API_RESPONSE error=0` 后，才会暂停指定 `strategy_id` 的执行权限。真实成功日志顺序应为：

```text
TRADING_RIGHT_REQUEST layer=YD_API ... trading_right=2 ... request_id=<实际编号>
TRADING_RIGHT_RETURN layer=YD_API ... result=True
API_RESPONSE error=0 ... request_id=<同一实际编号>
TRADING_RIGHT_CONFIRMED layer=YD_API trading_right=2 ... error=0
STRATEGY_CONTROL state=PAUSED layer=STRATEGY_EXECUTION strategy_id=strategy-2-9
TWO_LAYER_CONTROL state=PAUSED account_trading=FORBIDDEN strategy_id=strategy-2-9 strategy_execution=PAUSED
```

随后验证第二层脚本拒单；不填写订单参数即可，因为程序会在连接柜台和构造订单之前阻止发送：

```powershell
.\.venv\Scripts\python.exe scripts\order.py --send `
  --strategy-id $testStrategy
```

真实日志必须出现：

```text
TRADE_BLOCKED control=STRATEGY_PERMISSION layer=STRATEGY_EXECUTION strategy_id=strategy-2-9
```

测试完成后恢复两层权限：

```powershell
.\.venv\Scripts\python.exe scripts\order.py --resume-two-layer `
  --trading-right-source 3 `
  --confirm-account $testAccount `
  --strategy-id $testStrategy `
  --control-response-timeout 10 `
  --wait-seconds 1
```

恢复时也必须先看到易达 `trading_right=0` 的同请求号 `API_RESPONSE error=0` 和 `TRADING_RIGHT_CONFIRMED`，之后才会恢复指定策略的执行权限，并记录：

```text
STRATEGY_CONTROL state=RUNNING layer=STRATEGY_EXECUTION strategy_id=strategy-2-9
TWO_LAYER_CONTROL state=RUNNING account_trading=ALLOWED strategy_id=strategy-2-9 strategy_execution=RUNNING
```

可从汇总日志筛选出报告截图：

```powershell
Select-String -Path .\logs\trader.log `
  -Pattern 'TRADING_RIGHT_|API_RESPONSE|STRATEGY_CONTROL|TRADE_BLOCKED|TWO_LAYER_CONTROL'
```

真实性判定：`TRADING_RIGHT_RETURN result=True` 只表示请求已发出，不能单独证明易达禁止账户交易；没有匹配的真实 `API_RESPONSE error=0` 和 `TRADING_RIGHT_CONFIRMED` 时，账户权限层不得填写为通过。策略层必须出现同一 `strategy_id` 的 `STRATEGY_CONTROL` 与 `TRADE_BLOCKED`。若柜台不提供账户权限设置、账号无权修改或响应超时，脚本明确失败，也不会创建策略暂停成功证据。

Python API 没有提供可确认的强制登出接口，因此本脚本不伪造“强制账号退出”测试。

### 16.6 章节 2.10：部分和全部批量撤单

脚本只选择真实 API 返回的当前账号未完成订单，不生成订单对象。

部分批量撤单，例如最多选择 2 笔：

```powershell
.\.venv\Scripts\python.exe scripts\order.py --batch-cancel `
  --cancel-limit 2 `
  --confirm-account $testAccount `
  --wait-seconds 5
```

全部批量撤单：

```powershell
.\.venv\Scripts\python.exe scripts\order.py --batch-cancel `
  --cancel-limit 0 `
  --confirm-account $testAccount `
  --wait-seconds 5
```

`--cancel-limit 0` 会选择当前账号全部未完成订单，执行前必须在快期客户端逐笔核对。官方接口单批最多 16 笔；超过 16 笔时脚本按 16 笔自动分批，仍逐笔计入 `cancel_count`。

成功证据：

```text
BATCH_CANCEL_SELECTION
REAL_BATCH_CANCEL_REQUEST
REAL_BATCH_CANCEL_RETURN
REAL_ORDER_CALLBACK ... status:已撤
MONITOR_STATS ... cancel_success_count=<实际成功数量>
```

对于部分成交订单，订单必须仍由真实 API 返回为 `pending=True` 才会进入批量撤单列表。

### 16.7 章节 2.11：分类日志

每次运行都会保留原有汇总日志，并生成四类专项日志：

| 文件 | 内容 |
|---|---|
| `logs/trader.log` | 所有类别的汇总日志，兼容原测试流程 |
| `logs/trading.log` | 报单、成交、撤单和批量撤单 |
| `logs/runtime.log` | 启动、登录、初始化、测试模式和停止 |
| `logs/monitor.log` | 连接状态、报撤单统计、阈值预警和交易控制 |
| `logs/error.log` | 参数拒绝、柜台错误、撤单失败和程序异常 |

提交测试结果时至少保留 `trader.log`，并按测试章节附相应专项日志。所有日志均为 UTF-8；截图和提交前必须检查账号等敏感信息已经脱敏。

可用下面的 PowerShell 命令生成 2.11 截图。第一条证明五个日志文件存在，后四条分别抽取交易、运行、监测和错误事件：

```powershell
Get-ChildItem .\logs\*.log | Select-Object Name, Length, LastWriteTime
Select-String -Path .\logs\trading.log -Pattern 'REAL_ORDER|REAL_BATCH_CANCEL'
Select-String -Path .\logs\runtime.log -Pattern 'TEST_MODE|STATE_CHANGE'
Select-String -Path .\logs\monitor.log -Pattern 'TRADING_SERVER_CONNECTION|EXCHANGE_ROUTE_MONITOR|MONITOR_STATS|MONITOR_ALERT|TRADE_CONTROL'
Select-String -Path .\logs\error.log -Pattern 'VALIDATION_REJECT|COUNTER_ERROR|TRADE_BLOCKED|TEST_FAILED'
```

某条查询没有输出不代表日志功能失效，而是本次运行尚未发生该类事件。例如只有真实柜台拒单后才会出现 `COUNTER_ERROR`。报告截图应从已经完成相应测试点的日志中截取，不能补写或复制伪造事件。

### 16.8 正式判定原则

- 2.3 必须看到真实连接、断开、重连回调。
- 2.4、2.6、2.8、2.10 必须在柜台在线后以真实订单或撤单回报判定。
- 2.7 必须证明错误参数被拒绝，并且没有 `REAL_ORDER_SEND_REQUEST`。
- 2.9 必须同时证明易达账户权限的异步 `API_RESPONSE error=0`，以及同一 `strategy_id` 的策略暂停和 `TRADE_BLOCKED`。
- 2.11 必须检查五个日志文件包含对应真实事件。
- 2.5 不在本次实现范围，不得填写为已完成。
