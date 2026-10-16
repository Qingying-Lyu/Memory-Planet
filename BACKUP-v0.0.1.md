# Memory Planet v0.0.1 — 本地开发版本备份

备份日期：2026-10-16。此版本用于留档和恢复开发环境，不是免配置的交付便携版。

## 包含与排除

- 包含当前 backend、demo、docs、reader-extension、根目录说明 Markdown 和启动/恢复脚本。
- 已删除 25 份废弃或历史文档；保留的产品规则与源码已同步。旧示例链接及开发机路径仍可能存在，首次配置与便携路径改造尚未实现。
- Release ZIP 额外包含 Windows x64 Python 运行环境、产品所需已安装依赖、FFmpeg、Playwright 运行组件、公开语音模型及运行库。
- 排除现有 data、backups、课程、课程库、真实数据库、材料、题库、答题、掌握度、复习足迹、日志、令牌、密钥及 WPS 会话文件。
- 只允许白名单中的公共 ASR 模型从 data/models 纳入，不包含其他 data 文件。
- 未复制整个开发机 Python 环境中的无关 SDK。实际纳入的依赖版本见 requirements-v0.0.1.lock.txt。

## 恢复提示

1. 解压 Release ZIP 到独立目录，不要覆盖已有用户数据。
2. 现有源码和启动脚本保持开发时状态，仍有绝对路径，首次配置窗口尚未实现；本备份不能直接当成最终用户交付包。
3. 如需在另一目录验证环境，在 PowerShell 中进入解压目录后运行：

```powershell
$env:PYTHONHOME = Join-Path (Get-Location) 'runtime'
$env:PYTHONNOUSERSITE = '1'
$env:PYTHONPATH = ''
& .\runtime\python.exe -c "import fastapi, uvicorn, httpx, playwright, fitz, pptx, openpyxl; print('environment OK')"
```

4. 恢复实际使用时需自己提供星流 Key 与 WPS 授权；本包没有你的个人配置。ASR 模型位于 models/faster-whisper-base，相关模型与 DLL 路径需按新位置配置。WPS 动态页面读取仍依赖系统 Edge。
5. 后续交付版需要另外处理首次配置、便携路径及数据保留更新，不能把本备份等同于已完成这些改造。

## 第三方组件

Python 许可证保留在 runtime/LICENSE.txt。Python 依赖的包元数据及随包许可证保留于 runtime/Lib/site-packages 下；Playwright 自带驱动/Node 与 FFmpeg 的随包说明一并保留。Microsoft VC 运行库按其再分发条款使用；公开语音模型的来源许可见第三方说明。

## 数据安全检查

发布前检查源码不包含本机凭据正文，检查 ZIP 路径无用户数据目录，并生成逐文件 SHA256 清单及 ZIP 校验和。原本的使用数据与历史备份未修改。
