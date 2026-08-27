# YDTrader 高并发行情系统架构图

```mermaid
flowchart TB
    subgraph ACCESS["① 行情接入层"]
        direction LR
        YD["YDApi 行情接口<br/>正式数据源｜规划接入"]
        CTP["OpenCTP / CTP 7×24<br/>当前测试数据源"]
        SIM["Synthetic Publisher<br/>确定性压力测试"]
        YDA["YDApi Adapter<br/>未来替换最上游适配器"]
        CTB["Python CTP Bridge<br/>原生回调轻量转发"]
        UDP["156 Byte 固定二进制包<br/>UDP Loopback"]
        JP["Java Market Publisher<br/>publish / publish-ctp"]

        YD -.-> YDA
        YDA -.-> JP
        CTP --> CTB --> UDP --> JP
        SIM --> JP
    end

    subgraph BUS["② 高并发传输与持久化层"]
        direction LR
        VALID["接入校验<br/>Session / Sequence / Timestamp"]
        SBE["SBE MarketQuote<br/>固定二进制消息模型"]
        AP["Aeron IPC Publisher<br/>非阻塞 Offer"]
        MD["Aeron Media Driver<br/>共享内存高速传输"]
        AR["Aeron Archive<br/>Recording + Catalog"]
        DISK[("本地持久化存储<br/>Archive Segment")]
        RETRY["发布线程有限重试<br/>不阻塞行情回调"]

        JP --> VALID --> SBE --> AP --> MD --> AR --> DISK
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
    end

    subgraph OUTPUT["④ 统计输出与可视化层"]
        direction LR
        STAT["统计结果<br/>Mean / Std / P95 / Max"]
        QUALITY["质量指标<br/>Gap / Duplicate / Invalid"]
        SUMMARY[("Summary / Log<br/>当前已实现")]
        API["Metrics API / WebSocket<br/>规划"]
        DASH["实时监控 Dashboard<br/>曲线 / 统计表 / 告警｜规划"]

        COMPUTE --> STAT --> SUMMARY
        AUDIT --> QUALITY --> SUMMARY
        REPLAY --> STAT
        STAT -.-> API
        QUALITY -.-> API
        API -.-> DASH
    end

    subgraph FOUNDATION["运行与基础设施底座"]
        direction LR
        LINUX["Linux / WSL 2"]
        JAVA["Java 17 Runtime"]
        PY["隔离 Python 3.9<br/>OpenCTP TTS 6.7.11"]
        CONDA["Conda 环境<br/>environment.yml"]
        DEPS["项目本地依赖缓存<br/>result/aeron-mvp-deps"]
        OBS["运行日志与验收证据<br/>result/aeron-mvp"]
    end

    LINUX --- CTB
    LINUX --- MD
    JAVA --- JP
    JAVA --- COMPUTE
    PY --- CTB
    CONDA --- PY
    DEPS --- AP
    OBS --- SUMMARY
```

## 图例与边界

- 实线表示当前已经实现并完成验收的链路，虚线表示下一阶段规划。
- OpenCTP/CTP 是 YDApi 休市期间的测试行情入口，不改变正式架构的数据源定位。
- 正式接回 YDApi 时只替换最上游 Adapter；SBE、Aeron、Archive、计算、审计和重放链路保持不变。
- Dashboard 当前属于规划范围，现阶段结果通过 summary 和日志输出。
