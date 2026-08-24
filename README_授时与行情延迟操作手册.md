# 授时与行情延迟操作手册

本手册是本项目唯一有效的授时与行情延迟验收流程。Windows 使用 **Meinberg ntpd**，Linux 使用 `chrony`，两端必须各自直接连接同一个授时中心；Linux 不得跟随 Windows 的 `PHC0`。Windows 的 `W32Time` 必须保持停止和禁用，避免两个服务同时调整系统时钟。只有最终比较显示 `RESULT: PASS`，行情时间差才有解释价值。

## 一、选择同一个授时中心

广州及华南开发联调优先使用 `config/time_authority.tencent-south-china-fallback.conf`：

- 授时中心：Tencent Cloud Public NTP - pinned leader endpoint
- NTP：固定IP `106.55.184.199`（当前对应腾讯公网NTP `ntp.tencent.com`）
- 固定IP用于避免VPN改变DNS或地域解析，交付前必须根据腾讯官方NTP文档重新确认；它不是中金所授时源。
- 官方文档：[腾讯云 NTP 服务概述](https://cloud.tencent.com/document/product/213/30392)
- 环境：`test`

`106.55.184.199` 是本次与leader统一的腾讯公网NTP端点，当前也由腾讯官方域名 `ntp.tencent.com` 和旧域名 `time1.cloud.tencent.com` 解析得到。固定字面IP只能消除两端DNS解析差异，不能绕过VPN、保证公网路径对称或承诺5ms精度；交付前必须重新确认该IP仍属于腾讯官方NTP。Cloudflare 的 `config/time_authority.cloudflare-test.conf` 仅保留作跨运营商故障对照，不再作为华南默认源。

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
| 检查部署是否被24小时定时器销毁 | WSL终端 | 普通用户 |
| 重新安装程序、恢复柜台配置 | WSL终端 | 命令使用 `sudo` |
| 取得机器码、签发新许可证 | WSL终端 | 普通用户，不使用 `sudo` |
| 激活新许可证 | WSL终端 | 命令使用 `sudo` |
| 安装Meinberg NTP、首次配置或重新应用NTP | Windows PowerShell | **必须以管理员身份运行**；UAC选择“是” |
| 配置或重启WSL mirrored网络 | Windows PowerShell | 普通权限即可，会关闭全部WSL会话 |
| 生成Windows JSON报告、运行 `ntpq -pn` | Windows PowerShell | 普通权限即可，不使用管理员窗口 |
| Linux首次配置或重新应用chrony | WSL终端 | 普通登录后使用 `sudo` |
| 生成Linux JSON报告 | WSL终端 | 普通用户，不使用 `sudo` |
| 复制Linux报告、比较两个JSON | Windows PowerShell | 普通权限即可 |
| 运行行情测试 | WSL/Linux终端 | 普通用户，不使用 `sudo` |

Windows管理员窗口的打开方法：在开始菜单搜索“PowerShell”，右键选择“以管理员身份运行”，看到用户账户控制提示后选择“是”。不要把PowerShell命令粘贴到WSL，也不要把Linux命令粘贴到PowerShell。

### VPN开启时使用WSL mirrored网络

当前开发机需要保持VPN以便沟通或访问其他服务时，在Windows用户目录 `%USERPROFILE%\.wslconfig` 使用以下配置：

```ini
[wsl2]
networkingMode=mirrored
dnsTunneling=true
autoProxy=true
```

修改后在**普通Windows PowerShell**执行：

```powershell
wsl --shutdown
```

该命令会立即关闭全部WSL终端和WSL内进程。重新打开Ubuntu后验证：

```bash
wslinfo --networking-mode
```

必须输出 `mirrored`。镜像网络用于改善WSL与VPN的兼容性，不代表NTP流量会绕过VPN；固定IP `106.55.184.199` 仍可能经过VPN。切换网络模式后的第一次chrony校时必须保持同一个WSL终端持续打开至少2分钟，避免WSL反复启动导致时钟模型无法收敛。

### 24小时到期后的重新安装与激活

许可证从首次激活开始默认只有效24小时。到期后 `/opt/ydtrader` 通常会被销毁；Windows主项目、`~/projects/yd_trader`源码检查副本、构建结果和发证私钥不会被销毁。旧许可证不能重新开始计时。

**【普通WSL终端｜不要sudo】先检查部署是否还存在：**

```bash
if [ -x /opt/ydtrader/ydtrader ]; then
  echo "部署仍存在"
else
  echo "部署已销毁，需要重新安装和激活"
fi
```

如果输出“部署仍存在”，不要覆盖安装，也不要手工删除 `/opt/ydtrader` 或 `/var/lib/ydtrader`。先检查许可证和timer状态；如果已经到期但销毁没有完成，按照 `README_本机激活与完整运行.md` 排查，不要重复执行旧许可证的 `activate`。

如果输出“部署已销毁”，继续以下步骤。

**【WSL终端｜命令内含sudo】重新安装程序：**

```bash
cd ~/projects/yd_trader
sudo result/ydtrader-linux-x86_64/install/install_linux.sh
```

如果提示安装脚本不存在，先执行 `ls -l ~/projects/yd_trader/result`，不要从Windows目录直接运行Linux安装包。

**【WSL终端｜命令内含sudo】恢复本机真实柜台配置：**

```bash
sudo install -o root -g "$(id -gn)" -m 0640 \
  "/mnt/c/Users/Hello/Documents/基础环境配置/outputs/share/share/yd_trader/config/account.json" \
  /opt/ydtrader/config/account.json

sudo install -o root -g "$(id -gn)" -m 0640 \
  "/mnt/c/Users/Hello/Documents/基础环境配置/outputs/share/share/yd_trader/config/ydClient.ini" \
  /opt/ydtrader/config/ydClient.ini
```

**【普通WSL终端｜不要sudo】检查文件并签发全新的24小时许可证：**

```bash
ls -l /opt/ydtrader/ydtrader \
  /opt/ydtrader/config/account.json \
  /opt/ydtrader/config/ydClient.ini

cd ~/projects/yd_trader
MACHINE_CODE=$(/opt/ydtrader/ydtrader machine-code)
LICENSE_FILE="$HOME/.ydtrader-issuer/local-$(date -u +%Y%m%dT%H%M%SZ).license.json"

.venv/bin/python build_tools/issue_license.py \
  --private-key ~/.ydtrader-issuer/issuer.private.pem \
  --machine-code "$MACHINE_CODE" \
  --features order monitor marketdata \
  --ttl-hours 24 \
  --output "$LICENSE_FILE"

echo "新许可证：$LICENSE_FILE"
```

签发工具会依次要求输入“发证私钥密码、新业务运行密码、再次输入新业务运行密码”。这些都不是Linux的 `sudo` 密码。密码输入时屏幕不显示字符是正常现象；不要把密码写进命令行。之前在普通Shell中明文显示过的业务密码不得继续用于leader交付。

**【同一个WSL终端｜命令内含sudo】立即激活刚签发的许可证：**

```bash
sudo /opt/ydtrader/ydtrader activate --license "$LICENSE_FILE"
```

这里可能先要求Linux用户的 `sudo` 密码，然后程序显示 `运行密码:`；此时输入刚设置的新业务运行密码。看到“激活成功”和新的绝对UTC到期时间后检查timer：

```bash
systemctl status ydtrader-expiry.timer --no-pager
systemctl list-timers ydtrader-expiry.timer --all --no-pager
```

重新安装不会代替授时验收。完成激活后继续下面的Windows/Linux报告流程，最终必须重新得到 `RESULT: PASS`，才能接行情。

**【管理员 Windows PowerShell｜必须】安装与 leader 相同的 Meinberg NTP：**

先确认系统不是公司域成员或域控制器；域环境可能依赖 `W32Time`，不得直接禁用。普通个人工作站执行：

```powershell
winget install --id MeinbergGlobal.NTP --exact --version 4.2.8p18a2 `
  --source winget --location D:\NTP --interactive `
  --accept-package-agreements --accept-source-agreements
```

安装向导中使用 `D:\NTP`，让服务自动启动，并允许安装器禁用其他 Windows 授时服务。安装完成后核对：

```powershell
& "D:\NTP\bin\ntpd.exe" --version
Get-Service NTP,W32Time | Format-Table Name,Status,StartType
```

必须看到安装包版本 `4.2.8p18a2` 对应的内部版本 `ntpd 4.2.8p18a-o`；`NTP` 应为 `Running/Automatic`，`W32Time` 应为 `Stopped/Disabled`。不要同时启用两个校时服务。

**【管理员 Windows PowerShell｜必须】第一次配置Windows：**

```powershell
cd "C:\Users\Hello\Documents\基础环境配置\outputs\share\share\yd_trader"
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\windows_time_sync.ps1 `
  -Config .\config\time_authority.tencent-south-china-fallback.conf `
  -Apply `
  -Output .\windows-time.json `
  -NtpRoot D:\NTP
```

`-Apply` 会备份安装器原始配置为 `D:\NTP\etc\ntp.conf.ydtrader-original`，再写入唯一授时中心，使用 `iburst minpoll 6 maxpoll 6`（固定64秒轮询），停止并禁用 `W32Time`，重启 `NTP` 服务。脚本最长等待150秒，直到 `ntpq -pn` 出现属于配置中心的 `*` 选中源且 reach 至少包含3个成功样本。等待期间出现英文等待提示是正常现象。

Windows报告直接读取 ntpd 已滤波和驯服后的系统状态：`offset` 作为 `authority_minus_local_ms`，同时记录 `sys_jitter`、`rootdisp`、`frequency`、stratum、选中peer、poll、reach、delay和jitter。它比单次公网UDP探测稳定，但仍不能消除VPN路径或公网链路的固定上下行不对称。

从旧版 `W32Time` 升级后必须先安装上述精确版本，再在管理员PowerShell执行一次带 `-Apply` 的命令。报告会核对 ntpd 版本、服务状态、唯一选中源、reach样本、stratum和偏差；未真正应用成功时明确返回 `RESULT: FAIL`。域成员机器脚本会拒绝禁用 `W32Time`，必须交由域管理员设计。

**【WSL终端｜命令内含sudo】第一次配置Linux：**

```bash
cd ~/projects/yd_trader
sudo ./scripts/setup_linux_time_sync.sh --config ./config/time_authority.tencent-south-china-fallback.conf
```

该命令会让 chrony 只使用指定网络授时中心，移除内容完全匹配项目旧版本的 `ydtrader-windows-host.conf`，并在 WSL 中设置必要的 `SYNC_IN_CONTAINER=yes`。它会立即校时；不会配置 `PHC0`。

配置完成后，在60秒内依次生成两端只读报告。

**【普通 Windows PowerShell｜不要管理员】生成Windows报告：**

```powershell
cd "C:\Users\Hello\Documents\基础环境配置\outputs\share\share\yd_trader"
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\windows_time_sync.ps1 `
  -Config .\config\time_authority.tencent-south-china-fallback.conf `
  -Output .\windows-time.json `
  -NtpRoot D:\NTP
```

不带 `-Apply` 只通过本机 `ntpq` 读取状态，不修改时间或服务，因此普通PowerShell即可。正常结果应显示 `Selected : 106.55.184.199`、毫秒级 `Offset/Jitter` 和 `RESULT: PASS`。如 `ntpq -pn` 没有 `*`，先等待2分钟再查，不要用手工改JSON的方式通过验收。

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

### leader到期后的部署恢复与权限

| 操作 | 在哪里执行 | 权限 |
|---|---|---|
| 解压交付包、检查机器码 | leader Linux | 普通用户 |
| 安装程序、恢复配置、激活许可证 | leader Linux | 对应命令使用 `sudo` |
| 签发新许可证 | 持有私钥的安全发证机 | 普通用户，不在leader上放私钥 |
| 安装/配置Windows Meinberg ntpd | Windows PowerShell | 安装和 `-Apply` 必须管理员；只读报告普通权限 |
| 配置leader chrony | leader Linux | 命令使用 `sudo` |
| 生成leader Linux报告 | leader Linux | 普通用户，不使用 `sudo` |

如果 `/opt/ydtrader/ydtrader` 已被到期销毁，在leader普通Linux终端重新解压，然后用 `sudo` 安装：

```bash
tar -xzf ydtrader-linux-x86_64.tar.gz
cd ydtrader-linux-x86_64
sudo ./install/install_linux.sh

sudo install -o root -g "$(id -gn)" -m 0640 \
  ./account.json /opt/ydtrader/config/account.json
sudo install -o root -g "$(id -gn)" -m 0640 \
  ./ydClient.ini /opt/ydtrader/config/ydClient.ini

/opt/ydtrader/ydtrader machine-code
```

leader只把最后输出的64位机器码交给发证人员，私钥不得复制到leader。发证人员在安全构建机使用 `build_tools/issue_license.py` 签发新的许可证ID和新业务运行密码；旧许可证不得复用。把新许可证安全传回leader后执行：

```bash
sudo /opt/ydtrader/ydtrader activate --license ./leader.license.json
```

激活成功后再继续下面的独立授时和报告比较。

**【leader普通Linux终端；配置chrony的命令使用sudo】Linux首次配置：**

```bash
cd /path/to/ydtrader-linux-x86_64
cp config/time_authority.cffex.example.conf config/time_authority.cffex.conf
vi config/time_authority.cffex.conf
sudo ./tools/setup_linux_time_sync.sh --config ./config/time_authority.cffex.conf
```

**【管理员Windows PowerShell】** 先安装第二节指定的 Meinberg 精确版本，再运行 `windows_time_sync.ps1 -Apply`，只是把 `-Config` 换成同一份中金所配置。随后用普通PowerShell生成Windows JSON。确认两端 UDP 123 可达后，在60秒内分别生成。

**【leader普通Linux终端｜不要sudo】生成Linux报告：**

```bash
./tools/linux_time_report.sh --config ./config/time_authority.cffex.conf --output ./linux-time.json
```

**【普通Windows PowerShell】** 生成 `windows-time.json`。人工传递两个 JSON 文件，不使用 SSH 或共享凭据；再使用普通Windows PowerShell在Windows主项目目录运行 `compare_time_reports.ps1`。必须看到 `RESULT: PASS`。

## 四、运行 IC2609 行情延迟测试

只有授时比较通过后，才在已经安装、激活且配置好柜台账号的 Linux 环境执行。

**【普通WSL/Linux终端｜不要sudo】行情测试：**

```bash
cd /opt/ydtrader
./ydtrader marketdata --instrument IC2609 --duration-seconds 30 --max-quotes 100
```

按提示输入业务运行密码。结束时查看收到行情数、有效样本数、剔除首条有效行情后用于统计的样本数、平均延迟（绝对值）、中位延迟（绝对值）、最小延迟、最大延迟和“本机减行情”的平均时间差；这些统计量均不包含首条有效行情。本流程不执行真实报单或撤单。

汇总后还会输出与分析表相同风格的15分钟分箱表，列为 `time_bin / count / mean / std / p95 / max`，单位均为毫秒，仍使用剔除首条后的绝对时间差。30秒测试通常只有一行。若要覆盖一个完整交易时段并保存CSV，在普通WSL/Linux终端运行：

```bash
cd /opt/ydtrader
./ydtrader marketdata \
  --instrument IC2609 \
  --duration-seconds 21600 \
  --max-quotes 0 \
  --bin-minutes 15 \
  --latency-bins-csv ./logs/IC2609-latency-bins.csv
```

到收盘后可以按 `Ctrl+C` 提前结束；程序仍会打印汇总并写出截至当时的CSV。`std` 为样本标准差，`p95` 使用线性插值百分位。分箱依据本机收到回调的UTC+8时间，单个分箱只有一条数据时 `std=0`。

若任一授时报告或最终比较为 FAIL，立即停止：不得把行情统计解释为真实网络单向延迟。行情时间戳还可能包含交易所撮合/行情网关、柜台转发和时间戳粒度等因素，即使授时通过，汇总也不是纯网络单向延迟。

## 五、故障判定

- `ntpq -pn` 没有任何peer：`D:\NTP\etc\ntp.conf` 尚未写入服务器，用管理员PowerShell重新执行 `-Apply`。
- `ntpq -pn` 有peer但没有 `*`：尚未选中有效源；保持电脑和服务运行2分钟，检查 UDP 123、VPN路径与 `reach`。不得配置 `LOCAL(0)` 伪造通过。
- Windows报告提示 `W32Time` 未禁用或 `NTP` 未自动运行：两个校时服务状态不合格，用管理员PowerShell重新执行 `-Apply`，不要手工同时启动二者。
- Windows报告提示版本不符：安装包不是 `4.2.8p18a2`（内部版本应为 `4.2.8p18a-o`），先统一版本再验收。
- Linux 选中源为 `PHC0`、模式为 `#` 或没有选中源：仍在使用旧配置或网络 NTP不可达，重新执行 Linux 配置脚本并检查 UDP 123、DNS与 `chronyc sources -v`。
- 配置中心、官网或NTP列表不一致：复制同一份配置到两端，不要只凭服务器看起来相近就继续。
- 报告相隔超过60秒：不需要重新配置，只需在60秒内重新生成两份只读 JSON。
- 任一单端偏差或最终差异超过50 ms：先解决授时，不能通过修改报告或放宽阈值来认可本次行情结果。
