# Third-party notices

本 MVP 运行包包含以下第三方组件：

- Eclipse Temurin / OpenJDK 17.0.19+10。精简运行时中的 `legal/` 目录保留了对应许可证和公告。
- Aeron 1.51.0，Apache License 2.0。运行包在 `licenses/AERON-LICENSE.txt` 中携带其许可证。
- Simple Binary Encoding 1.38.1 仅在构建阶段生成 codec，不把 SBE generator JAR 放入运行包。

可选 CTP bridge 使用 `openctp-ctp` 6.7.11.0。该 Python 原生 wheel 不包含在 Java 自包含
发行包中。`scripts/bootstrap_ctp_tts.sh` 会从 OpenCTP 官网下载经过 SHA-256 固定校验的
TTS-CTPAPI 6.7.11 SDK，并把行情动态库安装进隔离 runtime；使用者仍须遵守其上游许可
以及 CTP/OpenCTP 服务条款。

第三方组件仍分别受其原始许可证约束。
