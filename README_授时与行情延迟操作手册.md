# 授时与行情延迟操作手册

本流程只处理时钟一致性和行情延迟测量，不需要项目许可证、激活或运行密码。

## 一、统一授时基准

Windows 和 Linux/WSL 必须使用同一权威授时源。仓库提供：

- `config/time_authority.cffex.example.conf`：中金所示例配置。
- `config/time_authority.tencent-south-china-fallback.conf`：华南腾讯云备用。
- `scripts/setup_linux_time_sync.sh`：Linux 授时配置助手。
- `scripts/linux_time_report.sh`：Linux JSON 报告。
- `scripts/windows_time_sync.ps1`：Windows 授时与 JSON 报告。
- `scripts/compare_time_reports.ps1`：比较两端报告。

不要混用不同上游，也不要在 VPN 或代理改变网络路径后沿用旧报告。

## 二、生成并比较授时报告

Windows 管理员 PowerShell：

```powershell
.\scripts\windows_time_sync.ps1
```

WSL/Linux：

```bash
cd ~/projects/high-concurrency
conda activate ydtrader-high-concurrency
scripts/linux_time_report.sh
```

回到 PowerShell 比较两份最新报告：

```powershell
.\scripts\compare_time_reports.ps1
```

只有输出 `RESULT: PASS`，才能解释跨机器或跨数据源的行情延迟结果。失败时先修复授时和网络路径，再重新生成两份报告。

## 三、运行行情延迟链路

确定性高并发基线：

```bash
bash scripts/run_aeron_mvp.sh --count 100000 --sync-level 0
```

YDApi、官方 CTP 实时模拟与 OpenCTP TTS 入口如下；数据源、合约、重复次数和前置地址参数以 `aeron_mvp/README.md` 为准：

```bash
bash scripts/run_ydapi_aeron_mvp.sh
bash scripts/run_ctp_live_aeron_mvp.sh
bash scripts/run_ctp_aeron_mvp.sh
```

YDApi 和 CTP 柜台自身要求的账号密码仍由各自本机配置提供；这是数据源登录，不是项目加密。

## 四、读取结果

运行产物位于 `result/aeron-mvp/<run-id>/`。重点检查：

- Compute 与 Audit 的 summary 是否完成。
- `gap`、`duplicate`、`invalid` 是否为零或符合预期。
- 按 15 分钟窗口与合约分组的 `count / mean / std / p50 / p90 / p95 / p99 / max`。
- Archive 录制、离线重放和消费者重启恢复是否通过。
- Dashboard API 是否读取到最新 NDJSON 与 summary。

不要比较来源不同、交易时段不同、链路模式不同或授时未通过的数据。

## 五、常见故障

- 没有行情：确认交易时段、合约、数据源登录和订阅回调。
- 延迟为负或跳变：重新检查授时源、VPN、WSL 网络模式和系统时钟。
- 丢包或序列缺口：检查 bridge 日志、UDP adapter、Aeron offer 重试与消费者状态。
- Archive 无结果：检查录制目录权限和磁盘空间。
- Dashboard 空白：先确认下游 NDJSON/summary 已生成，再检查只读 API。
