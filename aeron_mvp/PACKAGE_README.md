# YDTrader Aeron MVP Java 0.3.0

这是 Linux x86_64 自包含验收包，内含精简 Java 17 运行时、Aeron 1.51.0、已编译
SBE codec 和行情 MVP。目标机不需要安装 Java，也不需要联网下载依赖。

## 快速验收

```bash
tar -xzf ydtrader-aeron-mvp-java-0.3.0-linux-x86_64.tar.gz
cd ydtrader-aeron-mvp-java-0.3.0-linux-x86_64
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

## 实时 Dashboard（可选）

目标机已有 Python 3.9+ 时，可在另一个终端启动：

```bash
bash scripts/run_dashboard.sh --port 8080
```

浏览器访问 `http://127.0.0.1:8080`。Dashboard 只读取下游实时快照和最终 summary，不会
进入行情发布或 Aeron flow-control 链路。“最新采样行情”可查看合约、行情时间、最新价、
买一/卖一和数量；全量原始行情仍保存在 Archive。

## 当前边界

本版用于确认 Aeron IPC、Archive 持久化、双消费者、实时/离线一致性以及重启恢复。
默认 publisher 使用确定性模拟行情，尚未连接真实 YDApi；也不包含 C++ SPSC bridge、
Dashboard 告警推送、systemd 服务或 `secure_release_toolkit`。

包内附带可选的 OpenCTP Python bridge，但 Python 与原生 wheel 不包含在自包含 Java
运行时中。目标机需要 `uv`、`curl`、`tar`、`unzip` 和 `localedef`。

交易时段优先使用官方 CTP 实时行情前置。下面的命令会创建独立 runtime，固定校验 SDK
压缩包与行情 `.so`，连接实时模拟行情并把最新快照送到 Dashboard：

```bash
bash scripts/run_ctp_live_aeron_mvp.sh --count 1000000 --ctp-repeat 10000
```

该入口使用真实的当日市场行情快照，但属于模拟行情环境，不是生产交易柜台，也不是 YDApi。
休市或需要 7x24 回放时，以下命令会创建另一个隔离 Python
runtime，安装固定的 `openctp-ctp==6.7.11.0`，并下载、校验和启用同版本官方 TTS 行情库：

```bash
bash scripts/bootstrap_ctp_tts.sh
bash scripts/run_ctp_aeron_mvp.sh --count 1000000 --ctp-repeat 1000000
```

该模式使用真实 CTP Tick 驱动，并在 Java 入口按 repeat 放大压力；日志分别报告真实源 Tick
数和 Aeron 发布数。OpenCTP TTS 不能使用 CTP 原厂动态库，否则会出现断线原因 `4097`。
7x24 重放行情不能用于判断实时交易所网络延迟。
Dashboard 会对默认 OpenCTP 7x24 前置隐藏这类历史时间差，避免误认为实时链路延迟。
