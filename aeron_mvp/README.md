# YDTrader Aeron Archive MVP

这个目录是行情高并发架构的可运行验证，不接触 `secure_release_toolkit`，也不替换现有
YDApi/Unix socket 命令。它先用确定性模拟行情证明最难的传输和恢复语义：

[查看系统架构图（初版）](../docs/high-concurrency-architecture.md)

```text
SBE publisher -> Aeron IPC -> Aeron Archive
                                  |-- compute open-ended replay
                                  `-- audit open-ended replay
                                              |
                                      stopped recording replay
```

## 已验证范围

- SBE 二进制行情消息；
- Aeron IPC 非阻塞 `offer`，失败只在发布工作线程重试；
- Aeron Archive 原始流持久化；
- `compute` 与 `audit` 两个独立 JVM 进程同时跟随仍在写入的 recording；
- consumer 不参与原始临时流的 flow control；
- sequence 缺口、重复和无效时间戳使结果成为 `INCOMPLETE`；
- 实时计算和停止后的完整重放结果逐字段一致；
- 下游 Compute/Audit 每 250ms 输出轻量 NDJSON 快照，Dashboard 按秒展示进度、吞吐、延迟和完整性。

MVP 现已包含真实 YDApi adapter；仍不包含 C/Cython SPSC bridge、跨机器 UDP、HA
或自动销毁。CTP 适配器保留为 YDApi 无行情时的测试源。三种数据源共用同一套
schema、stream ID、Archive 和消费者。

## 依赖

- Linux x86_64 / WSL 2；
- JDK 17；
- `aeron-all-1.51.0.jar`；
- `sbe-all-1.38.1.jar`。
- `jeromq-0.6.0.jar`（通用 ZMQ 行情出口）。

脚本不依赖任何开发者的绝对路径，按以下顺序发现依赖：显式环境变量、发行包内置 runtime、
当前 Conda/PATH 中的 JDK 17、项目本地 `result/aeron-mvp-deps/`。JAR 和生成物不会提交
仓库。首次直接运行验收命令时，缺少的固定版本依赖会自动下载到项目 `result/`；也可以
提前执行：

```bash
bash aeron_mvp/bootstrap.sh
```

脚本会逐项校验固定的 SHA-256/SHA-1，不接受未通过校验的下载文件。
如需完全禁止自动联网，设置 `AERON_MVP_AUTO_BOOTSTRAP=0`，并通过 `JAVA_HOME`、
`AERON_JAR`、`SBE_JAR` 提供依赖。也可以创建项目 Conda 环境：

```bash
conda env create -f environment.yml
conda activate ydtrader-high-concurrency
```

## 一键验收

在 WSL 的 Linux 项目树中运行：

```bash
bash scripts/run_aeron_mvp.sh --count 100000 --sync-level 0
```

百万条验证：

```bash
bash scripts/run_aeron_mvp.sh --count 1000000 --sync-level 0
```

`sync-level=0` 用于吞吐功能验证；还应使用 `--sync-level 1` 重跑，测量要求强制刷脏页时
的吞吐差异。验收成功时最后会输出：

```text
AERON_MVP_ACCEPTANCE result=SUCCESS ...
AERON_MVP_ACCEPTANCE live_compute=SUCCESS live_audit=SUCCESS replay_match=YES
```

## 多行情源同时接入

每个行情源使用一份独立 JSON 配置，并用重复的 `--source-config` 传给启动脚本。类型支持
`synthetic`、`ctp`、`ydapi`，配置中的唯一 `name` 会写进每条 SBE 行情的 `source` 字段。
下面的本地命令会同时启动两个独立模拟源，每个源发布 1000 条：

```bash
bash scripts/run_multi_source_aeron_mvp.sh \
  --source-config config/market-sources/synthetic-sim-a.json \
  --source-config config/market-sources/synthetic-sim-b.json \
  --count 1000
```

同时连接 OpenCTP 与 YDApi 时，先从 `.example.json` 复制部署配置，分别填写两边 runtime、
front、账号文件和合约，再同时传入：

```bash
bash scripts/run_multi_source_aeron_mvp.sh \
  --source-config /secure/market-sources/ctp-tts.json \
  --source-config /secure/market-sources/ydapi-main.json \
  --count 1000000
```

每份配置独立控制 Python runtime、连接端点、合约、repeat 和超时；CTP 配置还独立控制
`api_kind` 与 `latency_mode`，YDApi 配置独立引用 account/API 文件。相对路径从项目根目录解析，
账号密码不能直接写进行情源配置。完整字段和样例见 `config/market-sources/README.md`。

`--count` 是每个源的目标条数，总条数等于来源数乘以该值。每个 bridge 使用独立 loopback
UDP 端口和独立输入 sequence；轻量 mux 校验每个源没有缺口后，为合并流分配全局 sequence，
再送入原有单一 Aeron publication、Archive recording、Compute/Audit 和 ZMQ 出口。任一源断流、
缺包或提前退出都会让整次运行失败，不会静默降级。Dashboard 的行情源列表会显示该次运行连接的
全部来源，严格保持 Bash 配置顺序；下拉名称来自每份配置的 `name`，不提供 `MULTI` 或历史运行
选项。进度快照同时保留兼容字段 `latency_by_source`，并新增 `source_views`，分别保存每个来源的
最新行情、五档盘口、总体延迟、分时延迟和分合约延迟。切换来源只切换这些来源级数据，吞吐、
总接收数、Recording、Archive、Compute/Audit 状态仍属于整次运行。

旧的 `--sources <类型>[:来源名],...` 及共享的 `--ctp-*`/`--ydapi-*` 参数继续保留，供既有
命令兼容使用；它们不能与 `--source-config` 混用。新部署应始终使用逐源配置模式。

这条入口只组合高并发项目已有的接入接口，不导入下游 hpquant 源码。部署时两个项目仍可分别
打包，在服务器上通过高并发侧 ZMQ `PUB` 与 hpquant 侧 `SUB` 连接。

## 通用 ZMQ/SBE 行情出口

出口位于 Aeron 原始行情 stream 的独立消费支路，而不是 Compute/Audit 结果层。
`ZmqMarketDataEgress` 订阅 SBE v3 原帧，以 ZMQ `PUB` 发送两帧消息：第一帧固定为
`snapshot`，第二帧是未改写的 SBE bytes。Aeron 继续负责 IPC 与 Archive，ZMQ 只承担跨进程
TCP 边界；下游项目自行实现 `SUB`、协议解码和队列适配。

```bash
bash scripts/run_zmq_market_smoke.sh 1000
```

正式运行时先启动下游 `SUB`，等待订阅建立后，再由多行情源主脚本同时编排 Media Driver 与 ZMQ 出口：

```bash
bash scripts/run_multi_source_aeron_mvp.sh \
  --source-config \
    config/market-sources/ydapi.json \
    config/market-sources/ctp-live.example.json \
  --zmq-endpoint tcp://0.0.0.0:7101 \
  --count 1000000
```

下游只需连接 `tcp://<high-concurrency-host>:7101`。高并发包不包含下游业务包、Python
monkey patch、`sitecustomize` 或下游队列实现。PUB/SUB 不缓存订阅建立前或订阅者断线期间的
实时行情；下游以 sequence 检测缺口，并使用 checkpoint 和 Archive 的显式
`--mode replay --resume` 恢复。
`zmq-egress` 是 Aeron 客户端，不能脱离主脚本单独启动；主脚本会把本次运行实际使用的动态
Aeron 目录传给它，不需要 `sudo`，也不得另写一个固定的 `/dev/shm` 目录。

### 输入与输出边界

`run_zmq_market_smoke.sh` 的 `live.summary.json` 和 `replay.summary.json` 同时保存完整
`first_tick`、`last_tick`，包括行情时间、最新价、五档价量、成交量、成交额、持仓量、开高低收、
涨跌停价、`sessionId + sequence`、schema version、来源和接收时间。两份摘要逐字节比较，用来证明
实时流与 Archive replay 送到网络边界的数据一致。探针只用于本项目自测，不是下游项目适配器。

运行证据和持久化 recording 保存在 `result/aeron-mvp/<run-id>/`。源码树中的 Java/SBE
文件比现有 class 新时，验收脚本会自动重编译，避免 `git pull` 后继续运行旧 class。
实时统计剔除每个
producer session 的第一条有效行情；`std_ms` 为样本标准差，`p95_ms` 使用 nearest-rank。

## 启动实时 Dashboard

先在一个 WSL 终端启动只读监控服务：

```bash
conda activate ydtrader-high-concurrency
bash scripts/run_dashboard.sh --port 8080
```

浏览器打开 `http://127.0.0.1:8080`，再在另一个终端运行任一 Aeron 验收命令。页面默认自动
选取 `result/aeron-mvp/` 中最新的 run，也可以在“行情源”下拉框中固定查看某个 source 的最新
run；选项从 `run.meta` 动态发现，新增 YDApi、CTP 等行情源无需修改 Dashboard。页面每秒刷新。
服务默认只监听本机；确需供局域网查看时
显式传入 `--host 0.0.0.0`，并由主机防火墙限制访问范围。

Dashboard 只读取 Compute/Audit 已写出的 NDJSON 和 summary，不订阅发布流、不参与 Aeron
flow control。页面的“最新采样行情”固定携带五档槽位，可选显示 1～5 档；
YDApi 只填写一档，CTP 最多填写五档，任何缺失档位都输出空值而不伪造数据。
延迟统计同时按中国时区的 15 分钟行情时间窗口和合约聚合，输出
`count / mean / std / p50 / p90 / p95 / p99 / max`。Compute 消费线程以 O(1) 方式更新
HdrHistogram，不把逐笔延迟写磁盘；均值和样本标准差使用在线算法，分位数保留三位有效数字。
全量行情仍只保存在 Archive。实时阶段显示 mean/std/max；
精确 P95 在计算完成后由同一套统计代码写入最终快照。

Dashboard 将系统对时和行情观测严格分为两个数据域：

- “对时操作”只展示 Windows 与 Leader Linux 的受控脚本入口，页面自身不执行提权命令，
  也不会修改系统时钟；
- “对时检测”只读取 `windows-time.json` 与 `linux-time.json`，验证报告有效期、单端 offset、
  授时源一致性、采样时间差和双端差值；可用 `--time-report-root` 指定报告目录，默认项目根目录，
  `--time-report-max-age-seconds` 默认 300 秒；
- 行情侧原有 `mean_ms / p95_ms / max_ms` 在页面中明确命名为“绝对行情观测差”，其定义是
  `|本地回调接收时间 - 行情事件时间|`。它包含时钟差、网络、柜台、网关和回调排队，
  不等于授时偏差，也绝不参与对时判定；
- OpenCTP 历史回放继续隐藏实时观测差，只保留吞吐、归档与完整性结果。

授时检测接口为 `GET /api/time-sync`，行情运行接口仍为 `GET /api/status`；传入
`GET /api/status?source=<source>` 可读取指定行情源的最新 run。生成同期报告或新增行情源后直接
刷新页面即可，不需要重启 Dashboard。

## 易达 YDApi 实时行情

先确认 `config/account.json` 和 `config/ydClient.ini` 是目标环境的真实配置，然后在
WSL2 项目 Conda 环境中执行：

```bash
conda env update -f environment.yml --prune
conda activate ydtrader-high-concurrency

bash scripts/run_ydapi_aeron_mvp.sh \
  --instrument IF2609 \
  --count 1000000 \
  --ydapi-repeat 10000 \
  --sync-level 0
```

`ydapi_bridge.py` 使用仓库固定的官方 `pyyd` 1.486.96.99 Linux wheel，直接登录、
查找并订阅真实合约。易达回调线程只做字段归一化和非阻塞 loopback UDP 投递；
磁盘持久化由 Aeron Archive 执行，Dashboard 从 Compute/Audit 快照中显示最新真实行情。

YDApi 只提供以 17:00 为起点的行情时钟。Adapter 使用回调接收时间和最近 24 小时
原则还原绝对时间，包括夜盘跨日；`--ydapi-repeat` 只放大 Aeron 压力，不改变
Dashboard 快照中的真实价格、数量、合约和行情时间。

## 官方 CTP 实时行情与最新快照

交易时段可直接连接官方 CTP 实时模拟行情前置，驱动同一条 Aeron/Archive/Compute 链路：

```bash
bash scripts/run_ctp_live_aeron_mvp.sh \
  --count 1000000 \
  --ctp-repeat 10000 \
  --sync-level 0
```

启动脚本使用独立的 `result/ctp-live-runtime`，下载的 SDK 压缩包及官方行情 `.so` 均校验
固定 SHA-256，并验证 API 版本为 6.7.11。默认前置为
`tcp://182.254.243.31:30011`；可用 `--ctp-front` 覆盖。Dashboard 会把该 run 标记为
`ctp-live`，并展示最新合约、当天行情时间、最新价及买卖一档。这个源提供真实市场快照，
但属于模拟行情环境，并不等同于生产交易柜台或正式 YDApi 链路。

`--ctp-repeat` 只负责用每个真实 Tick 驱动 Java/Aeron 压力测试；Dashboard 最新快照中的价格、
数量、合约和市场时间都来自最后一个真实 Tick。真实 Tick 数和放大后的发布数会分别记录。

## OpenCTP 7x24 历史行情验收

附件中的 `openctp_ctp` 回调被封装成独立 Python 采集进程。采集进程把每条真实 CTP Tick
编码为 v2 284 字节五档 UDP 包，只发送到 `127.0.0.1`；Java 仍兼容旧 v1 156 字节包，
并校验包头和连续 source sequence，
再转换为原有 SBE `MarketQuote` 并写入 Aeron Archive。

OpenCTP 官方说明：TTS 柜台虽然兼容 CTPAPI，但必须把 CTP 原厂 `dll/so` 替换为同版本
TTS 动态库；若误用原厂库，`OnFrontDisconnected` 会持续报告 `4097`。先运行固定版本、
固定校验值的隔离环境引导脚本：

```bash
bash scripts/bootstrap_ctp_tts.sh
```

脚本根据根 `environment.yml` 创建 `result/ctp-tts-runtime/conda`，下载并校验官方
`tts_6.7.11.zip`，只替换这个隔离 Conda 前缀中的行情 `.so`。官方 CTP Live 使用
独立的 `result/ctp-live-runtime/conda`，两个原生库不会互相覆盖。需要离线安装时可通过
`TTS_SDK_ZIP` 指向已经下载的官方 ZIP。

OpenCTP 7x24 一键验收：

```bash
bash scripts/run_ctp_aeron_mvp.sh \
  --count 1000000 \
  --ctp-repeat 1000000 \
  --sync-level 0
```

休市时前置可能只发送订阅快照；`--ctp-repeat 1000000` 可用首个真实 CTP Tick 驱动百万条
Aeron 压测。若设为 `10000`，则至少需要 100 个真实 Tick 回调，行情不连续时脚本会继续等待。

Linux wheel 内部使用 `zh_CN.GB18030`；引导脚本通过 `localedef` 自动把该 locale 生成到
隔离 runtime 并设置进程级 `LOCPATH`，无需 sudo，也不修改系统 locale。

默认行情前置是 `tcp://trading.openctp.cn:30011`。`--ctp-instruments` 接受逗号分隔的
合约列表，`--ctp-front` 可覆盖行情前置。每个真实 Tick 放大 `--ctp-repeat` 条消息，目的
是验证 Java/Aeron/Archive 的高并发能力，而不是声称 CTP 前置本身产生了同等 Tick 速率。
验收日志同时输出 `source_ticks` 和 `published`，两者不能混为一谈。

OpenCTP 7x24 环境可能重放历史交易日，因此其 market timestamp 适合验证字段传输与计算
稳定性，不适合衡量当前机器到真实交易所的实时链路延迟；Dashboard 会将该模式明确标记为
历史回放并隐藏误导性的延迟数值。

## 构建自包含发布包

```bash
bash aeron_mvp/build_release.sh
```

生成 `result/ydtrader-aeron-mvp-java-0.8.0-linux-x86_64.tar.gz`，包含精简 Java 17
运行时、Aeron runtime 和 Dashboard 静态资源；核心验收无需预装 Java或联网下载依赖，
Dashboard 和 Python bridge 需目标机安装 Miniconda，并按包内 `environment.yml` 创建环境。
