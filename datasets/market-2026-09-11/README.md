# 2026-09-11 行情重跑数据集

本目录位于 `datasets/market-2026-09-11` 分支。`main` 保持与 Gitea 主分支一致。

## 数据范围

- 北京时间原始回调接收日期：2026-09-11。
- 实盘 `ctp-live-5level`：408,914 条；TTS `ctp-tts-7x24`：38,485 条。
- 来源于 56 个批次，按来源、session、sequence 去重，保留原始行情时间、接收时间、价格和原有盘口档位。
- 来源为 2026-09-14 已建立的只读索引快照。每条记录与原始归档中的 SBE SHA256 已核对；这不是对所有订阅合约全天覆盖完整性的承诺。
- 仅导出这一自然日的完整记录。账号、密码、认证配置、运行日志、时钟探测及私人密钥不包含在数据包中。
- 原批次版本从原 `run.meta` 白名单字段保留，dirty 标记不删除。数据生成器基于 `174e86c6dc1662fe221b25b318cb09c68eed4a85`。

## 下载与校验重跑

```bash
git clone --branch datasets/market-2026-09-11 https://github.com/031asa/high-concurrency.git
cd high-concurrency
conda env create -f environment.yml
conda activate ydtrader-high-concurrency
python datasets/market-2026-09-11/verify_dataset.py
```

验证程序先检查压缩包 SHA256，再安全解压到 `result/sample-market-2026-09-11/`，核对所有文件校验值、逐条接收日期、来源条数，并调用现有清洗分析器重算均值、标准差和分位数，与 `expected-analysis.json` 比较。任一项不符即失败退出；不会连接柜台、Redis 或 ZMQ。

## 看板浏览回放

完成上面的验证后，在仓库根目录运行：

```bash
python main.py dashboard --host 0.0.0.0 --port 8080 \
  --result-root result/sample-market-2026-09-11/market-2026-09-11/aeron-mvp \
  --replay-cache-root result/sample-market-replay-cache
```

打开 `http://127.0.0.1:8080/`，进入历史回放，选择 2026-09-11。首次读取需等待索引建立，再选择来源与合约。

## 格式与边界

压缩包内 `.rec` 是供本项目只读解析器使用的紧凑帧样本：SBE 消息和盘口原样保留，去掉其他日期与空白预分配尾部，并保留 32 字节对齐。Aeron 帧头中的原始位置没有重写，因此**不能将此包当作完整 Aeron Archive 目录直接挂载给 Java Archive 恢复服务**。本数据集用于离线分析和 Dashboard 浏览回放，不是策略分发服务。

`provenance.csv.gz` 记录原批次、原段名、原帧偏移、样本帧偏移和 SBE 哈希。`manifest.json` 提供来源、合约、覆盖时间、快照信息和文件校验值；原始异常时间不被修正或删除，继续交由现有清洗规则处理。

`expected-analysis.json` 只针对该单日样本。原批次跨日累计值不可由单日样本还原，缺失的日志、时钟记录和分发证据也不能被解释为正常或零丢包。
