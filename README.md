# YDTrader Linux 高并发行情与交易工具

本仓库面向 Linux x86_64，包含 YDApi/CTP 行情接入、Aeron 高并发传输与归档、实时统计、Dashboard，以及 `order`、`monitor`、`marketdata` 三个业务入口。

项目不再包含自建许可证、运行密码、机器绑定、激活、到期销毁或加密状态。业务命令可直接启动。`config/account.json` 中的账号和密码仍然必须保留：它们是连接 YDApi 柜台所需的登录凭据，并不是项目加密机制。

## 项目结构

```text
environment.yml               唯一开发与运行依赖清单
data/                         输入和参考数据
result/                       构建、运行与验收产物
scripts/                      可重复执行的构建和运行入口
tests/                        自动化测试
utils/                        可复用的 WSL/Conda 运行时助手
aeron_mvp/                    Java、SBE、Aeron 与行情桥接源码
dashboard/                    高并发行情只读监控端
ydcore/                       报单、监控和行情业务源码
config/                       配置模板与本机运行配置
docs/                         架构和迁移文档
build_tools/                  Cython、Nuitka 与 manylinux 构建模块
install/                      固定路径安装程序
vendor/wheels/                官方离线 pyyd wheel
```

高并发架构和数据流见 `docs/high-concurrency-architecture.md`。旧 Windows 手册只作为迁移参考保存在 `docs/legacy-windows-manual.md`。

## 环境与测试

开发和测试在 WSL2 Linux 文件系统中的项目专用 Miniconda 环境运行：

```bash
cd ~/projects/high-concurrency
conda env update -f environment.yml --prune
conda activate ydtrader-high-concurrency
python -m pytest -q
```

`environment.yml` 是本机依赖的唯一清单；`requirements-build.txt` 只供 manylinux2014 Docker 构建使用。

## 高并发行情

```bash
bash scripts/run_aeron_mvp.sh --count 100000 --sync-level 0
bash scripts/run_multi_source_aeron_mvp.sh \
  --source-config \
    config/market-sources/synthetic-sim-a.json \
    config/market-sources/synthetic-sim-b.json \
  --count 1000
bash scripts/run_dashboard.sh
```

统一入口接受一个或多个独立 JSON 配置，可连接任意数量的 `synthetic`、`ctp`、`ydapi` 实例。
样例和字段说明见 `config/market-sources/README.md`。旧的 `--sources`
共享参数模式仅为兼容既有命令而保留。YDApi/CTP 数据源的准确参数和验收方式见
`aeron_mvp/README.md`，授时与行情延迟操作见 `README_授时与行情延迟操作手册.md`。

### Python 统一入口（源码/扩展模块共用）

Dashboard 默认按北京时间自然日展示当前所选行情源的当天平均延迟、分位数、
分时与分合约统计；另行保留该源的全会话总平均延迟及累计样本数（包含旧快照）。
当天统计要求行情事件日期与本地回调接收日期一致，不使用 `TradingDay`，不按
延迟大小剔除真实慢行情。跨日旧快照、未来日期、无效时间分别计数，归档数据不变。
页面午夜自动切换当天视图；无当天样本时显示等待状态。旧运行缺少每日字段时
显示“该运行未提供当天统计”，仍可查看其全会话均值。
Compute 在 `source_views[].daily_latency` 中提供 `date`、`timezone`、三类排除计数、
`latency`、`latency_by_time` 与 `latency_by_contract`；原有会话字段保留。
每日统计限当前运行批次，各来源独立直方图；迟到旧记录不会将统计日期回退。
更新后需要新的 Compute 进程读取归档/行情，并重新加载 Dashboard 后端，
已有进程的会话状态不会因源码更新自动重算。TTS 历史回放继续隐藏实时延迟。

在上述 Conda 环境中，推荐通过同一个参数入口启动：

```bash
python main.py --help
python main.py aeron --help
python main.py multi-source \
  --source-config \
    config/market-sources/synthetic-sim-a.json \
    config/market-sources/synthetic-sim-b.json \
  --count 1000
# 另一个终端启动 Dashboard；不会自动连接柜台。
python main.py dashboard --host 127.0.0.1 --port 8080
```

- `aeron` / `multi-source` 以 `exec` 交给原 Shell 调度，保留原参数、退出码及信号清理逻辑；
  Dashboard 独立运行，原 `order`、`monitor`、`marketdata` 命令不变。
- Shell 内的 Python 子进程统一调用此入口的 `source-config`、`multi-source-mux`、
  `synthetic-bridge`、`ctp-bridge`、`ydapi-bridge`、`zmq-probe` 命令，
  不再通过文件路径执行业务 `.py`。业务模块提供 `main(argv)`，支持编译为 `.so` 后导入调用。
- 顶层帮助不会导入业务模块。CTP 命令先解析参数，再加载原生 SDK，
  因此 `python main.py ctp-bridge --help` 不需要准备 locale 或连接柜台。
- CTP 正式/TTS 仍使用原配置选择的独立解释器；本次没有合并环境、修改原生库或迁移可写目录。

扩展模块验收（只使用本机模拟 UDP 行情与 HTTP，不登录柜台）：

```bash
python -m pytest -q
RUN_COMPILED_ENTRYPOINT_TEST=1 python -m pytest -q tests/test_unified_entrypoint.py
```

第二条命令在临时目录编译模块、删除其对应 `.py` 后对照输出并运行模拟行情与 Dashboard。
`product.toml` 已覆盖跨包 Python 保护范围，并在安全打包前预构建 Java 产物；
Web 加密镜像构建仍需提供签名密钥，并按部署环境准备柜台配置、原生运行库和许可证。

实时 CTP/YDApi 来源在未配置 `source_timeout_seconds` 时默认允许 86400 秒无行情，
以覆盖午休、夜盘间隔和休市；模拟来源默认仍为 60 秒。若已有私有来源配置明确写了
`60`，请改为适合交易时段的值或删除该字段以采用实时来源默认值。超时日志会同时报告
已接收、已发布和剩余数量，便于区分“从未收到行情”和“收到行情后进入休市”。

### 双 CTP 常驻服务

长期运行入口同时连接 `CTP-TTS-7X24` 与 `CTP-LIVE-5LEVEL`。两个 Bridge 独立接入，
Dashboard 分来源展示行情、五档盘口与延迟；Aeron、Archive、Compute/Audit 仍按同一运行批次统计。

```bash
bash scripts/ydtrader_stack.sh install-start
bash scripts/ydtrader_stack.sh status
bash scripts/ydtrader_stack.sh logs
```

该命令把 user-systemd 单元安装到 `~/.config/systemd/user/`，并启用：

- `ydtrader-market.service`：双 CTP 行情、Aeron、Archive 与计算审计链路；异常退出自动重启。
- `ydtrader-dashboard.service`：`http://127.0.0.1:8080/`；异常退出自动重启。
- `ydtrader-stack.target`：统一启动和停止上述两个服务。

日常操作：

```bash
bash scripts/ydtrader_stack.sh start
bash scripts/ydtrader_stack.sh restart
bash scripts/ydtrader_stack.sh stop
```

Windows 桌面入口来自 `deploy/windows/start-ydtrader-dashboard.cmd`。它只调用统一管理脚本，
不会复制业务逻辑，并启动隐藏的 WSL 保活客户端（同一项目重复点击只保留一份）。仅启用 systemd
不能防止 WSL 空闲退出；`ydtrader_stack.sh stop` 后保活客户端会自动退出。
Windows 关机、休眠或显式执行 `wsl --shutdown` 仍会中断接收；恢复后点击桌面入口重新启动。
YDApi 启动追赶阶段的缓存回调不会进入 Aeron；仅在 caughtup 后启用配置合约，
并只转发该合约的正式订阅回调，避免 Dashboard 把 17:00 缓存快照当成实时行情。
TTS 与官方 CTP 均使用主循环中间隔 500ms 的逐合约订阅，并保留原生请求缓冲区。
2026-09-03 夜盘实测：指定实盘前置的批量订阅虽确认成功却没有行情，改用上述方式后收到当前夜盘行情；
实盘 11 合约配置不变，不用历史回放替代实时源。夜盘交易日可能为次日，应结合 ActionDay 和 UpdateTime 验收。
订阅确认不等于行情到达，应检查 `FORWARDING`、Compute 接收数和市场时间是否持续变化。
运行脚本同时检查 Publisher、Aeron Server、Compute、Audit 和可选 ZMQ Egress；
子进程异常退出时整个运行标为失败并交由 systemd 重启，避免 Bridge 仍有回调但曲线停住。
Bridge/Mux/Publisher 的 UDP 序号缺口仍严格报错，不跳过缺口伪装完整；
自动重启只能恢复后续接收，不能补回中断期间的行情，也不代表 UDP 丢包原因已消除。
仅当源明确为 live，且 ActionDay 等于未来交易日而 UpdateTime 距接收时刻不超过一分钟时，
延迟使用最近的实际日历日期；原始 ActionDay/TradingDay 保留，historical_replay 不改写日期。
两份非敏感行情源配置位于 `config/market-sources/ctp-tts-7x24.json` 和
`config/market-sources/ctp-live-5level.json`；每个来源的合约、前置与超时均可独立调整。
当前回放源仅配置已验证回调的 IF2609、IC2609、IH2609、IM2609、au2612、ag2612；
实盘源保留原 11 个合约，不将实盘合约列表直接套给回放源。2026-09-03 验证中，加入 cu2610
及后续未验证合约会出现该连接停止更新；新增回放合约前须单独确认回调和持续增长。

## 业务命令

源码模式：

```bash
python main.py --help
python main.py order --help
python main.py monitor --help
python main.py marketdata --help
```

命令会直接进入相应业务模块，不再要求额外运行密码。真实报单仍必须遵守原有风控：先执行连接、查询、行情与 `checked=2` 验证；`--send`、策略身份、暂停标志、报单和撤单阈值仍然有效，未经负责人明确批准不得执行真实报单或撤单。

## 发布与安装

当前 Linux 环境的功能调试包：

```bash
python build_tools/build_release.py
```

正式兼容包从带 Docker 的 Linux/WSL 主机运行：

```bash
build_tools/build_manylinux2014.sh
```

产物为 `result/ydtrader-linux-x86_64.tar.gz`。发布包保留 SHA-256 文件完整性清单，但不再生成或校验签名，也不需要任何密钥。

目标机安装：

```bash
tar -xzf ydtrader-linux-x86_64.tar.gz
sudo ./ydtrader-linux-x86_64/install/install_linux.sh
sudo install -o root -g "$(id -gn)" -m 0640 account.json /opt/ydtrader/config/account.json
sudo install -o root -g "$(id -gn)" -m 0640 ydClient.ini /opt/ydtrader/config/ydClient.ini
/opt/ydtrader/ydtrader --help
```

安装完成后可直接运行，不存在申请码、发证、激活、计时器或自动销毁步骤。详细人工流程见 `README_人工重跑执行流程.md` 和 `README_本机构建与完整运行.md`。
# 接入独立计数

运行目录的 `ctp-*.log`（实际文件名由来源配置决定）、`mux.log`、`publisher.log`
中可搜索 `state=COUNTERS`。计数仅覆盖各进程本次生命周期，重启归零。

- CTP Bridge：`callbacks` 为进入回调处理次数，`packed_packets` 为打包成功数，
  `udp_sent_packets` 为完整数据报被本机 sendto 接受的次数。
- Mux：按来源输出 `udp_received_packets`（含校验失败或超出目标数的数据报）、
  `udp_sent_packets`（校验后完整发送成功数）、`expanded_records`（repeat 展开条数）。
- Publisher：`udp_received_packets`、`validated_packets`、`aeron_accepted_records`
  分别为接收数据报、校验成功数据报及 Aeron offer 接受的行情条数。

Mux/Publisher 每 5 秒及正常退出或异常展开时输出；Bridge 沿用首条/每 100 条
输出节奏，关闭时再输出。强制杀进程无法保证最后一份计数输出。
这些是独立观测值，不改变丢包、乱序或重试处理。UDP 发送成功不等于对端收到，
Aeron 接受不等于已经刷盘，计数差也不能直接当作永久丢包数。
# 手动分析持久化行情

原始行情在 `result/aeron-mvp/<run_id>/archive/*.rec`，索引为同目录的
`archive.catalog`。请保留整个 archive 目录及运行目录中的 `run.meta`。
`compute-live.ndjson` 是统计快照，不是完整行情；`/dev/shm` 的 Aeron 目录是运行时共享内存，
不是长期存储。实际目录可由启动参数改变，应以运行配置为准。

在项目根目录、Conda 环境中手动执行（替换 RUN_ID）：

```bash
python main.py analyze-archive --run-dir result/aeron-mvp/RUN_ID --date 2026-09-07 --output result/reports/archive-analysis.json
```

省略 `--date` 分析整个批次，省略 `--output` 在终端输出 JSON。
脚本仅使用标准库，临时 SQLite 用于避免原始样本全部进入 Python 内存。
输出逐源条数、合约计数、最新行情/Last Price、有效延迟样本及 Mean/Std/P50/P90/P95/P99/Max、
累计平均值、旧快照/未来日期/无效时间数、超过60秒的接收间隔（明细最多1000项）。
日期筛选依据北京时间回调接收自然日，延迟有效样本要求行情事件同自然日；
累计统计和归档序号缺口仍覆盖全批次，在报告中独立命名。
7×24回放及未知模式不输出实盘延迟。

仅支持当前未分片 SBE v3、从段首开始的本项目归档；不支持格式会报错，不猜测解码。
建议选择已停止的批次，读取运行中归档只是非原子快照，可能遇到不完整帧。
只读原始文件、不启动服务、不自动判断丢包；序号连续不代表持久化前无丢失。
间隔需人工结合休市、回调与进程日志核验。报告输出必须位于原始运行目录之外。
# 系统时钟只读检测与留档

独立采样不修改系统时间，不依赖行情回调，也不要求后台 chrony 已同步。使用 chronyd **大写 `-Q`**；不要改成会校时的小写 `-q`。Linux/容器须有 chronyd（Ubuntu 软件包 chrony），运行采样无需 sudo 或 SYS_TIME 权限。依据：https://chrony-project.org/doc/latest/chronyd.html。

在已激活项目 Conda 环境的仓库根目录运行一次：

```bash
python scripts/time_probe.py --config config/time_authority.tencent-south-china-fallback.conf
```

持续采样（默认建议300秒，多授时源逐个独立记录）：

```bash
python scripts/time_probe.py --config config/time_authority.tencent-south-china-fallback.conf --interval 300
```

Linux/WSL 用户服务安装及开机用户会话自动运行：

```bash
python scripts/install_time_probe_service.py --config config/time_authority.tencent-south-china-fallback.conf --interval 300
systemctl --user status ydtrader-time-probe.service
journalctl --user -u ydtrader-time-probe.service -n 30
```

仅在 Linux 用户服务管理器运行期间采样；Windows关机/睡眠、WSL关闭或网络断开不可能保证持续采样。可用 `systemctl --user disable --now ydtrader-time-probe.service` 停止。容器无systemd时用上述持续命令由容器进程管理器托管；持久挂载 `result/time-probes`。看板与采样分容器时共享该目录，看板只需只读挂载。

Dashboard 新增“系统时钟检测留档”，可选北京时间日期及主机/授时源，展示偏移曲线、当天均值、最大绝对偏移、失败/超限次数，最近100条原始证据。`GET /api/clock-history?date=2026-09-08` 返回当天全部记录；默认当天，日期缺数据不回退。`--clock-history-root` 可设置看板读取目录，须对应采样 `--output-root`。页面刷新不会触发网络探测；原有双端对时报告不被本采样覆盖。

证据存储：`result/time-probes/YYYY-MM-DD/<probe_id>.json`，一条一文件、原子发布并fsync，不自动删除历史。记录UTC采样起止、北京时间日期、执行主机、配置目标NTP、阈值、正负偏移、退出码、原始输出。偏移正值表示本机落后授时源；均值按主机与源分开，包含超限有效样本，失败值null不参与均值。duration_ms是整个探测耗时，不是RTT；RTT/不确定度不可得时留null。该公开测试NTP不是交易所授时源。容器hostname不是物理宿主身份，跨主机核验需额外记录部署对应关系。

历史报告可读取相同日期的JSON留档，保留失败、超限和缺测事实；不得用今天检测结果回填上周，也不得将偏移直接从绝对行情观测差中相减。本功能不自动改写既有PDF。

## Redis 交易兼容入口

交易核心以 `trader-api` 提交 `ed86bc0070a757886201fe066fb60d68fac05aed` 的
`scripts/order.py` 替换，保留 Linux 路径、延迟加载与原 `python main.py order ...` 入口。
Redis 服务迁自同一提交的 `scripts/yd_redis_server.py`，共用这一份交易核心。

在项目专用 Conda 环境中先更新 `environment.yml` 中的依赖。以下帮助命令不连接柜台或 Redis：

```bash
python main.py order --help
python main.py redis-trader --help
python main.py yd-redis-server --help
```

需要启动交易服务时，在项目根目录执行 `python main.py redis-trader`，或使用兼容脚本
`python main.py yd-redis-server`。这会连接配置的柜台，并消费真实交易指令。

打包兼容：`analyze-archive`、`yd-redis-server` 均由根目录 `main.py` 延迟导入
受保护的 `ydcore` 模块；原有 `redis-trader` 命令保持不变。
两个 `scripts/*.py` 兼容入口仅供源码运行，发布时由 `exclude_paths` 排除，
不再要求容器中存在这些明文脚本。参数原样传递；顶层及子命令 `--help` 不连接柜台。
两个入口共用 `--account-config`、`--api-config`、`--startup-timeout`（默认 60 秒）。
Redis 参数仍为 `REDIS_HOST`、`REDIS_PORT`、`REDIS_DB`，默认 `127.0.0.1:6379/0`。
同一账号队列只运行一个消费入口；行情和 Dashboard 不会自动启动该服务。

队列及数据格式保持原脚本：`order_queue:{account_id}` 使用 BRPOP，
`callback_queue:{account_id}` 使用 LPUSH；回报封装为 `type` 与 `data`。
`order_map:{account_id}` 保存 local_id 映射，`positions:{account_id}` 每 5 秒更新。
指令仍只分发 `order`、`cancel`、`cancel_all`，不接入持仓查询指令。

原脚本行为未在迁移时统一或修复：Redis 下单和单撤直接调用 API，未经过人工入口的
完整控制与计数路径；重复 local_id 会覆盖映射；重新启动后的回报关联只搜索内存映射；
必填字段转换异常可能只写日志而没有错误回报；Redis 故障没有回报重放机制。
这些是待单独确认的问题，不视为本次修复完成。离线测试通过不等于柜台联调通过。
