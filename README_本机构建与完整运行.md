# YDTrader 本机构建与完整运行

本文用于在 WSL2 中验证源码、构建无许可证 Linux 调试包并运行主要入口。

## 一、准备环境

```bash
cd ~/projects/high-concurrency
conda env update -f environment.yml --prune
conda activate ydtrader-high-concurrency
python -m pytest -q
```

## 二、验证业务入口

```bash
python main.py --help
python main.py order --help
python main.py monitor --help
python main.py marketdata --help
```

这些命令不再读取许可证、不提示项目运行密码，也不会创建激活状态。连接 YDApi 时仍需在本机 `config/account.json` 提供柜台账号密码，并准备正确的 `ydClient.ini`。

## 三、验证高并发行情

先执行确定性链路，确认 Aeron、Archive、Compute、Audit 和 Replay：

```bash
bash scripts/run_aeron_mvp.sh --count 100000 --sync-level 0
```

按数据源选择实时入口。参数以 `aeron_mvp/README.md` 为准：

```bash
bash scripts/run_ydapi_aeron_mvp.sh
bash scripts/run_ctp_aeron_mvp.sh
bash scripts/run_ctp_live_aeron_mvp.sh
bash scripts/run_dashboard.sh
```

运行证据统一写入 `result/aeron-mvp/`，不提交 Git。

## 四、构建调试发布包

```bash
python build_tools/build_release.py
tar -tzf result/ydtrader-linux-x86_64.tar.gz | head
```

构建不接受公钥或私钥参数。发布包包含 SHA-256 完整性清单，安装器会在复制前核对文件，但不做签名或授权校验。

## 五、安装并运行

请只在一次性 WSL 发行版或明确允许写入 `/opt/ydtrader` 的 Linux 机器执行安装：

```bash
tar -xzf result/ydtrader-linux-x86_64.tar.gz
sudo ./ydtrader-linux-x86_64/install/install_linux.sh
sudo install -o root -g "$(id -gn)" -m 0640 account.json /opt/ydtrader/config/account.json
sudo install -o root -g "$(id -gn)" -m 0640 ydClient.ini /opt/ydtrader/config/ydClient.ini
/opt/ydtrader/ydtrader --help
```

安装后可直接运行 `order`、`monitor`、`marketdata`。真实报单必须继续遵守 `--send`、策略身份、暂停标志以及报单/撤单阈值等原有风控要求。

## 六、正式兼容构建

正式交付使用 manylinux2014：

```bash
build_tools/build_manylinux2014.sh
```

该脚本不接受密钥参数，并检查最终二进制的 glibc 兼容性和动态库完整性。
