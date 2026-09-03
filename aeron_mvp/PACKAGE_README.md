# YDTrader Aeron MVP Java 0.8.0

这是 Linux x86_64 自包含验收包，内含精简 Java 17 运行时、Aeron 1.51.0、已编译
SBE codec 和行情 MVP。目标机不需要安装 Java，也不需要联网下载依赖。

## 快速验收

```bash
tar -xzf ydtrader-aeron-mvp-java-0.8.0-linux-x86_64.tar.gz
cd ydtrader-aeron-mvp-java-0.8.0-linux-x86_64
sha256sum -c manifest.sha256
bash scripts/run_aeron_mvp.sh --count 100000 --sync-level 0
```

成功时必须同时出现：

```text
AERON_MVP_ACCEPTANCE result=SUCCESS
AERON_MVP_ACCEPTANCE live_compute=SUCCESS live_audit=SUCCESS replay_match=YES
AERON_MVP_ACCEPTANCE archive_restart=SUCCESS consumer_restart=SUCCESS
```

百万条验收：

```bash
bash scripts/run_aeron_mvp.sh --count 1000000 --sync-level 0
```

更强的 Archive 刷盘配置：

```bash
bash scripts/run_aeron_mvp.sh --count 100000 --sync-level 1
```

运行结果和 recording 保存在包内 `result/aeron-mvp/<run-id>/`。

## 多行情源同时接入

每个行情源使用一份独立 JSON 配置，并重复传入 `--source-config`。当前类型为 `synthetic`、
`ctp`、`ydapi`。下面的离线命令同时启动两个模拟源：

```bash
bash scripts/run_multi_source_aeron_mvp.sh \
  --source-config config/market-sources/synthetic-sim-a.json \
  --source-config config/market-sources/synthetic-sim-b.json \
  --count 1000
```

真实源应从 `config/market-sources/*.example.json` 建立部署配置，每份配置独立指定 runtime、
连接端点、合约、账号文件、repeat 和超时。`--count` 按每个源计算。每个源独立校验输入 sequence，
合并后共用一条全局 sequence、一份 Archive recording 和一个 ZMQ 出口；任一源失败都会令验收
失败。多源 mux、bridge 和下游项目互不导入对方源码。旧 `--sources` 共享参数模式只用于兼容。

## 通用 ZMQ/SBE 行情出口

本版提供独立消费支路：Aeron/Archive 保持原职责，Java/JeroMQ 通过 TCP `PUSH`
发送 `snapshot` topic 与未改写的 SBE v3 bytes。下游项目通过自己的 `PULL` 接口接收；
本包不携带任何下游业务源码、队列实现或运行时钩子。

```bash
bash scripts/run_zmq_market_smoke.sh 1000
```

该命令同时验证实时订阅和 Archive replay，成功时输出 `live_replay_match=YES`。
正式运行时先启动下游 `PULL`，再在多行情源命令中启用出口：

```bash
bash scripts/run_multi_source_aeron_mvp.sh \
  --source-config config/market-sources/ydapi.json config/market-sources/ctp-live.json \
  --zmq-endpoint tcp://0.0.0.0:7101
```

主脚本负责复用同一个动态 Aeron 目录；不要使用 `sudo` 或另写固定的 `/dev/shm` 目录。

## 实时 Dashboard（可选）

目标机已安装 Miniconda 时，先创建项目环境，再在另一个终端启动：

```bash
conda env update -f environment.yml --prune
conda activate ydtrader-high-concurrency
bash scripts/run_dashboard.sh --port 8080
```

浏览器访问 `http://127.0.0.1:8080`。Dashboard 只读取下游实时快照和最终 summary，不会
进入行情发布或 Aeron flow-control 链路。“最新采样行情”可查看合约、行情时间、最新价、
一到五档买卖价和数量；档位可选，数据源未提供的档位显示为空。
YDApi 当前只有一档，CTP 支持五档。全量原始行情仍保存在 Archive。
Dashboard 还按 15 分钟行情时间窗口和合约展示
`count / mean / std / p50 / p90 / p95 / p99 / max`，分位数由下游 Compute 的
HdrHistogram 在线聚合，不在发布链路上执行，也不会逐笔写盘。

## 当前边界

本版用于确认 Aeron IPC、Archive 持久化、双消费者、实时/离线一致性、重启恢复，以及
通用 ZMQ/SBE 行情出口。
默认 publisher 使用确定性模拟行情；可选 YDApi bridge 已能连接真实易达行情。本版不包含 C++ SPSC bridge、
Dashboard 告警推送、systemd 服务或 `secure_release_toolkit`。

在包目录中创建/更新 Conda 环境，放入真实 `config/account.json` 和
`config/ydClient.ini` 后，可运行：

```bash
conda env update -f environment.yml --prune
conda activate ydtrader-high-concurrency
bash scripts/run_ydapi_aeron_mvp.sh \
  --instrument IF2609 --count 1000000 --ydapi-repeat 10000
```

发行包保留官方 `pyyd` wheel 原始文件，不携带真实账号、密码或 `ydClient.ini`。

包内附带可选的 OpenCTP Python bridge，但 Python 与原生 wheel 不包含在自包含 Java
运行时中。目标机需要用户级 Miniconda、`curl`、`tar`、`unzip` 和 `localedef`。

交易时段优先使用官方 CTP 实时行情前置。下面的命令会创建独立 runtime，固定校验 SDK
压缩包与行情 `.so`，连接实时模拟行情并把最新快照送到 Dashboard：

```bash
bash scripts/run_ctp_live_aeron_mvp.sh --count 1000000 --ctp-repeat 10000
```

该入口使用真实的当日市场行情快照，但属于模拟行情环境，不是生产交易柜台，也不是 YDApi。
休市或需要 7x24 回放时，以下命令会创建另一个隔离 Python
Conda runtime，并下载、校验和启用同版本官方 TTS 行情库：

```bash
bash scripts/bootstrap_ctp_tts.sh
bash scripts/run_ctp_aeron_mvp.sh --count 1000000 --ctp-repeat 1000000
```

该模式使用真实 CTP Tick 驱动，并在 Java 入口按 repeat 放大压力；日志分别报告真实源 Tick
数和 Aeron 发布数。OpenCTP TTS 不能使用 CTP 原厂动态库，否则会出现断线原因 `4097`。
7x24 重放行情不能用于判断实时交易所网络延迟。
Dashboard 会对默认 OpenCTP 7x24 前置隐藏这类历史时间差，避免误认为实时链路延迟。
