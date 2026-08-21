# YDTrader 本机激活与完整运行

本文用于在当前 Windows＋WSL 开发机完整走通：Linux 构建、安装、签发许可证、激活、输入运行密码以及安全连接柜台。Windows 主项目仍是唯一源码目录，WSL 只保存 Linux 检查副本和 Linux 构建环境。

> 本流程会在 WSL 内创建 `/opt/ydtrader`、`/var/lib/ydtrader` 和 root 级 systemd 到期 timer。24 小时到期后只会销毁 WSL 内的安装目录、运行配置、日志和授权状态，不会删除 Windows 主项目或 `~/projects/yd_trader` 源码检查副本。

## 一、为什么普通校验没有出现密码

以下操作按设计不要求密码：

- `pytest` 会在测试中模拟密码输入，不进行真实交互。
- `ydtrader --help` 和各业务命令的 `--help` 允许未激活查看。
- `ydtrader machine-code` 必须允许未激活获取申请码。

只想确认密码输入入口是否存在，可以在 PowerShell 进入 WSL：

```powershell
wsl -d Ubuntu-24.04
```

然后执行：

```bash
cd ~/projects/yd_trader
.venv/bin/python scripts/ydtrader.py order --wait-seconds 10
```

此时会出现：

```text
运行密码:
```

输入时屏幕不显示字符是正常现象。尚未激活时，输入任意内容后会提示授权失败并返回退出码 20：

```bash
echo $?
```

这只验证密码入口，不属于完整运行。

## 二、开始前检查

确认 WSL 检查副本已经同步 Windows 主项目：

```bash
cd ~/projects/yd_trader
git pull --ff-only
git status --short --branch
```

确认输出没有 `M`、`D` 或 `??` 文件，再运行基础测试：

```bash
sh -n build_tools/build_manylinux2014.sh \
  install/install_linux.sh \
  install/ydtrader-destroy
.venv/bin/python -m pytest -q
```

必须看到全部测试通过。当前版本预期为：

```text
10 passed
```

检查 WSL 中是否已经安装过：

```bash
ls -ld /opt/ydtrader /var/lib/ydtrader 2>/dev/null
```

如果这两个目录已经存在，说明本机可能安装或激活过。不要直接覆盖、不要手工删除授权状态；优先使用现有安装，或者等待其到期销毁后重新部署。

## 三、首次生成本机测试密钥

以下密钥只用于当前开发机测试。正式 leader 交付应使用单独保管的正式密钥。

创建项目外的密钥目录：

```bash
mkdir -p ~/.ydtrader-issuer
chmod 700 ~/.ydtrader-issuer
```

仅在密钥文件不存在时执行一次：

```bash
cd ~/projects/yd_trader
.venv/bin/python build_tools/generate_keypair.py \
  --private-key ~/.ydtrader-issuer/issuer.private.pem \
  --public-key ~/.ydtrader-issuer/issuer.public.pem
```

按照提示输入并确认“私钥密码”。以后看到文件已存在时不要删除重建，直接继续使用原密钥。

私钥禁止放入项目、Git、交付压缩包或 leader 机器。可以检查：

```bash
ls -l ~/.ydtrader-issuer
git status --short
```

## 四、构建当前 WSL 调试包

执行：

```bash
cd ~/projects/yd_trader
.venv/bin/python build_tools/build_release.py \
  --private-key ~/.ydtrader-issuer/issuer.private.pem \
  --public-key ~/.ydtrader-issuer/issuer.public.pem
```

按照提示输入私钥密码。成功后产生：

```text
result/ydtrader-linux-x86_64.tar.gz
result/ydtrader-linux-x86_64/
```

这是 Ubuntu 24.04 WSL 本机调试包，可能要求 GLIBC 2.38，只能用于当前本机完整激活测试。正式 leader 包必须使用：

```bash
build_tools/build_manylinux2014.sh \
  ~/.ydtrader-issuer/issuer.public.pem \
  ~/.ydtrader-issuer/issuer.private.pem
```

## 五、安装到当前 WSL

首次安装执行：

```bash
cd ~/projects/yd_trader
sudo result/ydtrader-linux-x86_64/install/install_linux.sh
```

安装器固定安装到 `/opt/ydtrader`，不会覆盖已有安装。

把 Windows 主项目中本机真实配置安装进去：

```bash
sudo install -o root -g "$(id -gn)" -m 0640 \
  "/mnt/c/Users/Hello/Documents/基础环境配置/outputs/share/share/yd_trader/config/account.json" \
  /opt/ydtrader/config/account.json

sudo install -o root -g "$(id -gn)" -m 0640 \
  "/mnt/c/Users/Hello/Documents/基础环境配置/outputs/share/share/yd_trader/config/ydClient.ini" \
  /opt/ydtrader/config/ydClient.ini
```

检查安装结果，不显示账号密码内容：

```bash
ls -l /opt/ydtrader/ydtrader \
  /opt/ydtrader/config/account.json \
  /opt/ydtrader/config/ydClient.ini
/opt/ydtrader/ydtrader --help
```

## 六、取得机器码并签发24小时许可证

取得当前 WSL 机器申请码：

```bash
/opt/ydtrader/ydtrader machine-code
```

复制输出的64位字符，然后签发许可证，必须把 `<粘贴64位机器码>` 替换掉：

```bash
cd ~/projects/yd_trader
.venv/bin/python build_tools/issue_license.py \
  --private-key ~/.ydtrader-issuer/issuer.private.pem \
  --machine-code '<粘贴64位机器码>' \
  --features order monitor marketdata \
  --ttl-hours 24 \
  --output ~/.ydtrader-issuer/local.license.json
```

程序会依次要求：

1. 输入发证私钥密码。
2. 设置业务运行密码。
3. 再次输入业务运行密码。

运行密码以后每次启动 `order`、`monitor` 或 `marketdata` 都要输入。不要把运行密码写进命令行、环境变量或文本文件。

如果 `local.license.json` 已存在，工具会拒绝覆盖。下一次完整部署应签发新文件，例如：

```text
local-02.license.json
```

不要重复使用已经激活过的旧许可证。

## 七、激活并创建24小时 timer

执行：

```bash
sudo /opt/ydtrader/ydtrader activate \
  --license ~/.ydtrader-issuer/local.license.json
```

可能先出现 sudo 的 Linux 用户密码，随后出现程序自己的：

```text
运行密码:
```

这里输入第六节设置的业务运行密码。成功时会显示许可证 ID 和绝对 UTC 到期时间。

检查 timer：

```bash
systemctl status ydtrader-expiry.timer --no-pager
systemctl list-timers ydtrader-expiry.timer --all --no-pager
```

同一安装不能重复执行 `activate` 来重新开始计时。

## 八、行情测试前检查 Windows 和 Linux 时间

Windows 使用 Windows Time Service（`W32Time`）对外校时，WSL chrony 通过 Hyper-V PTP 设备 `/dev/ptp_hyperv` 直接跟随 Windows 宿主时钟。时间链路必须是：

```text
经批准的外部 NTP → Windows W32Time → Hyper-V PHC0 → WSL chrony
```

只让 Windows 和 Linux 各自跟随不同公网 NTP 不能保证它们彼此对齐。首次在 WSL 安装 chrony 后，运行一次：

```bash
cd ~/projects/yd_trader
sudo scripts/setup_wsl_chrony_windows_sync.sh
```

该脚本会禁用 `systemd-timesyncd`，允许 chrony 在 WSL 中控制系统时钟，并将 Windows 提供的 `PHC0` 设为首选且可信的参考源。成功后 `chronyd` 进程不得带 `-x`，`chronyc sources -v` 应显示 `#* PHC0`。

然后在 Windows PowerShell 中运行项目内检测脚本：

```powershell
cd "C:\Users\Hello\Documents\基础环境配置\outputs\share\share\yd_trader"
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\windows_time_sync.ps1
```

默认只检查，不修改系统。它会输出 Windows 当前实际时间源和配置源、Windows 相对该 NTP 源的3次实测偏差、chrony 当前参考源与偏差，检查 chronyd 是否真的能调整时钟以及是否选中 `PHC0`，并进行 7 次 Windows↔WSL 直接比较。默认 Windows↔NTP、chrony 和 Windows↔WSL 三类最大允许偏差分别为 50 ms、20 ms 和 50 ms；异常时输出 `RESULT: FAIL` 并返回退出码 2。

要按 Windows 当前配置的时间源立即重新同步，以管理员身份打开 PowerShell：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\windows_time_sync.ps1 -ResyncWindows
```

只有经负责人或 IT 确认时才修改 Windows NTP 服务器；脚本不提供默认第三方服务器：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\windows_time_sync.ps1 `
  -ResyncWindows `
  -WindowsPeers "<经批准的NTP服务器1>,0x8 <经批准的NTP服务器2>,0x8"
```

Windows 手工 NTP 服务器后缀建议使用 `0x8`（客户端模式）。`0x9` 还包含特殊固定轮询标志；当 `SpecialPollInterval` 很大时，不适合临时行情延迟测试的快速校时。

行情延迟测试前必须看到 `RESULT: PASS`。如果只有 chrony 显示纳秒级偏差，但 Windows↔WSL 直接时差超标，仍不得把行情统计当作网络延迟。

## 九、完整运行并输入密码

先运行最安全的连接测试：只登录柜台、接收数据，不报单，10秒后退出。

```bash
cd /opt/ydtrader
./ydtrader order --wait-seconds 10
```

此时必须看到：

```text
运行密码:
```

密码验证成功后才会加载 `ydcore` 业务模块、`pyyd` 并连接柜台。

独立监控60秒：

```bash
cd /opt/ydtrader
./ydtrader monitor --wait-seconds 60
```

行情测试，必须把 `IC2609` 替换成当时真实存在的合约：

```bash
cd /opt/ydtrader
./ydtrader marketdata \
  --instrument IC2609 \
  --duration-seconds 10 \
  --max-quotes 10
```

运行结束后会看到 `=== 行情延迟测试汇总 ===`，其中 `平均延迟（绝对值）` 是 leader 最直接查看的指标；同时还会显示样本数、最小/最大延迟和平均有符号时间差。该值包含本机与行情源的时钟偏差，测试前应确保系统时钟已同步，不能把它直接当成纯网络单向延迟。

只做 `checked=2` 校验，不发送到交易所；所有业务参数必须由测试人员按当时行情填写：

```bash
cd /opt/ydtrader
./ydtrader order --check-only \
  --instrument IC2609 \
  --action 0 \
  --open-close 0 \
  --volume 1 \
  --price 5000 \
  --order-type 0 \
  --hedge 1 \
  --wait-seconds 5
```

> 不要照抄示例合约和价格。没有负责人明确批准，严禁使用 `--send`、`--batch-cancel`、`--set-trading-right`、`--pause-two-layer` 或 `--resume-two-layer`。

## 十、失败时查看退出码

运行后立即执行：

```bash
echo $?
```

授权退出码：

| 退出码 | 含义 |
|---:|---|
| 20 | 尚未激活或授权状态缺失 |
| 21 | 密码、签名、密文或状态校验失败 |
| 22 | 机器、功能或权限不允许 |
| 23 | 已到期或检测到系统时间回拨 |

查看日志：

```bash
tail -n 100 /opt/ydtrader/logs/runtime.log
tail -n 100 /opt/ydtrader/logs/error.log
tail -n 100 /opt/ydtrader/logs/monitor.log
```

## 十一、本机与 leader 的区别

- 当前 WSL：可直接使用 `build_release.py` 生成调试包，目的是完整验证密码和授权链路。
- leader：必须交付 `build_manylinux2014.sh` 构建的 GLIBC 2.17 兼容包。
- 两边安装后的运行命令完全相同，都是 `/opt/ydtrader/ydtrader ...`。
- leader 不应持有发证私钥；机器码发回安全构建机签发许可证。
- 到期后应用立即拒绝业务运行，timer 会删除 `/opt/ydtrader`、运行配置、日志和 `/var/lib/ydtrader`。需要再次使用时必须重新安装并签发新许可证。
