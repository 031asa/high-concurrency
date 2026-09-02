# YD Market Wire v1

这是高并发行情出口和独立下游行情入口之间唯一共享的网络契约。两个项目不导入、复制或调用
对方源码。

## Transport

- ZMTP over TCP；高并发侧使用 `PUSH bind`，下游侧使用 `PULL connect`。
- 默认端口 `7101`；跨服务器部署必须显式配置私网 IP。
- 每条行情是两个 ZMQ frame：第一帧固定 UTF-8 `snapshot`，第二帧是原始 SBE message。
- 不使用 Python pickle，不传输 Python 对象。

## Message

- Contract name: `MarketQuote`
- Schema ID: `701`
- Template ID: `1`
- Schema version: `3`
- Fixed block length: `324`
- Byte order: little-endian
- Canonical schema: `aeron_mvp/schema/market-data.xml`

字段包括行情时间、接收时间、最新价、五档价量、成交量、成交额、持仓量、开高低收、结算、
涨跌停、均价、合约、交易所、交易日、ActionDay、UpdateTime、来源，以及
`sessionId + sequence`。

## Reliability

- 每个 session 的 sequence 必须连续；缺口或倒退使消费者失败退出。
- 发送超时使生产者失败退出，不允许静默丢行情。
- 恢复由高并发侧 Aeron Archive 显式 replay 完成；相同 recording 的 bytes 不得改写。
- HWM 只是内存上限，不代表允许丢行情。

## Compatibility

- 本接口当前发送 schema version 3 和 block length 324。
- 任何字段布局变更必须升级 schema version，并先为两端增加兼容测试，再部署生产者。
- 两边各自打包和启动；共享的是本契约，不是代码或运行环境。
