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
bash scripts/run_dashboard.sh
```

YDApi/CTP 数据源的准确参数和验收方式见 `aeron_mvp/README.md`，授时与行情延迟操作见 `README_授时与行情延迟操作手册.md`。

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
