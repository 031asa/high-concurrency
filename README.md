# YDTrader Linux 高并发行情与交易工具

本仓库面向 Linux x86_64，包含 YDApi/CTP 行情接入、Aeron 高并发传输与归档、实时统计、Dashboard，以及 `order`、`monitor`、`marketdata` 三个业务入口。

项目不再包含自建许可证、运行密码、机器绑定、激活、到期销毁或加密状态。业务命令可直接启动。`config/account.json` 中的账号和密码仍然必须保留：它们是连接 YDApi 柜台所需的登录凭据，并不是项目加密机制。

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
ydcore/                       报单、监控和行情业务源码
config/                       配置模板与本机运行配置
docs/                         架构和迁移文档
build_tools/                  Cython、Nuitka 与 manylinux 构建模块
install/                      固定路径安装程序
vendor/wheels/                官方离线 pyyd wheel
```

高并发架构和数据流见 `docs/high-concurrency-architecture.md`。旧 Windows 手册只作为迁移参考保存在 `docs/legacy-windows-manual.md`。

## 环境与测试

开发和测试在 WSL2 Linux 文件系统中的项目专用 Miniconda 环境运行：

```bash
cd ~/projects/high-concurrency
conda env update -f environment.yml --prune
conda activate ydtrader-high-concurrency
python -m pytest -q
```

`environment.yml` 是本机依赖的唯一清单；`requirements-build.txt` 只供 manylinux2014 Docker 构建使用。

## 高并发行情

```bash
bash scripts/run_aeron_mvp.sh --count 100000 --sync-level 0
bash scripts/run_multi_source_aeron_mvp.sh \
  --source-config \
    config/market-sources/synthetic-sim-a.json \
    config/market-sources/synthetic-sim-b.json \
  --count 1000
bash scripts/run_dashboard.sh
```

统一入口接受一个或多个独立 JSON 配置，可连接任意数量的 `synthetic`、`ctp`、`ydapi` 实例。
样例和字段说明见 `config/market-sources/README.md`。旧的 `--sources`
共享参数模式仅为兼容既有命令而保留。YDApi/CTP 数据源的准确参数和验收方式见
`aeron_mvp/README.md`，授时与行情延迟操作见 `README_授时与行情延迟操作手册.md`。

### Python 统一入口（源码/扩展模块共用）

在上述 Conda 环境中，推荐通过同一个参数入口启动：

```bash
python scripts/ydtrader.py --help
python scripts/ydtrader.py aeron --help
python scripts/ydtrader.py multi-source \
  --source-config \
    config/market-sources/synthetic-sim-a.json \
    config/market-sources/synthetic-sim-b.json \
  --count 1000
# 另一个终端启动 Dashboard；不会自动连接柜台。
python scripts/ydtrader.py dashboard --host 127.0.0.1 --port 8080
```

- `aeron` / `multi-source` 以 `exec` 交给原 Shell 调度，保留原参数、退出码及信号清理逻辑；
  Dashboard 独立运行，原 `order`、`monitor`、`marketdata` 命令不变。
- Shell 内的 Python 子进程统一调用此入口的 `source-config`、`multi-source-mux`、
  `synthetic-bridge`、`ctp-bridge`、`ydapi-bridge`、`zmq-probe` 命令，
  不再通过文件路径执行业务 `.py`。业务模块提供 `main(argv)`，支持编译为 `.so` 后导入调用。
- 顶层帮助不会导入业务模块。CTP 命令先解析参数，再加载原生 SDK，
  因此 `python scripts/ydtrader.py ctp-bridge --help` 不需要准备 locale 或连接柜台。
- CTP 正式/TTS 仍使用原配置选择的独立解释器；本次没有合并环境、修改原生库或迁移可写目录。

扩展模块验收（只使用本机模拟 UDP 行情与 HTTP，不登录柜台）：

```bash
python -m pytest -q
RUN_COMPILED_ENTRYPOINT_TEST=1 python -m pytest -q tests/test_unified_entrypoint.py
```

第二条命令在临时目录编译模块、删除其对应 `.py` 后对照输出并运行模拟行情与 Dashboard。
本次仅完成入口和调用链适配；`product.toml` 的跨包保护范围、Java 构建期产物和
CTP 运行环境仍需后续适配，不能据此认定当前整仓已通过 Web 加密镜像构建。

### 双 CTP 常驻服务

长期运行入口同时连接 `CTP-TTS-7X24` 与 `CTP-LIVE-5LEVEL`。两个 Bridge 独立接入，
Dashboard 分来源展示行情、五档盘口与延迟；Aeron、Archive、Compute/Audit 仍按同一运行批次统计。

```bash
bash scripts/ydtrader_stack.sh install-start
bash scripts/ydtrader_stack.sh status
bash scripts/ydtrader_stack.sh logs
```

该命令把 user-systemd 单元安装到 `~/.config/systemd/user/`，并启用：

- `ydtrader-market.service`：双 CTP 行情、Aeron、Archive 与计算审计链路；异常退出自动重启。
- `ydtrader-dashboard.service`：`http://127.0.0.1:8080/`；异常退出自动重启。
- `ydtrader-stack.target`：统一启动和停止上述两个服务。

日常操作：

```bash
bash scripts/ydtrader_stack.sh start
bash scripts/ydtrader_stack.sh restart
bash scripts/ydtrader_stack.sh stop
```

Windows 桌面入口来自 `deploy/windows/start-ydtrader-dashboard.cmd`。它只调用统一管理脚本，
不会复制业务逻辑，并启动隐藏的 WSL 保活客户端（同一项目重复点击只保留一份）。仅启用 systemd
不能防止 WSL 空闲退出；`ydtrader_stack.sh stop` 后保活客户端会自动退出。
Windows 关机、休眠或显式执行 `wsl --shutdown` 仍会中断接收；恢复后点击桌面入口重新启动。
YDApi 启动追赶阶段的缓存回调不会进入 Aeron；仅在 caughtup 后启用配置合约，
并只转发该合约的正式订阅回调，避免 Dashboard 把 17:00 缓存快照当成实时行情。
TTS 与官方 CTP 均使用主循环中间隔 500ms 的逐合约订阅，并保留原生请求缓冲区。
2026-09-03 夜盘实测：指定实盘前置的批量订阅虽确认成功却没有行情，改用上述方式后收到当前夜盘行情；
实盘 11 合约配置不变，不用历史回放替代实时源。夜盘交易日可能为次日，应结合 ActionDay 和 UpdateTime 验收。
订阅确认不等于行情到达，应检查 `FORWARDING`、Compute 接收数和市场时间是否持续变化。
运行脚本同时检查 Publisher、Aeron Server、Compute、Audit 和可选 ZMQ Egress；
子进程异常退出时整个运行标为失败并交由 systemd 重启，避免 Bridge 仍有回调但曲线停住。
Bridge/Mux/Publisher 的 UDP 序号缺口仍严格报错，不跳过缺口伪装完整；
自动重启只能恢复后续接收，不能补回中断期间的行情，也不代表 UDP 丢包原因已消除。
仅当源明确为 live，且 ActionDay 等于未来交易日而 UpdateTime 距接收时刻不超过一分钟时，
延迟使用最近的实际日历日期；原始 ActionDay/TradingDay 保留，historical_replay 不改写日期。
两份非敏感行情源配置位于 `config/market-sources/ctp-tts-7x24.json` 和
`config/market-sources/ctp-live-5level.json`；每个来源的合约、前置与超时均可独立调整。
当前回放源仅配置已验证回调的 IF2609、IC2609、IH2609、IM2609、au2612、ag2612；
实盘源保留原 11 个合约，不将实盘合约列表直接套给回放源。2026-09-03 验证中，加入 cu2610
及后续未验证合约会出现该连接停止更新；新增回放合约前须单独确认回调和持续增长。

## 业务命令

源码模式：

```bash
python scripts/ydtrader.py --help
python scripts/ydtrader.py order --help
python scripts/ydtrader.py monitor --help
python scripts/ydtrader.py marketdata --help
```

命令会直接进入相应业务模块，不再要求额外运行密码。真实报单仍必须遵守原有风控：先执行连接、查询、行情与 `checked=2` 验证；`--send`、策略身份、暂停标志、报单和撤单阈值仍然有效，未经负责人明确批准不得执行真实报单或撤单。

## 发布与安装

当前 Linux 环境的功能调试包：

```bash
python build_tools/build_release.py
```

正式兼容包从带 Docker 的 Linux/WSL 主机运行：

```bash
build_tools/build_manylinux2014.sh
```

产物为 `result/ydtrader-linux-x86_64.tar.gz`。发布包保留 SHA-256 文件完整性清单，但不再生成或校验签名，也不需要任何密钥。

目标机安装：

```bash
tar -xzf ydtrader-linux-x86_64.tar.gz
sudo ./ydtrader-linux-x86_64/install/install_linux.sh
sudo install -o root -g "$(id -gn)" -m 0640 account.json /opt/ydtrader/config/account.json
sudo install -o root -g "$(id -gn)" -m 0640 ydClient.ini /opt/ydtrader/config/ydClient.ini
/opt/ydtrader/ydtrader --help
```

安装完成后可直接运行，不存在申请码、发证、激活、计时器或自动销毁步骤。详细人工流程见 `README_人工重跑执行流程.md` 和 `README_本机构建与完整运行.md`。
