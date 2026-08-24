# YDTrader 傻瓜式人工重跑执行流程

这份文档只讲照着做的顺序。Windows 目录

```text
C:\Users\Hello\Documents\基础环境配置\outputs\share\share\yd_trader
```

是唯一源码目录；WSL 只用于 Linux 编译和测试。不要在 WSL 副本里长期改代码，也不要把 Windows `.venv` 拿到 Linux 使用。

## 一、只想在 leader Linux 机器重新运行

适用场景：程序被 `Ctrl+C` 停止、终端断开或某个功能进程退出，但许可证还没有到期，且 `/opt/ydtrader/ydtrader` 仍存在。

先检查程序和授权入口：

```bash
ls -l /opt/ydtrader/ydtrader
/opt/ydtrader/ydtrader --help
```

然后按需要选择一个命令。每次启动都会出现 `运行密码:`，输入时屏幕不会显示字符，这是正常现象，输完按回车。

只登录柜台、接收数据，不报单，10 秒后退出：

```bash
cd /opt/ydtrader
./ydtrader order --wait-seconds 10
```

启动独立监控，持续运行到手工按 `Ctrl+C`：

```bash
cd /opt/ydtrader
./ydtrader monitor
```

监控 60 秒后自动退出：

```bash
cd /opt/ydtrader
./ydtrader monitor --wait-seconds 60
```

订阅一个真实合约的行情，下面的 `IC2609` 必须替换成当时真实存在的合约：

```bash
cd /opt/ydtrader
./ydtrader marketdata --instrument IC2609 --duration-seconds 10 --max-quotes 10
```

结束时终端会直接输出中文汇总，包括收到行情条数、有效时间戳样本数、剔除首条有效行情后用于统计的样本数、平均延迟（绝对值）、中位延迟（绝对值）、最小/最大延迟及“本机减行情”的平均时间差。平均值、中位数、最小值、最大值和平均时间差均按剔除首条后的样本计算。延迟结果包含本机与行情源的时钟偏差，不等同于纯网络单向延迟；测试前应确认两端时钟已同步。

行情测试前，Windows 与 Linux 必须分别直接连接同一个授时中心，并在60秒内生成 JSON 报告进行比较。完整有效流程见项目根目录 `README_授时与行情延迟操作手册.md`；最终没有看到 `RESULT: PASS` 时不得继续解释行情延迟。本项目不再使用 Windows `PHC0` 作为 Linux 时间源。

仅做 `checked=2` 委托校验，不把订单报到交易所。所有参数都要由测试人员按本次测试填写：

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

不要照抄示例价格和合约。先执行 `./ydtrader order --help` 确认参数含义。

> 严禁自行执行 `--send`、`--batch-cancel`、`--set-trading-right`、`--pause-two-layer` 或 `--resume-two-layer`。这些命令会产生真实报单、撤单或账户权限变化，必须由负责人明确批准并现场确认账号后才能运行。

同时运行监控、行情和交易连接时，开三个终端，每个终端分别运行一个命令。不要在一个终端里用 `&` 绕过密码输入。

## 二、人工重跑失败时怎么判断

先看退出码：

```bash
cd /opt/ydtrader
./ydtrader order --wait-seconds 10
echo $?
```

常见结果：

| 退出码 | 含义 | 处理方法 |
|---:|---|---|
| 0 | 正常完成 | 无需处理 |
| 1–19 | 原业务参数、配置、网络或柜台错误 | 查看终端及 `/opt/ydtrader/logs` |
| 20 | 未激活或授权状态缺失 | 检查是否完成激活；不要自行伪造状态文件 |
| 21 | 密码错误、许可证损坏或状态被篡改 | 核对密码；连续三次失败后重新执行命令 |
| 22 | 机器、功能或权限不允许 | 核对机器码和许可证功能，联系发证人员 |
| 23 | 已到期或检测到时间回拨 | 停止重试，按第五节重新部署和发证 |
| 130 | 手工按了 `Ctrl+C` | 需要时重新执行即可 |

快速看最近日志：

```bash
tail -n 100 /opt/ydtrader/logs/runtime.log
tail -n 100 /opt/ydtrader/logs/error.log
tail -n 100 /opt/ydtrader/logs/monitor.log
```

不要删除或修改 `/var/lib/ydtrader` 中的授权文件，不要改系统时间，不要手工修改 systemd timer。这样做只会触发授权失败。

## 三、开发机改完代码后重新测试

所有代码先在 Windows 主项目目录修改并提交。然后打开 PowerShell，把 Windows 仓库同步到 WSL 检查副本：

```powershell
wsl -d Ubuntu-24.04
```

进入 WSL 后执行：

```bash
cd ~/projects/yd_trader
git pull --ff-only
```

如果这台机器第一次测试，安装 Linux 原生 Python 3.9 环境：

```bash
cd ~/projects/yd_trader
uv venv --python 3.9 .venv
uv pip install --python .venv/bin/python -r requirements-build.txt
uv pip install --python .venv/bin/python \
  vendor/wheels/pyyd-1.486.96.99-cp39-cp39-linux_x86_64.whl
```

每次代码修改后必须执行：

```bash
cd ~/projects/yd_trader
sh -n build_tools/build_manylinux2014.sh \
  install/install_linux.sh \
  install/ydtrader-destroy
.venv/bin/python -m pytest -q
```

预期看到全部测试通过。任何一项失败都不要继续打包。

## 四、重新构建 Linux 交付包

发证私钥只能保存在构建/发证人员控制的安全目录，不能放进项目、Git、压缩包或 leader 机器。首次建立正式密钥：

```bash
mkdir -p ~/.ydtrader-issuer
chmod 700 ~/.ydtrader-issuer
cd ~/projects/yd_trader
.venv/bin/python build_tools/generate_keypair.py \
  --private-key ~/.ydtrader-issuer/issuer.private.pem \
  --public-key ~/.ydtrader-issuer/issuer.public.pem
```

密钥只生成一次。以后提示文件已存在时不要删除重建，否则旧许可证和旧程序会失去匹配关系。

WSL 本机调试包可以这样构建：

```bash
cd ~/projects/yd_trader
.venv/bin/python build_tools/build_release.py \
  --private-key ~/.ydtrader-issuer/issuer.private.pem \
  --public-key ~/.ydtrader-issuer/issuer.public.pem
```

这个包只用于当前 WSL 冒烟测试。Ubuntu 24.04 直接构建可能要求 GLIBC 2.38，不能当成兼容 GLIBC 2.17 的正式包。

正式交付必须在装有 Docker 的 Linux/WSL 主机运行：

```bash
cd ~/projects/yd_trader
build_tools/build_manylinux2014.sh \
  ~/.ydtrader-issuer/issuer.public.pem \
  ~/.ydtrader-issuer/issuer.private.pem
```

构建成功后得到：

```text
result/ydtrader-linux-x86_64.tar.gz
```

将它复制给 leader 前记录哈希：

```bash
sha256sum result/ydtrader-linux-x86_64.tar.gz
```

## 五、leader 首次安装或到期后重新部署

到期销毁后 `/opt/ydtrader` 会消失，旧进程不能人工重跑。必须重新安装交付包，并由发证人员签发新的许可证 ID；不要重复使用旧许可证。

在 leader 机器解压并安装：

```bash
tar -xzf ydtrader-linux-x86_64.tar.gz
cd ydtrader-linux-x86_64
sudo ./install/install_linux.sh
```

准备本机真实配置，文件名必须正确：

```bash
sudo install -o root -g "$(id -gn)" -m 0640 \
  ./account.json /opt/ydtrader/config/account.json
sudo install -o root -g "$(id -gn)" -m 0640 \
  ./ydClient.ini /opt/ydtrader/config/ydClient.ini
```

取得机器申请码：

```bash
/opt/ydtrader/ydtrader machine-code
```

把这串 64 位申请码发给发证人员。发证人员在安全构建机执行，`<机器申请码>` 必须替换：

```bash
cd ~/projects/yd_trader
.venv/bin/python build_tools/issue_license.py \
  --private-key ~/.ydtrader-issuer/issuer.private.pem \
  --machine-code '<机器申请码>' \
  --features order monitor marketdata \
  --ttl-hours 24 \
  --output leader.license.json
```

把 `leader.license.json` 安全传到 leader，然后只激活一次：

```bash
sudo /opt/ydtrader/ydtrader activate --license ./leader.license.json
```

看到 `激活成功` 和绝对 UTC 到期时间后，回到第一节运行需要的功能。不要重复执行 `activate`，它不会重新开始 24 小时计时。

## 六、每次操作前的五项检查

1. 确认正在使用 `/opt/ydtrader/ydtrader`，不是源码脚本。
2. 确认 `config/account.json` 和 `config/ydClient.ini` 属于本次 leader 机器。
3. 先运行 `--help`，再运行连接、监控、行情或 `--check-only`。
4. 没有负责人书面或现场批准，绝不使用真实报单、撤单和权限控制参数。
5. 到期退出码 23 或部署目录已删除时停止重试，重新走“构建、安装、申请码、签发新许可证、激活”流程。
