# 看板单日历史回放

这是归档浏览器，不是策略行情分发。不会连接柜台、启动 Bridge、修改归档、
启动 Redis 或向 ZMQ 发送消息。实时页面和日报任务不变。

## 启动

在项目 Conda 环境内：

```bash
python main.py dashboard --host 0.0.0.0 --port 8080 \
  --result-root /data/aeron-mvp \
  --replay-cache-root /data/dashboard-replay
```

输入根目录应包含多个 `批次/archive/*.rec`，不是单个 `.rec` 文件。
缓存默认是项目 `result/dashboard-replay`，不得放在原始数据目录里面。

Docker（使用包含本次修改的新镜像；旧 v0.9.1 镜像没有此功能）：

```bash
docker run --rm --name yd-dashboard -p 8080:8080 \
  --mount type=bind,src=/srv/ydtrader/result/aeron-mvp,dst=/data/aeron-mvp,readonly \
  --mount type=bind,src=/srv/ydtrader/dashboard-replay,dst=/data/dashboard-replay \
  --entrypoint ydtrader YOUR_UPDATED_IMAGE \
  dashboard --host 0.0.0.0 --port 8080 \
  --result-root /data/aeron-mvp --replay-cache-root /data/dashboard-replay
```

将宿主路径和镜像名换成实际值，提前创建缓存目录并授予镜像运行用户写权限。
已有 8080 容器时不要重复占用端口，可将映射改为 `8081:8080`。
打开 `http://服务器IP:8080/`，点击“历史回放”。无需容器内 systemd。
本轮不创建或覆盖发布标签。

## 使用与口径

- 一次选北京时间一天，日期按原始回调接收时间计算，不按交易日或批次目录名。
- 日期、来源、合约取自归档。默认最近一天、暂停、10×；切换选择重新定位。
- 1× / 10× / 60× 控制回放时间；拖动定位读取真实记录，不用抽样价格代替盘口。
- 超过 60 秒的无记录区间自动跳过，仅为播放操作，不代表休市或断线判断。
- 曲线按接收时间显示，采用时间桶内高低点抽样，保留首尾，只绘制已播放部分。
- 来源、session、sequence 去重；接收时间相同采用稳定的文件和帧顺序。
- 支持 SBE v2/v3；未知版本、损坏或未完成尾帧显示不完整及文件证据。
- 当天活动归档是加载快照，不自动追行情；点击“刷新归档快照”才重新索引。
- 所有查看者的播放状态独立。索引刷新时其他页面需要重新加载选择，不拼接新旧快照。
- 缺少运行版本时显示 unknown，不用当前 Git 标签补历史数据。

## 运维

首次点击回放时后台构建 SQLite 索引，显示“数据加载中”；实时接口不等待索引。
归档路径、大小、修改时间、运行元数据或解析版本变化后，主动刷新会重建缓存。
索引写入临时数据库，完成后原子替换；失败不覆盖上一份数据库。
盘口按记录偏移读取并校验内容摘要，防止原始文件被替换后错读。
缓存可重新生成；原始 `.rec` 文件不能删。大归档首次索引需要时间和额外磁盘空间。

接口：`GET /api/replay/catalog`、`GET /api/replay/curve`、
`GET /api/replay/snapshot`；选择参数为 `date/source/contract/time`。
`time` 使用精确纳秒字符串，可带 `:记录游标`，快照返回 `next_position`。
`POST /api/replay/refresh` 只刷新缓存。接口不接受原始文件路径参数。

## 验证

```bash
python -m pytest -q tests/test_dashboard_replay.py tests/test_archive_analysis.py
```

回放与统计清洗是两件事：浏览器保留原始旧快照，不把行情时间与当前时间做延迟统计。
