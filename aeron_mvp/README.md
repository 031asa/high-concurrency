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
- 实时计算和停止后的完整重放结果逐字段一致。

MVP 不包含真实 YDApi adapter、C/Cython SPSC bridge、Web Dashboard、跨机器 UDP、HA
或自动销毁。CTP 适配器用于在 YDApi 无行情时验证同一条下游链路；正式接回 YDApi 时
保持 schema、stream ID、Archive 和消费者不变，只替换最上游行情 adapter。

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

运行证据和持久化 recording 保存在 `result/aeron-mvp/<run-id>/`。实时统计剔除每个
producer session 的第一条有效行情；`std_ms` 为样本标准差，`p95_ms` 使用 nearest-rank。

## OpenCTP 7x24 行情验收

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
  --ctp-repeat 10000 \
  --sync-level 0
```

Linux wheel 内部使用 `zh_CN.GB18030`；引导脚本通过 `localedef` 自动把该 locale 生成到
隔离 runtime 并设置进程级 `LOCPATH`，无需 sudo，也不修改系统 locale。

默认行情前置是 `tcp://trading.openctp.cn:30011`。`--ctp-instruments` 接受逗号分隔的
合约列表，`--ctp-front` 可覆盖行情前置。每个真实 Tick 放大 `--ctp-repeat` 条消息，目的
是验证 Java/Aeron/Archive 的高并发能力，而不是声称 CTP 前置本身产生了同等 Tick 速率。
验收日志同时输出 `source_ticks` 和 `published`，两者不能混为一谈。

OpenCTP 7x24 环境可能重放历史交易日，因此其 market timestamp 适合验证字段传输与计算
稳定性，不适合衡量当前机器到真实交易所的实时链路延迟。

## 构建自包含发布包

```bash
bash aeron_mvp/build_release.sh
```

生成 `result/ydtrader-aeron-mvp-java-0.1.1-linux-x86_64.tar.gz`，包含精简 Java 17
运行时和 Aeron runtime；目标 Linux x86_64 主机无需预装 Java或联网下载依赖。
