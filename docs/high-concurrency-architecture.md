# YDTrader 高并发行情系统架构图

```mermaid
flowchart TB
    subgraph ACCESS["① 行情接入层"]
        direction LR
        YD["YDApi 行情接口<br/>正式数据源｜当前已接入"]
        CTPL["官方 CTP 实时模拟行情<br/>当前实时测试数据源"]
        CTP["OpenCTP TTS 7×24<br/>历史回放备用源"]
        SIM["Synthetic Publisher<br/>确定性压力测试"]
        YDA["Python YDApi Bridge<br/>非阻塞回调转发"]
        CTB["Python CTP Bridge<br/>原生回调轻量转发"]
        UDP["v2 284 Byte 五档二进制包<br/>兼容 v1 156 Byte｜UDP Loopback"]
        MUX["Multi-source Mux<br/>独立源 Sequence 校验｜来源标记｜全局排序"]
        JP["Java Market Publisher<br/>publish / publish-adapter"]

        YD --> YDA --> UDP --> MUX
        CTPL --> CTB
        CTP --> CTB --> UDP
        MUX -->|并发列表模式| JP
        UDP -->|单源模式| JP
        SIM --> JP
        SIM -->|并发列表模式| MUX
    end

    subgraph BUS["② 高并发传输与持久化层"]
        direction LR
        VALID["接入校验<br/>Session / Sequence / Timestamp"]
        SBE["SBE MarketQuote v3<br/>完整行情字段｜兼容 v1/v2"]
        AP["Aeron IPC Publisher<br/>非阻塞 Offer"]
        MD["Aeron Media Driver<br/>共享内存高速传输"]
        AR["Aeron Archive<br/>Recording + Catalog"]
        DISK[("本地持久化存储<br/>Archive Segment")]
        RETRY["发布线程有限重试<br/>不阻塞行情回调"]
        LZA["ZMQ Market Egress<br/>Aeron SBE 原帧｜PUSH"]

        JP --> VALID --> SBE --> AP --> MD --> AR --> DISK
        MD --> LZA
        AP -.-> RETRY
        RETRY --> AP
    end

    subgraph COMPUTING["③ 实时计算、审计与恢复层"]
        direction LR
        LIVE["Open-ended Replay<br/>边写边读"]
        COMPUTE["Compute JVM<br/>实时统计计算"]
        AUDIT["Audit JVM<br/>完整性审计"]
        REPLAY["Offline Replay<br/>离线重算"]
        RECOVERY["故障恢复验证<br/>Archive / Consumer 重启"]

        AR --> LIVE
        LIVE --> COMPUTE
        LIVE --> AUDIT
        AR --> REPLAY
        AR --> RECOVERY --> COMPUTE
        AR -->|显式 replay| LZA
    end

    subgraph DOWNSTREAM["⑤ 独立下游项目（单独部署）"]
        direction LR
        ZPULL["项目自有行情输入接口<br/>ZMQ PULL｜TCP 7101"]
        DECODE["SBE v3 解码与连续性校验<br/>sessionId + sequence"]
        MDQ["项目自有队列适配器"]
        ENGINE["下游业务处理<br/>不反向依赖高并发源码"]

        LZA -->|topic=snapshot + SBE bytes| ZPULL --> DECODE --> MDQ --> ENGINE
    end

    subgraph OUTPUT["④ 统计输出与可视化层"]
        direction LR
        STAT["统计结果<br/>Mean / Std / P95 / Max"]
        QUALITY["质量指标<br/>Gap / Duplicate / Invalid"]
        STREAM[("NDJSON 实时快照<br/>250ms｜当前已实现")]
        SUMMARY[("Summary / Log<br/>当前已实现")]
        API["只读 HTTP Metrics API<br/>JSON 轮询｜当前已实现"]
        DASH["实时监控 Dashboard<br/>曲线 / 统计表 / 最新行情采样｜当前已实现"]

        COMPUTE --> STAT --> STREAM
        AUDIT --> QUALITY --> STREAM
        STAT --> SUMMARY
        QUALITY --> SUMMARY
        REPLAY --> STAT
        STREAM --> API
        SUMMARY --> API
        API --> DASH
    end

    subgraph FOUNDATION["运行与基础设施底座"]
        direction LR
        LINUX["Linux / WSL 2"]
        JAVA["Java 17 Runtime"]
        PY["隔离 Python 3.9<br/>YDApi / 官方 CTP / OpenCTP TTS"]
        CONDA["Conda 环境<br/>environment.yml"]
        DEPS["项目本地依赖缓存<br/>result/aeron-mvp-deps"]
        OBS["运行日志与验收证据<br/>result/aeron-mvp"]
    end

    LINUX --- CTB
    LINUX --- YDA
    LINUX --- MD
    JAVA --- JP
    JAVA --- COMPUTE
    PY --- CTB
    PY --- YDA
    CONDA --- PY
    DEPS --- AP
    OBS --- SUMMARY
```

## 图例与边界

- 实线表示当前已经实现并完成验收的链路，虚线表示下一阶段规划。
- YDApi 已通过专用 bridge 接入固定 UDP adapter 协议，并共用 SBE、Aeron、Archive、计算、审计和重放链路。
- 统一入口通过 `--source-config <独立 JSON>...` 接受一到多份配置，分别配置 runtime、连接端点、
  合约、repeat 和超时，再启动对应的独立 bridge。每个源在自己的 loopback UDP 端口上维护并校验
  输入 sequence；Multi-source Mux 写入来源标签并为合并流分配全局 sequence。旧的 `--sources`
  共享参数模式只为命令兼容保留，新部署不使用它。
  合并后仍只有一条 Aeron publication、一份 Archive recording 和一个 ZMQ 出口，避免复制持久化与恢复链路。
- 任一来源缺包、断流或提前退出都会使整次多源运行失败。Compute 输出 `latency_by_source`，
  Dashboard 同时展示本次连接的来源列表和按源统计。
- 统一消息模型预留五档：YDApi 仅填一档，CTP 可填五档，缺失档位保持空值。
- 通用接口开在高并发传输与持久化层的 Aeron 出口：实时模式订阅原始 IPC stream，恢复模式
  读取同一 recording 的 replay。这样下游收到的是已经完成接入校验、可以审计和重放的规范 SBE
  行情，又不会让下游处理速度反向参与 Aeron publisher 的 flow control。
- 跨进程边界使用原生 ZMTP TCP `PUSH/PULL`，不是让 Aeron“改成 ZMQ”。Aeron 继续负责共享内存
  高速传输和 Archive 持久化，ZMQ 只负责把一个独立消费支路送到另一个进程或服务器。
- 两侧只共享版本化 SBE/ZMQ 线协议。高并发项目不导入下游包；下游项目也不导入高并发包，
  各自在自己的仓库实现出口或输入接口，因此可以分别构建、打包、升级和回滚。
- ZMQ HWM 不是无损承诺。适配器在发送超时、SBE 损坏或 sequence 缺口时立即失败并留下 checkpoint，
  运维应从 Archive 显式 replay 恢复，不能静默丢行情后继续运行。
- 官方 CTP 实时模拟行情用于备用快照验收；OpenCTP TTS 仅作 7×24 历史回放备用。
- Dashboard 当前从 Compute/Audit 的下游快照和最终 summary 读取数据，不订阅 Publisher，
  不参与 Aeron flow control；告警推送和 WebSocket 属于后续规划。
- Compute 使用 HdrHistogram 在线维护按中国时区 15 分钟行情窗口和按合约的延迟分布，
  输出 `count / mean / std / p50 / p90 / p95 / p99 / max`，不写逐笔延迟明细。

## 无加密运行边界

- 项目不再包含许可证、机器绑定、运行密码、激活、到期销毁或加密状态；业务入口直接分发到原有模块。
- UDP loopback、SBE、Aeron IPC、Archive、NDJSON 和 Dashboard API 均不增加项目级加密层，消息结构与并发路径保持不变。
- YDApi/CTP 的柜台账号密码仍只从数据源主机配置读取，用于登录数据源或交易柜台，不写入 SBE 消息、Aeron Archive、统计结果或 Dashboard。
- 去掉项目加密不等于去掉交易风控；真实报单的 `--send`、策略身份、暂停标志、报单和撤单阈值继续生效。
