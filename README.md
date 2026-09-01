# YDTrader Linux 受保护交付

本仓库只构建 Linux x86_64 版本。`order`、`monitor`、`marketdata` 的实现位于 `ydcore`，发布时编译成 CPython 3.9 Cython 扩展；交付包只有一个 Nuitka standalone 入口 `ydtrader`，不包含自有 `.py/.pyc/.c/.pdb`。官方 `pyyd` wheel 不修改，由构建环境收集其中的 `pyyd...so` 和 `yd.so`。

## 安全边界

许可证由 Ed25519 私钥签名，运行密码使用 Argon2id 派生 AES-256-GCM 密钥。许可证绑定 `/etc/machine-id`、x86_64、许可功能和 TTL。首次激活写入绝对到期时间，默认 24 小时且不能重新激活续时；每次业务启动都先隐藏输入密码并校验授权，成功前不会导入 `ydcore` 业务模块、`pyyd` 或连接柜台。

这能提高源码逆向与普通复制的成本，但不能抵抗能修改程序、系统时间或磁盘快照的 root 攻击者。监控命令同样每次要求人工输入密码，因此不支持无人值守自动重启。

## 项目结构

```text
environment.yml               唯一开发与运行依赖清单
data/                         输入和参考数据
result/                       构建、运行与验收产物
scripts/                      可重复执行的构建和运行入口
tests/                        自动化测试
utils/                        可复用的 WSL/Conda 运行时助手
aeron_mvp/                    Java、SBE、Aeron 与行情桥接源码
dashboard/                    高并发行情只读监控端
ydcore/                       报单、监控、行情和授权业务源码
config/                       配置模板与本机运行配置
docs/                         架构和迁移文档
build_tools/                  私有发证和 manylinux 构建模块
install/                      固定路径安装与到期销毁程序
vendor/wheels/                官方离线 pyyd wheel
```

这是存量多语言项目，`aeron_mvp`、`dashboard` 和 `ydcore` 保留为成熟应用模块；
`build_tools`、`install` 和官方离线 wheel 是交付边界，不为追求目录外观而搬迁。
所有生成依赖、SDK、Archive、日志和验收证据只写入 `result/`，不提交 Git。

旧 Windows 使用手册仅作为迁移参考保存在 `docs/legacy-windows-manual.md`，不属于当前交付方式。

Windows/Linux 独立使用同一授时中心、生成 JSON 报告、比较时差并运行 IC2609 行情测试的唯一有效流程，见 `README_授时与行情延迟操作手册.md`。授时比较未显示 `RESULT: PASS` 时，不得解释行情延迟汇总。

## 构建环境

正式发布要求 Linux x86_64、CPython 3.9 和 glibc 2.17 兼容构建环境。开发和测试只在
WSL2 Linux 文件系统中的项目专用 Miniconda 环境运行：

```bash
cd ~/projects/high-concurrency
conda env update -f environment.yml --prune
conda activate ydtrader-high-concurrency
```

`environment.yml` 是本机开发和运行依赖的唯一清单。`requirements-build.txt` 仅供
manylinux2014 Docker 中的 CPython 3.9 正式构建使用，不用于创建本机虚拟环境。

生成发证密钥。私钥应移出仓库并进入受控离线存储；不要提交任何私钥或已签发许可证：

```bash
python build_tools/generate_keypair.py \
  --private-key /secure/issuer.private.pem \
  --public-key /secure/issuer.public.pem
```

运行测试。直接调用 `build_release.py` 只生成当前 Linux 发行版的调试验收包：

```bash
python -m pytest -q
python build_tools/build_release.py \
  --private-key /secure/issuer.private.pem \
  --public-key /secure/issuer.public.pem
```

正式发布必须从装有 Docker 的 Linux/WSL 主机运行 manylinux2014 入口：

```bash
build_tools/build_manylinux2014.sh \
  /secure/issuer.public.pem \
  /secure/issuer.private.pem
```

产物为 `result/ydtrader-linux-x86_64.tar.gz`。构建程序会拒绝缺少 `pyyd.so/yd.so`、包含自有 Python 源码或调试文件的交付目录，并生成签名 SHA-256 清单；可变的 `config` 和 `logs` 不进入不可变文件哈希。manylinux2014 将兼容基线固定为 glibc ≥2.17；Ubuntu 24.04/WSL 直接构建的产物可能要求更高版本 glibc，只能用于功能调试，不能交付给兼容性未知的 leader 机器。

## 发证、安装和激活

目标机先解压、以 root 安装到固定路径。安装器不接受目标路径参数，也不会覆盖已有部署：

```bash
tar -xzf ydtrader-linux-x86_64.tar.gz
sudo ./ydtrader-linux-x86_64/install/install_linux.sh
sudo install -o root -g "$(id -gn)" -m 0640 account.json /opt/ydtrader/config/account.json
sudo install -o root -g "$(id -gn)" -m 0640 ydClient.ini /opt/ydtrader/config/ydClient.ini
```

在目标机取得申请码，在安全发证机签发默认 24 小时许可证：

```bash
/opt/ydtrader/ydtrader machine-code

python build_tools/issue_license.py \
  --private-key /secure/issuer.private.pem \
  --machine-code '<申请码>' \
  --features order monitor marketdata \
  --output leader.license.json
```

将许可证交给目标机后仅激活一次：

```bash
sudo /opt/ydtrader/ydtrader activate --license ./leader.license.json
```

激活会记录 UTC 激活与绝对到期时间，并创建 root 级 `ydtrader-expiry.timer`。关机错过到期点时，systemd 的 `Persistent=true` 会在下次启动补执行；即使 timer 有延迟，业务入口也会立即拒绝已到期授权。

## 统一命令

```bash
/opt/ydtrader/ydtrader --help
/opt/ydtrader/ydtrader order --help
/opt/ydtrader/ydtrader monitor --help
/opt/ydtrader/ydtrader marketdata --help
```

业务参数沿用原脚本，只需把原来的 `python scripts/order.py ...` 改成 `/opt/ydtrader/ydtrader order ...`；其余两个功能同理。业务命令每次最多允许三次密码输入，密码不能通过命令行、环境变量或明文文件传入。多个功能可以各自启动一个 `ydtrader` 进程并行运行。

授权退出码为：20 未激活或缺状态，21 密码、签名、密文或状态校验失败，22 机器、功能或权限不允许，23 到期或检测到时间回拨。业务成功授权后仍保留原功能的参数、确认流程和退出码。

真实报单测试必须逐级执行 `CONNECT_ONLY`、查询、行情与 `checked=2` 验证；`checked=0` 报单和撤单只有在负责人明确批准后才能执行。

## 到期销毁

销毁程序固定安装在 `/usr/local/libexec/ydtrader-destroy`，不接受路径或 TTL 参数。它只处理 `/opt/ydtrader` 和 `/var/lib/ydtrader`，并在删除前验证 realpath、root 所有权、安装/状态双标记、Ed25519 签名清单和全部不可变文件哈希。随后停止实际可执行路径位于部署目录的进程，原子改名部署目录，再删除部署、配置、日志、许可证、状态、systemd 单元和 helper。

任一安全检查失败都会拒绝删除，绝不扩大范围；授权到期仍会使业务失效。应用不主动创建外部销毁审计文件，但 systemd 和操作系统可能保留自身元数据。销毁集成测试只能在一次性容器或专用 WSL 测试发行版中运行，禁止在开发机真实 `/opt` 上试验。
