# 授时与行情延迟操作手册

本手册是本项目唯一有效的授时与行情延迟验收流程。Windows 的 `W32Time` 和 Linux 的 `chrony` 必须各自直接连接同一个授时中心；Linux 不得跟随 Windows 的 `PHC0`。只有最终比较显示 `RESULT: PASS`，行情时间差才有解释价值。

## 一、选择同一个授时中心

广州及华南开发联调优先使用 `config/time_authority.tencent-south-china-fallback.conf`：

- 授时中心：Tencent Cloud Public NTP - South China fallback
- NTP：`ntp4.tencent.com`、`ntp5.tencent.com`、`ntp2.tencent.com`
- 官方文档：[腾讯云 NTP 服务概述](https://cloud.tencent.com/document/product/213/30392)
- 环境：`test`

这三个域名是腾讯云官方公网源；在当前华南网络的5次UDP实测中，往返约7–16 ms。公网域名可能因运营商和DNS调度到不同节点，因此它表示“当前网络低延迟的华南替代源”，不保证固定在某一广州机房。Cloudflare 的 `config/time_authority.cloudflare-test.conf` 仅保留作跨运营商故障对照，不再作为华南默认源。

正式交付使用 `config/time_authority.cffex.example.conf`：

- 授时中心：中国金融期货交易所
- 官网：[中国金融期货交易所](https://www.cffex.com.cn/)
- 环境：`production`

中金所生产模板故意没有真实 NTP 地址。生产验收前，必须从负责人或中金所官方技术文档取得 NTP 域名/IP和对应文档链接，用真实地址替换 `REPLACE_WITH_CFFEX_NTP_HOST_OR_IP`，再把同一份配置分别交给 Windows 和 Linux。脚本遇到占位符会拒绝运行。没有取得中金所地址时可使用腾讯华南替代配置完成设备间校时和行情联调，但报告必须保持 `environment=test`，不得宣称它测得的是中金所时钟偏差，也不得把腾讯或Cloudflare地址填入中金所生产模板。

配置中的 `ntp_servers` 可用空格填写同一授时中心的多个域名/IP。默认单机偏差 `max_offset_ms=50`，Windows/Linux 差异 `max_cross_difference_ms=50`。

## 二、当前 Windows＋WSL 开发机

先把 Windows 主项目同步到 WSL 检查副本。以下命令中的项目路径按本机实际位置执行。

### 窗口和权限速查（必须先看）

| 操作 | 在哪里执行 | 权限 |
|---|---|---|
| Windows首次配置或重新应用NTP | Windows PowerShell | **必须以管理员身份运行** |
| 生成Windows JSON报告 | Windows PowerShell | **必须以管理员身份运行**，否则可能无法读取W32Time活动源并返回FAIL |
| Linux首次配置或重新应用chrony | WSL终端 | 普通登录后使用 `sudo` |
| 生成Linux JSON报告 | WSL终端 | 普通用户，不使用 `sudo` |
| 复制Linux报告、比较两个JSON | Windows PowerShell | 普通权限即可 |
| 运行行情测试 | WSL/Linux终端 | 普通用户，不使用 `sudo` |

Windows管理员窗口的打开方法：在开始菜单搜索“PowerShell”，右键选择“以管理员身份运行”，看到用户账户控制提示后选择“是”。不要把PowerShell命令粘贴到WSL，也不要把Linux命令粘贴到PowerShell。

**【管理员 Windows PowerShell｜必须】第一次配置Windows：**

```powershell
cd "C:\Users\Hello\Documents\基础环境配置\outputs\share\share\yd_trader"
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\windows_time_sync.ps1 `
  -Config .\config\time_authority.tencent-south-china-fallback.conf `
  -Apply `
  -Output .\windows-time.json
```

`-Apply` 会把 W32Time 配到配置文件中的 NTP，并固定 `UpdateInterval=100`。只有实测偏差超过50 ms时才临时执行一次立即校正，`finally` 会恢复原跳时阈值。

**【WSL终端｜命令内含sudo】第一次配置Linux：**

```bash
cd ~/projects/yd_trader
sudo ./scripts/setup_linux_time_sync.sh --config ./config/time_authority.tencent-south-china-fallback.conf
```

该命令会让 chrony 只使用指定网络授时中心，移除内容完全匹配项目旧版本的 `ydtrader-windows-host.conf`，并在 WSL 中设置必要的 `SYNC_IN_CONTAINER=yes`。它会立即校时；不会配置 `PHC0`。

配置完成后，在60秒内依次生成两端只读报告。

**【管理员 Windows PowerShell｜仍然必须】生成Windows报告：**

```powershell
cd "C:\Users\Hello\Documents\基础环境配置\outputs\share\share\yd_trader"
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\windows_time_sync.ps1 `
  -Config .\config\time_authority.tencent-south-china-fallback.conf `
  -Output .\windows-time.json
```

不带 `-Apply` 虽然不会修改Windows时间，但脚本仍要读取W32Time活动源；普通PowerShell可能得到 `UNAVAILABLE`，从而出现 `W32Time active source is not the configured authority`。

**【普通WSL终端｜不要sudo】紧接着生成Linux报告：**

```bash
cd ~/projects/yd_trader
./scripts/linux_time_report.sh \
  --config ./config/time_authority.tencent-south-china-fallback.conf \
  --output ./linux-time.json
```

**【普通Windows PowerShell】把WSL报告复制回Windows项目目录：**

```powershell
Copy-Item "\\wsl.localhost\Ubuntu-24.04\home\hello\projects\yd_trader\linux-time.json" .\linux-time.json -Force
```

**【普通Windows PowerShell】然后比较：**

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\compare_time_reports.ps1 `
  -WindowsReport .\windows-time.json `
  -LinuxReport .\linux-time.json
```

必须同时满足：中心名称、官网、NTP列表和环境完全一致；两个报告自身均为 PASS；UTC采样时刻相差不超过60秒；最终差异不超过50 ms。成功时最后一行是 `RESULT: PASS`，退出码为0。

## 三、leader 原生 Linux

leader 原生 Linux 不启用 WSL 兼容逻辑。先复制生产模板为本次正式配置，并填入中金所官方 NTP 地址；Windows 必须使用内容完全相同的配置。

**【leader普通Linux终端；配置chrony的命令使用sudo】Linux首次配置：**

```bash
cd /path/to/ydtrader-linux-x86_64
cp config/time_authority.cffex.example.conf config/time_authority.cffex.conf
vi config/time_authority.cffex.conf
sudo ./tools/setup_linux_time_sync.sh --config ./config/time_authority.cffex.conf
```

**【管理员Windows PowerShell】** 按第二节的命令运行 `windows_time_sync.ps1 -Apply`，只是把 `-Config` 换成同一份中金所配置。后续生成Windows JSON时也必须继续使用管理员PowerShell。确认两端 UDP 123 可达后，在60秒内分别生成。

**【leader普通Linux终端｜不要sudo】生成Linux报告：**

```bash
./tools/linux_time_report.sh --config ./config/time_authority.cffex.conf --output ./linux-time.json
```

**【管理员Windows PowerShell】** 生成 `windows-time.json`。人工传递两个 JSON 文件，不使用 SSH 或共享凭据；再使用**普通Windows PowerShell**在Windows主项目目录运行 `compare_time_reports.ps1`。必须看到 `RESULT: PASS`。

## 四、运行 IC2609 行情延迟测试

只有授时比较通过后，才在已经安装、激活且配置好柜台账号的 Linux 环境执行。

**【普通WSL/Linux终端｜不要sudo】行情测试：**

```bash
cd /opt/ydtrader
./ydtrader marketdata --instrument IC2609 --duration-seconds 30 --max-quotes 100
```

按提示输入业务运行密码。结束时查看收到行情数、有效样本数、平均延迟（绝对值）、最小延迟、最大延迟和“本机减行情”的平均时间差。本流程不执行真实报单或撤单。

若任一授时报告或最终比较为 FAIL，立即停止：不得把行情统计解释为真实网络单向延迟。行情时间戳还可能包含交易所撮合/行情网关、柜台转发和时间戳粒度等因素，即使授时通过，汇总也不是纯网络单向延迟。

## 五、故障判定

- Windows 活动源显示 `Local CMOS Clock`：W32Time 没有使用网络源，用管理员 PowerShell重新执行 `-Apply`。
- Linux 选中源为 `PHC0`、模式为 `#` 或没有选中源：仍在使用旧配置或网络 NTP不可达，重新执行 Linux 配置脚本并检查 UDP 123、DNS与 `chronyc sources -v`。
- 配置中心、官网或NTP列表不一致：复制同一份配置到两端，不要只凭服务器看起来相近就继续。
- 报告相隔超过60秒：不需要重新配置，只需在60秒内重新生成两份只读 JSON。
- 任一单端偏差或最终差异超过50 ms：先解决授时，不能通过修改报告或放宽阈值来认可本次行情结果。
