# 记忆星球后端

界面沿用项目 `demo/index.html` 与原有书架、侧栏和样式，通过 `demo/backend-bridge.js` 连接真实后端。题库仅在后端保存；产品不展示题库栏目或题目列表。双击 `打开记忆星球.cmd` 启动；旧启动脚本也改为打开同一产品入口。`/lab` 兼容地址跳转到现有demo页面。

## 每日挑战与题库（2026-10-09更新）

每日09:00生成15道新选择题；旧题无论作答与否均保留在后台供深度复习安排。深度复习支持选择与简答，两个模式共用原文锚点掌握度。详细规则及限制见 [每日挑战与深度复习](../docs/每日挑战与深度复习-2026-10-09.md)。下文按旧题包每日选一道题的规则已停用。

新增 `bank.py` 管理已审题库与原文锚点、`challenges.py` 管理每日批次、历史去重与09:00周期。`backend.dev` 开启本机后台调度线程。每天15题按课程掌握度分配，生成与审核不足时明确显示缺额，不用旧题补数。进程停止后定时任务暂停，启动时只补当前时段。

私有API：GET `/daily`、GET `/bank`、POST `/bank/{run_id}/{question_id}/start`、GET `/overview`。提交与答案查看仍使用practice接口。旧POST `/runs/{run_id}/daily` 返回410。

当前每日批量出题使用 `daily_generate_model=mimo-v2.6-flash`，提炼/答疑/简答评审仍为MiMo Pro，独立审核为GLM，均通过用户指定的星流接口。配置可在本机 `data/local-settings.json` 修改。

验证：`python -m unittest backend.test_backend backend.test_practice backend.test_core backend.test_ingestion backend.test_challenges -v`。

## 2026-10-09 本地产品与真实WPS接入

使用 `data/local-settings.json` 配置本机路径和模型：当前模型经星流调用，提炼/出题使用 `mimo-v2.6-pro`，独立审题使用 `glm-5.3`。不使用小米直连。`storage=local` 禁止云文件归档；凭据只保存文件路径，设置文件不返回给页面。

WPS本地读取：`python -m backend.wps_reader <链接> --session-file <会话文件路径> --note/--presentation/--video --output <目录>`。会话仅发往对应WPS服务，不进入Markdown、截图处理包或模型请求。动态正文按稳定块ID去重，演示逐页截图；失败明确报告，不把HTTP 200或应用外壳当完整材料。

图片补提取：`python -m backend.vision data/wps/training-slides`。当前37页已经完成图文草稿提取，每页保存截图哈希、模型和原始输出，仍待视觉审核。它们不会自动成为可考证据。

视频：播放器元数据及授权播放分片已接入，本地提取16kHz单声道音频。它不同于原文件下载接口；原文件下载接口对该分享链接返回拒绝。未获取到字幕轨道。`python -m backend.transcribe <audio.wav> --output <目录>` 用本地ASR产生带时间戳的转写草稿，逐段保存可恢复进度，不上传音频。ASR权重为公共下载，在本机核对官方SHA256后加载。词语、数字和否定条件仍需要音视频核对。

出题按显式批次执行：`python -m backend.pipeline --course personal-learning --batch-start 0 --batch-size 60`，覆盖字段明确记录批次在全课程中的位置。引用只允许恢复原文空白字符，逐字校验最终引用；独立盲答分小批执行，截断输出不接受。通过审核的选择题选项重排并保存原顺序映射，避免答案总在第一个选项。

答疑API当前通过MiMo生成课程证据内的回答，GLM独立核对，再检查范围是否仍有效；引用必须属于本次检索到的已审证据。证据不足拒答、审核不通过只返回原文，不执行操作。

运行验证：`python -m unittest backend.test_backend backend.test_practice backend.test_core backend.test_ingestion -v`。

## 2026-10-08 核心功能调整

当前优先级与实现边界见 [核心产品-v4](../docs/核心产品-v4.md)。暂停页面和桌宠。

新增 `library.py`：`set_scope` 定义 training/personal 和可考主题（personal必须提供笔记依据）；`ingest` 将文本块写入 `课程库/<文档ID>/source.md`，默认待审核；`approve` 显式映射块到主题；`evidence` 只返回当前版本已审批证据。范围改变自动撤销审批。源文件Markdown记录入库时状态，实时审批状态以数据库为准。

CLI导入：`python -m backend.library <课程ID> <DOCX或MD路径>`，必须先为已有课程设定范围。PPT/OCR/网页/视频需先提取文字，目前不会把未经提取的文件当成功。

课程库出题：`python -m backend.pipeline --course <课程ID>`，复用引用校验与盲答审题，额外要求独立超纲检查通过。当前按显式批次处理，单批上限90块，默认70块，覆盖字段记录未处理部分。生成结果仍是review_preview。

新增私有API：PUT `/courses/{id}/scope`、GET `/courses/{id}/evidence`、POST `/courses/{id}/ask`（body为answer问题文本）、POST `/runs/{id}/daily`。daily返回session_id和4个选项，使用既有submit接口提交0至3；只用于后端研发验证，尚无手机点选卡片。

`POST /courses/{id}/daily` 从整门课程当前范围的各个已审题包挑题；历史无范围映射的样例题包不参加课程级每日挑战。当天同课程的选择保持不变。没有未见过的到期题时明确要求补题，不通过反复作答同题增加掌握度。

WPS适配配置：`ZHIWEN_WPS_WEBHOOK_FILE` 保存官方webhook地址；`ZHIWEN_WPS_CHAT_ID` 指定培训群；`ZHIWEN_WPS_BINDINGS_FILE` 保存JSON，如 `{"WPS用户ID":{"owner":"local-owner","course_id":"cdn-edge","audience":"shared_training"}}`。回调 GET/POST `/integrations/wps/callback`，POST只验身份、权限及秒级时间戳并去重入队，不处理模型任务或发送回复。尚未部署公网，ctime单位需联调。个人资料禁止通过该群适配回复。发送函数未自动调用，不会在测试中向群发消息。

测试：`python -m unittest backend.test_backend backend.test_practice backend.test_core -v`。

当前为本机研发服务，监听127.0.0.1:18766。原Demo（18765）保持独立，尚未迁移。产品改为本地SQLite和文件课程库，不需要云端数据库。

## 配置与启动

安装：`python -m pip install -r backend/requirements.txt`。

PowerShell设置文件路径（不把密钥正文写入代码）：

```powershell
$env:ZHIWEN_STARFLOW_KEY_FILE = 'D:\重要\API-Key.txt'
python -m backend.pipeline --sample
python -m backend.dev
```

`pipeline --sample` 会真实调用获准星流模型，产生服务用量，原资料和题包仅存本机。历史DOCX试点仍限定前90个非空文本块；新的WPS来源使用课程库批次流程。成功输出目录为data/runs/<任务ID>，包含来源哈希、阶段输出、用量、耗时、审核意见及题包。失败可用 `--resume <任务ID>` 恢复同一范围及提示版本的任务；范围、提示或模型策略变化时新建任务。结果仍须人工验收，模型一致不等于事实正确。

`backend.dev` 生成本地令牌，将含令牌的预览链接写入data/lab-url.txt。打开该文件中的完整链接；令牌仅供本机预览，重启会变化。云模型及KS3密钥绝不发送给页面。请勿把本机服务直接绑定公网；尚未实现5人身份体系、生产限流和部署。

## 已实现

- 金山云星流JSON输出适配，固定获准域名、超时、拒绝重定向、错误不泄露密钥或供应商响应原文。
- KS3小文件归档与SHA256读回校验。使用官方V2签名和HTTPS；大文件分块上传及生产SDK/V4适配后续完成。
- 原文逐字引用验证、知识点ID与题目ID验证、单选选项验证、量表权重合计100检查。
- 当前由MiMo Pro提炼知识点及出题，GLM独立盲答；歧义、证据不足、超纲及答案不一致的题隔离。简答量表另审。角色模型可通过本地设置配置。
- 真实课程预览页：知识点及原文依据、试答、标准答案、逐评分点审查结果。
- 客观题程序判分；简答两模型逐量表判断。程序按100%/50%/0%计算Q，两模型逐项不一致则待复核、不按0分处理。
- SQLite事务保存答题与评分v2：首次上限80、重复提交幂等、同知识点同日最多一次、看答案或重做同题不涨分，包括多窗口先开题后看答案场景。
- 试点知识点按课程版本隔离，不擅自合并新旧AI生成ID；正式稳定概念映射尚未实现。

## 验证

`python -m unittest backend.test_backend backend.test_practice -v`

覆盖来源导入、鉴权、首答80和渐近增长、重复提交/当天限次、跨用户隔离、提前看答案、评分证据、双模型分歧。

## 尚未完成

通用课程导入界面、自动分块任务队列、视频转写准确性校核、全部图片证据审核、稳定知识点映射、人工复核操作页、本地备份恢复、旧Demo迁移及Windows安装包。WPS手机卡片和回调暂缓。页面与桌宠不在当前优先范围。当前不能宣称整套产品或整门课程已上线。
