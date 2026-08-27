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

浏览器打开 `http://127.0.0.1:8080`，再在另一个终端运行任一 Aeron 验收命令。页面会自动
选取 `result/aeron-mvp/` 中最新的 run，并每秒刷新。服务默认只监听本机；确需供局域网查看时
显式传入 `--host 0.0.0.0`，并由主机防火墙限制访问范围。

Dashboard 只读取 Compute/Audit 已写出的 NDJSON 和 summary，不订阅发布流、不参与 Aeron
flow control。页面的“最新采样行情”固定携带五档槽位，可选显示 1～5 档；
YDApi 只填写一档，CTP 最多填写五档，任何缺失档位都输出空值而不伪造数据。
全量行情仍只保存在 Archive。实时阶段显示 mean/std/max；
精确 P95 在计算完成后由同一套统计代码写入最终快照。

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
编码为固定 156 字节 UDP 包，只发送到 `127.0.0.1`；Java 校验包头和连续 source sequence，
再转换为原有 SBE `MarketQuote` 并写入 Aeron Archive。

OpenCTP 官方说明：TTS 柜台虽然兼容 CTPAPI，但必须把 CTP 原厂 `dll/so` 替换为同版本
TTS 动态库；若误用原厂库，`OnFrontDisconnected` 会持续报告 `4097`。先运行固定版本、
固定校验值的隔离环境引导脚本：

```bash
bash scripts/bootstrap_ctp_tts.sh
```

脚本创建 `result/ctp-tts-runtime/venv`，安装 `openctp-ctp==6.7.11.0`，下载并校验官方
`tts_6.7.11.zip`，只替换这个隔离虚拟环境中的行情 `.so`。原项目 `.venv` 不受影响，
因此以后仍可单独使用 CTP 原厂柜台。需要离线安装时可通过 `TTS_SDK_ZIP` 指向已经下载
的官方 ZIP，通过 `UV_BIN` 指定 `uv`。

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

生成 `result/ydtrader-aeron-mvp-java-0.5.0-linux-x86_64.tar.gz`，包含精简 Java 17
运行时、Aeron runtime 和 Dashboard 静态资源；核心验收无需预装 Java或联网下载依赖，
Dashboard 另需目标机已有 Python 3.9+。
