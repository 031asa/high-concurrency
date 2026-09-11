# 独立行情日报

## 当前版本标识约定（取代下文旧 tag 命名）

报告不再使用 v0.x、tag-multi 或 tag-unknown 作为版本名。
文件名及目录为 日期__报告生成器完整commit哈希；PDF逐批列出行情启动时
run.meta中的完整commit，行情与生成器分开，不以生成器哈希冒充历史行情版本。
历史commit缺失仍为unknown；dirty批次明确提示该哈希不足以还原本地修改。
不创建以哈希命名的Git tag，也不改动或删除旧标签与旧报告。

生成前要求工作树干净，刷新origin分支并核实HEAD已推送；失败则停止，
不覆盖已有产物。定时任务不会自行提交或推送源码，请维护者先审核、提交和推送。
无Git镜像必须由干净且已推送提交生成build-version.json，
包含与commit一致的published_commit证明；旧镜像缺失证明需重新构建。

本工具只读 Archive、run.meta、日志和时钟记录，不导入交易服务，不连接柜台、Redis或ZMQ。
代码在 `code/`，最终产出在 `pdf/<日期>__<行情版本>/`。每个最终目录只有 PDF 和待核验 CSV。
该目录结构按本项目需求设置，产出不进入Git。隐藏的 .state/.history/.failed 分别保存调度状态、旧成功产物和失败尝试，均不提交Git，不自动删历史。

## 环境和一次执行

在Linux/WSL项目根目录使用项目Conda环境：

```bash
conda env update -f environment.yml
conda activate ydtrader-high-concurrency
python timer_pdf/code/main.py --date yesterday
python timer_pdf/code/main.py --date 2026-09-09
# 等价统一入口（加密镜像使用 ydtrader daily-report）：
python main.py daily-report --date 2026-09-09
```

参数：`--input-root`默认result/aeron-mvp，`--clock-root`默认result/time-probes，`--output-root`默认timer_pdf/pdf，
`--rules`默认timer_pdf/code/rules.json，`--font`可指定中文TTF/TTC。
输入目录可任意放置，输出必须在原始输入目录之外；不支持修改归档。

ReportLab与pypdf由根environment.yml管理，Poppler用于渲染验收。Linux安装 `fonts-wqy-microhei` 后自动发现中文字体；
不希望安装系统字体可运行以下步骤（只解包，不安装服务）：

```bash
mkdir -p result/report-fonts
cd result/report-fonts
apt-get download fonts-wqy-microhei
dpkg-deb -x fonts-wqy-microhei_*.deb .
cd ../..
```

字体也可由 `YD_REPORT_FONT=/absolute/path/font.ttf` 提供。字体嵌入PDF，生成不依赖Windows路径或联网。

## 日期、清洗和解释

- 日期为北京时间接收自然日，不使用TradingDay替代。不同来源独立统计，回放不计算实时延迟。
- 使用来源+session+sequence去重，保留原始记录。累计平均值与当日平均值分开，累计值明确为覆盖本日批次的全量样本。
- rules.json提供2026交易日历、假期前停夜盘、品种日盘和夜盘；夜盘跨午夜为同一个session。规则范围外、未知品种不能默认为正常交易日。
- `overrides`可指定某自然日的分组时段，例如 `{"2026-09-11":{"index":[]}}` 表示该日index组休市；
  override日未提供的组视为未知。完整时段使用带+08:00的ISO起止时间。临时停市必须维护配置，不联网猜测。
- 事件时间不在连续交易时段的有效数据按约定视为非实时快照；闭市后收到较旧的前时段行情同样排除。
  这不等同于证明柜台快照标记，原数据不删除。盘前集合竞价数据也不进入连续交易延迟。
- 收盘整秒（例如15:00:00.xxx）纳入。事件处于交易时段且接收略跨收盘、差值不超过180秒仍保留；
  重新开盘后收到另一时段的旧记录进入待核验，不自动认定正常。
- 交易时段内真实慢行情保留实时主均值，另提供严格剔除>180秒后的参考均值。恰好180秒保留。
- 来源级>60秒接收间隔按当日观测合约的时段并集扣除休市。缺少逐时订阅状态，只是覆盖候选，不是断联证明。
- Mux/Publisher报错按批次、来源、expected/actual去重，各层分开；统计范围为覆盖当日批次的完整日志。
  无独立报错时间时不冒充精确当天事件；各层可能同因，不相加为丢包数。
- CSV包含全部待核验数据、日志事件和证据缺失项；它的总行数不是故障数。每类PDF最多展示5条完整例证。
- 只读已有时钟记录，失败不填0，分主机/授时源，超过11分钟的采样空档列为覆盖待核验。
  公网NTP不冒充交易所授时，时钟偏移不直接抵扣行情观测差。
- 支持本项目未分片SBE v2/v3 Archive；v2来源取批次元数据。遇到损坏/不支持帧标记INCOMPLETE，退出1并输出到.failed，保留之前成功版本。
  无行情但读取完整生成NO_DATA说明报告，字段不可得不填0。未知规则会完整列待核验，不代表统计有效。
- 同一输出根目录加进程锁；另一任务并发启动会失败退出。PDF/CSV全部生成验收后才发布目录。
  已有成功文件转存.history，不静默删除。异常退出遗留的临时目录不当作成功结果。

## 版本留档

`python main.py version-info`显示当前运行代码版本；源码取精确HEAD tag、commit及dirty，无精确tag时不套用最近tag。
打包准备阶段生成build-version.json和build-version.meta供无.git镜像使用。
源码归档打包前应保留这两份构建元数据；缺Git且无文件只能标unknown，不能填写一个猜测的tag。

单源/多源pipeline启动时一次读取身份，后续run.meta更新复用该值。已经运行的历史批次不补写版本。
日报分别记录行情运行版本与生成器版本。多行情版本命名tag-multi并按组列出；未知为tag-unknown。
未打tag采用commit-短哈希，本地修改附-dirty。文件名中的版本为行情版本，不能拿生成器v0.9.0代替历史行情版本。

## 每天09:00与补报

```bash
python timer_pdf/code/install_timer.py --since 2026-09-09
systemctl --user start ydtrader-daily-report.service
systemctl --user list-timers ydtrader-daily-report.timer
journalctl --user -u ydtrader-daily-report.service -n 50
```

调度使用 `09:00 Asia/Shanghai` 和Persistent。恢复时从since到昨天逐日补齐；
成功产物按.state中的SHA256核验后跳过；损坏/缺失产物重算，失败日期下次重试。
要更新已成功生成的历史报告，手动指定 `--date`，不要带catch-up。
任务配置由安装器写入当前Linux用户的systemd目录；安装不会启动行情、交易、Dashboard或时钟服务。

用户systemd需持续存活。可由管理员运行 `loginctl enable-linger <用户名>`。
Windows关机、睡眠、WSL关闭时不能执行；主机恢复后补报。服务器长期运行更可靠。
停用：`systemctl --user disable --now ydtrader-daily-report.timer`。这不会删除报告。
调度失败通过systemd状态和journal查看；不再依赖Codex，也不自动发聊天通知。

## Docker

镜像需包含本次代码、ReportLab/pypdf与中文字体（product.toml已声明）。容器通常没有systemd，使用宿主机定时器调用：

```bash
docker exec <容器名> ydtrader daily-report \
  --date yesterday --catch-up-from 2026-09-09 \
  --input-root /opt/ydtrader/result/aeron-mvp \
  --clock-root /opt/ydtrader/result/time-probes \
  --output-root /opt/ydtrader/timer_pdf/pdf
```

把实际可执行路径替换为镜像中的路径。归档及clock目录可只读挂载，输出目录必须可写并持久化。
可单独启动报告容器共享数据卷，不需要交易账号，不启动交易服务。
宿主机systemd使用同样OnCalendar/Persistent设置，ExecStart替换为上面的docker exec绝对路径命令。
`--catch-up-from`负责补齐多日，不能仅依赖Persistent的一次补触发。

### 宿主机定时入口

新增入口仅封装上述docker exec命令，沿用容器内原有日报锁和补报逻辑，不启动或重启任何行情服务：

```bash
bash timer_pdf/code/run_daily_docker.sh yd-market 2026-09-09 \
  --input-root /opt/ydtrader/result/aeron-mvp \
  --clock-root /opt/ydtrader/result/time-probes \
  --output-root /opt/ydtrader/timer_pdf/pdf
```

`yd-market`替换为已有容器名，所有报告路径是容器内部路径。若可执行文件不在PATH，设置
`YDTRADER_REPORT_EXECUTABLE=/opt/ydtrader/ydtrader`。容器须运行且宿主机调度用户须具有Docker访问权限。
真实日期有效性由日报入口检查，Docker或报告失败的退出码原样传回，不伪报成功。

现有宿主机systemd服务的ExecStart可使用以下形式（替换路径和容器名）：

```ini
ExecStart=/usr/bin/bash /absolute/project/timer_pdf/code/run_daily_docker.sh yd-market 2026-09-09 --input-root /opt/ydtrader/result/aeron-mvp --clock-root /opt/ydtrader/result/time-probes --output-root /opt/ydtrader/timer_pdf/pdf
```

对应timer沿用`OnCalendar=*-*-* 09:00:00 Asia/Shanghai`和`Persistent=true`。
Linux直接运行与Docker定时入口二选一，不重复安装两套日报任务。

### 版本范围

v0.9.1基于v0.9.0，仅补充Docker定时入口、说明和测试；行情接入、Aeron、ZMQ代码不变。
Git标签更新不会自动替换已构建镜像或已部署服务。
