# 看板多合约同步历史回放

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

- 全页选择一天、一个来源，合约选择支持搜索、选中即添加、标签 × 移除和“添加全部”。取消四块限制，覆盖当天该来源实际可用合约，不承诺无限数量下保持固定刷新速度。
- 日期按原始回调接收时间计算，不按交易日或批次目录名；默认最近一天、一个合约、暂停、10×。切换日期或来源保留可用合约，提示移除不可用项，不自动换成别的合约。
- 所有合约共用北京时间时钟、播放按钮和进度条；各合约价格图与盘口上下排列，横轴范围相同、价格纵轴独立。尚未开始显示空值；已结束保留最后行情并标记，不补造数据。
- 倍速为 1× / 10× / 60× / 300× / 600× / 1800× / 3600×。3600× 表示现实一秒推进一小时行情时间。全页最多每秒四次批量快照请求、一个在途请求；高倍速按同一目标时间读取最近的真实记录，不逐条动画。
- 增删合约时统一暂停、保留当前位置；当前位置超出新的时间范围时定位到最近边界并提示。移除全部后禁用播放。曲线分批加载，最多四个并发请求。
- 切到实时会暂停回放，返回保留选择和进度；刷新整个网页恢复初始单合约，不保存配置。
- 只有所有已选合约均无记录且空档超过60秒时才统一跳过，不代表休市或断线判断。单合约损坏独立报错，不导致其他合约串数据或擅自跳时。
- 曲线按接收时间显示，采用时间桶内高低点抽样，保留首尾，只绘制已播放部分。
- 来源、session、sequence 去重；接收时间相同采用稳定的文件和帧顺序。
- 支持 SBE v2/v3；未知版本、损坏或未完成尾帧显示不完整及文件证据。
- 当天活动归档是加载快照，不自动追行情；“刷新归档快照”统一暂停并重新加载，尽量保留选择和位置。索引版本变化会暂停并提示刷新，避免混用新旧快照。
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

新增只读 `POST /api/replay/snapshots`：JSON 请求包含 `date`、`source`、
`contracts`（合约名称数组）和 `time`（纳秒整数字符串）。
响应含统一 `generation/time`，以及各合约的 `items`（状态、行情、下一条位置及版本证据）。
整批使用同一个只读数据库快照；未开始、已结束、不可用和读取错误分别表示。
请求体最大1MiB；不接收文件路径。旧单合约接口保持兼容，原始归档格式不变。

## 验证

```bash
python -m pytest -q tests/test_dashboard_replay.py tests/test_dashboard_compare.py tests/test_archive_analysis.py
node --test tests/test_dashboard_replay_ui.cjs
```

回放与统计清洗是两件事：浏览器保留原始旧快照，不把行情时间与当前时间做延迟统计。

前端状态机测试使用 Node.js 22 的内置测试运行器，无额外 npm 依赖；Node 仅用于测试，不是 Dashboard 运行依赖。打包入口包含 HTML 和 replay.js 两个静态资源；更新时必须一起部署。
