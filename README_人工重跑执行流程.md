# YDTrader 人工重跑执行流程

## 一、leader 上重新运行

```bash
test -x /opt/ydtrader/ydtrader
test -r /opt/ydtrader/config/account.json
test -r /opt/ydtrader/config/ydClient.ini
/opt/ydtrader/ydtrader --help
```

按需求启动：

```bash
/opt/ydtrader/ydtrader order --help
/opt/ydtrader/ydtrader monitor --help
/opt/ydtrader/ydtrader marketdata --help
```

程序不再要求申请码、许可证、激活或额外运行密码。`account.json` 的密码是 YDApi 柜台登录凭据，必须由本机安全配置继续提供。

## 二、失败定位

1. `--help` 失败：检查可执行文件和动态库。
2. 连接失败：检查 `account.json`、`ydClient.ini`、网络和柜台状态。
3. 行情无数据：检查交易时段、合约、订阅结果和授时报告。
4. Aeron 失败：查看 `result/aeron-mvp/<run-id>/` 中的进程日志和 summary。
5. Dashboard 无数据：确认 Compute/Audit 正在输出 NDJSON 或 summary。

## 三、开发机回归

```bash
cd ~/projects/high-concurrency
conda activate ydtrader-high-concurrency
python -m pytest -q
bash scripts/run_aeron_mvp.sh --count 100000 --sync-level 0
```

需要真实数据源时，再分别运行 YDApi 或 CTP bridge。不要用真实报单来验证安装；先使用帮助、连接、查询和 `checked=2` 路径。

## 四、重新构建交付包

功能调试包：

```bash
python build_tools/build_release.py
```

正式 manylinux2014 包：

```bash
build_tools/build_manylinux2014.sh
```

两个入口都不接收密钥。产物位于 `result/ydtrader-linux-x86_64.tar.gz`。

## 五、首次安装或覆盖升级

安装器拒绝覆盖 `/opt/ydtrader`。升级前先停掉实际运行进程并按运维规范备份本机配置，再由管理员移走旧目录；不要在脚本中使用模糊路径或递归通配符。

```bash
tar -xzf ydtrader-linux-x86_64.tar.gz
sudo ./ydtrader-linux-x86_64/install/install_linux.sh
sudo install -o root -g "$(id -gn)" -m 0640 account.json /opt/ydtrader/config/account.json
sudo install -o root -g "$(id -gn)" -m 0640 ydClient.ini /opt/ydtrader/config/ydClient.ini
```

安装完成后直接运行 `/opt/ydtrader/ydtrader --help`，不再有激活和到期步骤。

## 六、操作前检查

- 运行主机、账号和配置文件是否正确。
- 授时比较是否为 `RESULT: PASS`。
- 使用的数据源和合约是否符合本次任务。
- `result/` 是否有足够磁盘空间。
- 涉及真实报单时，是否已取得负责人明确批准并确认 `--send`、策略身份、暂停标志和阈值设置。
