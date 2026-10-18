# 记忆星球

本分支是 **v0.0.2 Windows x64 便携交付版**的源码。`main`保留开发基线；产品已有数据不在仓库中。

## 下载版本

| 版本 | 用途 | 下载 |
| --- | --- | --- |
| **v0.0.2** | **给其他用户使用的便携交付版**，带运行环境，首次填写自己的星流 API KEY，wps_sid 可稍后填写 | [便携版发布页](https://github.com/Qingying-Lyu/Memory-Planet/releases/tag/v0.0.2) |
| v0.0.1 | 本地开发环境备份，不是面向其他用户的便携交付包 | [开发备份发布页](https://github.com/Qingying-Lyu/Memory-Planet/releases/tag/v0.0.1) |

请在 v0.0.2 发布页下载 **Memory-Planet-v0.0.2-portable-win-x64.zip**。GitHub自动生成的Source code ZIP仅是源码，不包含Python环境、Chromium和语音模型。

## 首次使用

1. 完整解压便携ZIP到有写入权限的文件夹。
2. 双击“打开记忆星球.cmd”。
3. 填写自己的星流API KEY；wps_sid可稍后在右上角“连接设置”补填。
4. 进入空课程库，添加自己的课程与材料。

包内包含Python及依赖、Chromium后台浏览器、FFmpeg和离线语音模型，无需自行安装Python或WPS桌面客户端。KEY、账号权限及模型费用由使用者自己提供。凭据使用Windows当前用户加密保存，不随学习备份导出；换电脑或Windows用户后需重新填写。

关闭网页不会停止后台。使用“关闭记忆星球.cmd”确认关闭。本版本不附恢复CMD入口，后台备份能力保留。

详细说明见[记忆星球使用与规则说明](记忆星球使用与规则说明.md)。ZIP约450MiB，解压后约1GB。发布包排除所有已有课程、材料、题目、成绩、历史备份、日志、密钥及WPS会话。

## 本分支与开发基线的差异

启动入口移至scripts并使用包内runtime；增加首次连接配置、配置前任务隔离、Windows凭据保护、统一包内Chromium和目录迁移。目录迁移先备份数据库，再修正内部路径，不重算成绩。已有核心业务规则保持不变。

backend、demo包含产品源码及开发验证文件，发行ZIP采用白名单，仅包含实际运行文件。运行环境、浏览器和模型通过Release附件分发，未提交到Git。根目录其余产品与研发说明沿用开发基线；便携版的安装、配置及目录结构以本文和随包使用说明为准。

验证：136项原有隔离测试、7项便携专项测试、实际ZIP首次启动、可选WPS配置、空课程库、无系统Python路径启动、重复启动、中文/空格目录移动、数据保留、包内Chromium读取已授权WPS页面、离线语音模型及FFmpeg运行。ZIP已完成文件清单、CRC、SHA256和凭据扫描。

第三方组件见[THIRD-PARTY-NOTICES.txt](THIRD-PARTY-NOTICES.txt)及各组件随包许可证。
