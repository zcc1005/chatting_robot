# 交建通施工日报机器人

这是“交建通施工日报机器人”的第一阶段后端。除交建通机器人 JSON 回调外，也兼容企业微信自建应用的 XML 回调，可先用企业微信验证 Token、EncodingAESKey、URL 验证、消息验签、解密和本地保存链路。

当前已实现：

- 回调 URL 验证；
- POST 消息签名校验和 AES-256-CBC 解密；
- 同一 POST 地址按 `Content-Type` 分流交建通 JSON 与企业微信 `text/xml`/`application/xml`；
- 企业微信 XML 安全解析、通用字段标准化和 `success` 应答；
- 解密业务 JSON 的安全日志（`response_url`、token 类字段会脱敏）；
- 按上海时区写入每日 JSONL 文件；
- 最多保留 10,000 个 `msgid` 的进程内 LRU 去重；
- 将 text、image、mixed、file 明文转换为统一消息模型；
- 使用 SQLite 保存消息与附件元数据，并通过 `msgid` 唯一约束去重；
- 提供开发环境明文模拟接口、消息列表和详情查询接口；
- 健康检查和本地自动化测试。

当前未实现：生产环境自动命令识别、定时发送、真实 `response_url` 协议联调、文件内容识别，以及在真实交建通消息协议中发送图文卡片。开发验收页面已经提供仅限本地 Mock 的日报命令自动闭环。

## 环境要求与安装

- Python 3.10 或 3.11
- Windows PowerShell

在本项目目录执行：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```

打开 `.env`，填写交建通机器人配置：

```dotenv
JJT_CALLBACK_TOKEN=交建通后台配置的回调Token
JJT_ENCODING_AES_KEY=交建通后台配置的43位EncodingAESKey
JJT_RECEIVE_ID=
JJT_MESSAGE_DATA_DIR=./data/messages
JJT_LOG_LEVEL=INFO
JJT_TIMEZONE=Asia/Shanghai
APP_ENV=development
ENABLE_MOCK_API=true
ENABLE_JJT_CALLBACK=false
DATABASE_URL=sqlite:///./data/jjt_bot.db
```

交建通智能机器人场景的 `JJT_RECEIVE_ID` 保持空字符串。使用企业微信自建应用测试时，将它设置为企业的 CorpID，例如 `ww1234567890abcdef`。仅当 `ENABLE_JJT_CALLBACK=true` 时，程序才要求 Token 非空并校验 43 位 EncodingAESKey；离线模式可在没有真实平台密钥时启动。真实 Token 和 EncodingAESKey 只能放在已被 Git 忽略的 `.env` 中。

## Docker 镜像与阿里云部署

项目提供 `Dockerfile`、`compose.yaml`、完整的 `.env.docker.example` 配置模板，以及 Windows 一键构建推送脚本。镜像以非 root 用户运行，监听 8000 端口，并内置 `/health` 健康检查。SQLite 数据库、消息记录和下载图片统一保存在 `/app/data` 数据卷中，容器升级后不会丢失。

> 仓库为公开仓库时，绝对不要把 `.env`、API Key、回调 Token、EncodingAESKey 或现有数据库复制进镜像。镜像层能被任何拉取者检查；配置应通过 Compose 的 `env_file` 在启动时注入。

### 1. 准备生产配置

```powershell
Copy-Item .env.docker.example .env.docker
```

编辑 `.env.docker`，至少填写 `JJT_CALLBACK_TOKEN` 和 `JJT_ENCODING_AES_KEY`。如果需要日报大模型或图片识别，再填写对应的 `LLM_*`、`VISION_*` 配置。`.env.docker` 已加入 Git 和 Docker 忽略规则，不会进入源码仓库或镜像。

### 2. 本地构建并推送阿里云

先安装并启动 Docker Desktop，然后登录阿里云镜像仓库。密码应在 Docker 的交互提示中输入，不要写入脚本：

```powershell
docker login --username=alyzcc crpi-5zyp5pdzn7yrv5oo.cn-beijing.personal.cr.aliyuncs.com
```

构建并推送版本 `0.2.0`，同时更新 `latest`：

```powershell
.\scripts\build-and-push.ps1 -Tag 0.2.0 -AlsoLatest
```

脚本默认构建常见的 `linux/amd64` 镜像；如果阿里云服务器是 ARM 实例，改用 `-Platform linux/arm64`。

脚本推送到：

```text
crpi-5zyp5pdzn7yrv5oo.cn-beijing.personal.cr.aliyuncs.com/zcc_0811/chat_robot:0.2.0
```

### 3. 在服务器启动

将 `compose.yaml` 和填写好的 `.env.docker` 放在服务器同一目录。登录仓库后执行：

```bash
docker compose pull
docker compose up -d
docker compose ps
curl http://127.0.0.1:8000/health
```

如需换端口，可在命令前设置 `HOST_PORT`；如需部署其他标签，可设置 `IMAGE_TAG`。例如 Linux Shell：

```bash
HOST_PORT=8080 IMAGE_TAG=0.2.0 docker compose up -d
```

查看日志和停止服务：

```bash
docker compose logs -f --tail=200
docker compose down
```

`docker compose down` 不会删除命名数据卷。不要使用 `docker compose down -v`，否则会删除 SQLite 数据库和消息文件。升级时先备份 `chat-robot-data` 数据卷，再拉取新标签并重建容器。

## 启动与检查

使用启动脚本：

```powershell
.\run.ps1
```

或在已激活的虚拟环境内直接启动：

```powershell
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

- 健康检查：<http://127.0.0.1:8000/health>
- Swagger：<http://127.0.0.1:8000/docs>

## 离线业务开发模式

当前没有公网 HTTPS 地址时，推荐使用：

```dotenv
APP_ENV=development
ENABLE_MOCK_API=true
ENABLE_JJT_CALLBACK=false
DATABASE_URL=sqlite:///./data/jjt_bot.db
```

此模式不会注册 `/api/jjt/callback`，因此不需要真实交建通 Token 或 EncodingAESKey。启动命令：

```powershell
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

打开 <http://127.0.0.1:8000/docs>，使用 `POST /api/dev/mock-message` 提交交建通解密后的明文 JSON。例如：

```json
{
  "msgid": "mock-text-001",
  "aibotid": "bot-001",
  "chatid": "construction-group-001",
  "chattype": "group",
  "from": {"userid": "user-001"},
  "msgtype": "text",
  "text": {"content": "兴城项目施工日报"}
}
```

首次提交返回 `saved`，相同 `msgid` 再次提交返回 `ignored`。查询接口：

- `GET /api/messages`：支持 `chatid`、`msgtype`、`process_status`、`start_time`、`end_time`、`limit`、`offset`；
- `GET /api/messages/{msgid}`：返回消息、附件元数据和脱敏后的详情。

SQLite 默认位于 `data/jjt_bot.db`。模拟接口和真实交建通 JSON 回调都调用同一个 `MessageService.process_plain_message`；真实回调额外执行验签、AES 解密和 JSONL 原始审计备份。消息接收接口本身不会调用大模型；开发验收页面会在规则初筛后显式调用结构化提取 API。当前阶段不会下载附件或回复交建通群聊。

正式环境未来应配置：

```dotenv
APP_ENV=production
ENABLE_MOCK_API=false
ENABLE_JJT_CALLBACK=true
```

交建通后台应填写可从公网访问的 HTTPS 地址：

```text
https://你的公网HTTPS域名/api/jjt/callback
```

同一个地址同时处理 GET URL 验证和 POST 消息回调。GET 验证会在完成签名校验和解密后直接返回原始明文，不带 JSON 引号、BOM、额外换行或 HTML。部署时应保证此请求在 1 秒内返回。

## 消息入口与格式兼容

项目明确区分三种消息入口，信封解析逻辑互不混用：

1. **交建通机器人真实回调**：请求使用 JSON 信封，格式为 `{"encrypt":"密文"}`。接口只从小写 `encrypt` 字段读取密文，完成验签和 AES 解密后，将解密得到的业务 JSON 交给 `MessageService`。该入口不会调用 XML 解析器。
2. **企业微信传统回调**：请求的 `Content-Type` 为 `text/xml` 或 `application/xml`，信封中使用 `<Encrypt>`。该入口先解析 XML 信封，解密后的正文仍按企业微信传统 XML 处理。
3. **本地模拟接口**：`POST /api/dev/mock-message` 直接接收已经解密的业务 JSON，不验签、不解密，也不访问外部网络。

交建通 JSON 与企业微信 XML 入口只共用底层的 SHA1 验签和 AES-256-CBC 解密能力；两者的信封和明文解析完全分开。用户提供的 `python.zip` 是旧版 Python 2 XML 加解密参考，仅用于核对 SHA1、AES-256-CBC、PKCS#7 和消息长度规则；项目不会复制其中代码或安装 `pycrypto`，仍使用 Python 3 与 `pycryptodome`。

解密后的业务消息兼容规则：

- `text`、`image`、`mixed`、`file` 按现有逻辑标准化并保存；附件只保存元数据，不下载文件。
- 单聊（`chattype=single`）缺少 `chatid` 时，内部生成 `single:{userid}`；群聊缺少 `chatid` 时返回参数错误。
- `quote` 完整保留在 `raw_payload`/`raw_json`，但暂不拼入 `text_content`；引用内容中的图片也不会重复保存为当前消息附件。
- `voice` 和未知 `msgtype` 不触发下载、识别或 URL 执行；原始 JSON 会保存，`process_status` 记为 `unsupported`，接口不会因此返回 HTTP 500。

## 施工日报初步识别

消息成功写入 SQLite 后，系统会对 `text` 和 `mixed` 消息的 `text_content` 执行一次完全本地、可解释的规则识别。当前不使用大模型、机器学习、OCR 或外部 API，也不会下载图片、调用 `response_url` 或生成最终日报。纯图片、文件、语音以及没有正文的消息会安全地记为 `not_applicable`。

识别结果保存在独立的 `message_report_detections` 表中，不会覆盖 `messages.raw_json`，也不会改变 `messages.process_status` 的含义。每个 `message_id` 只有一条当前识别结果；通过重识别接口重复执行时更新原记录。

四种识别状态：

- `report_candidate`：分数不低于 5，明显像施工日报；
- `needs_review`：分数为 2–4，命中部分特征但信息不足，需要人工确认；
- `ignored`：分数不高于 1，普通聊天或不像日报；
- `not_applicable`：不是 `text`/`mixed` 正文消息，或正文为空。

当前 `rules-v3` 规则与分数：

| 规则 | 分数 |
| --- | ---: |
| 包含“施工日报”或明显的“项目日报” | +4 |
| 包含“今日完成”“今日施工”“施工内容”或“施工进度” | +2 |
| 包含 `2026年8月6日`、`2026-08-06`、`8月6日` 等日期 | +1 |
| 包含“管理人员8人”“施工人员117人”“作业人员20人”等人员数量 | +1 |
| 包含“挖掘机2台”“吊车1辆”“机械设备3台”等机械设备数量 | +1 |
| 包含“天气晴”“晴天”“小雨”等天气描述 | +1 |
| 包含项目、标段、工区、楼栋、隧道、桥梁等工程场景词 | +1 |
| 去除正文首尾空白后长度不少于 30 个字符 | +1 |
| 不超过 30 字且明显是在询问或催要日报，如“今天的施工日报呢” | -4 |

单独出现“日报”不会直接成为候选，所有规则都按总分阈值判断。明显的日报询问会直接作为普通聊天过滤；仍有歧义的 `needs_review` 消息可由结构化提取阶段的大模型继续做相关性复核。

在开发模式启动服务后，打开 Swagger <http://127.0.0.1:8000/docs>，使用 `POST /api/dev/mock-message` 提交测试消息。例如：

```json
{
  "msgid": "manual-text-001",
  "chatid": "construction-group-001",
  "chattype": "group",
  "from": {"userid": "user-001"},
  "msgtype": "text",
  "text": {
    "content": "兴城项目施工日报，2026年8月6日天气晴，施工人员20人，今日完成桥梁桩基施工。"
  }
}
```

首次保存响应会包含 `detection_status`、`score`、`is_report_candidate`、`matched_rules` 和 `reason`。真实交建通回调仍按回调协议返回确认响应，可通过查询接口查看识别结果：

- `POST /api/messages/{msgid}/detect-report`：重新识别一条已保存消息；
- `GET /api/report-detections`：支持 `detection_status`、`chatid`、`limit`、`offset` 筛选；
- `GET /api/report-candidates`：只查询 `report_candidate` 候选消息；
- `GET /api/messages/{msgid}`：通过可选的 `report_detection` 字段查看该消息的识别详情。

规则识别仅用于初筛，只有 `report_candidate` 和 `needs_review` 可以进入下面的单条日报结构化提取流程。

## 单条施工日报结构化字段提取

结构化提取只通过 `POST /api/messages/{msgid}/extract-report` 触发，消息接收和规则识别服务本身不会隐式调用大模型。开发验收页面会在发送候选日报及生成预览前自动调用这个现有接口；直接使用后端 API 时仍由调用方决定何时提取。

处理流程如下：

1. 查询消息及其初步识别结果；
2. 拒绝 `ignored`、`not_applicable`、纯图片、文件、语音和空正文；
3. 在 `project_reports` 中创建或更新唯一的 `pending` 记录；
4. 独立大模型客户端只接收 `text_content`，同时返回相关性结论和结构化纯 JSON；
5. 后端对原文中的月日日期做确定性补全，并忽略没有明确 `content` 的孤立施工子项，所有处理都保存来源或警告；
6. 使用严格 Pydantic 模型校验 JSON、字段类型、额外字段和缺失字段一致性；
7. 成功时更新主记录，并替换 `report_equipment`、`report_work_items` 子项；
8. 非法 JSON、其他字段类型错误、响应错误或超时会将当前结果更新为 `failed`，不会修改或删除原消息和初步识别结果。

结构化字段：

| 字段 | 含义 |
| --- | --- |
| `project_name` | 原文明确给出的项目名称 |
| `report_date` | 日报日期，API 使用 `YYYY-MM-DD` |
| `weather` | 原文天气描述 |
| `management_count` | 管理人员数量 |
| `worker_count` | 施工或作业人员数量 |
| `equipment` | 机械列表，每项包含 `name`、`count`、`unit` |
| `work_items` | 施工项列表，每项包含 `location`、`content`、`progress` |
| `tomorrow_plan` | 明日计划 |
| `safety_status` | 安全情况 |
| `quality_status` | 质量情况 |
| `missing_fields` | 原文未提供的结构化字段名 |
| `confidence` | 大模型给出的 0–1 提取置信度；人工结果可以为空 |
| `extraction_status` | `pending`、`completed`、`needs_review` 或 `failed` |
| `extraction_source` | `llm` 或 `manual` |
| `relevance_status` | `report`、`related_update`、`ordinary_chat`、`uncertain` 或历史记录的 `not_reviewed` |
| `relevance_reason` | 大模型对相关性判断的简短依据 |
| `relevance_confidence` | 0–1 的相关性置信度 |
| `date_source` | 日期来自模型、原文完整日期、月日结合消息年份或人工修正 |
| `normalization_warnings` | 被安全忽略的异常施工子项等确定性处理记录 |
| `raw_extraction_json` | 大模型原始返回文本，仅存储，不执行 |

原文没有提供的标量字段必须返回 `null`；没有机械或施工项时，相应字段也必须为 `null`。这些字段必须同时列入 `missing_fields`，不能使用空字符串、空数组或编造的默认值。服务会复核 `missing_fields` 与所有 `null` 字段是否完全一致。原文明示完整年月日时直接使用；只写“8月10日”等月日时，以消息接收时间为基准选择最近的合理年份并记录 `date_source=text_month_day_message_year`。完全没有月日仍保持 `null`。缺少 `project_name`、可解析日期或 `work_items` 任一关键字段时，状态仍为 `needs_review`。

相关性结论与字段完整度互不混用：`report` 进入正常日报判定；`related_update` 表示施工补充但不是完整日报；`ordinary_chat` 会把本地识别结果复核为 `ignored`，不需要人工补齐日报字段；`uncertain` 才继续交给人工判断。大模型原始 JSON 始终保留。若一个施工子项只有位置而没有任何明确施工内容，后端不会编造内容，而是忽略该子项、写入 `normalization_warnings`，其余有效字段仍可保存。

大模型使用兼容 Chat Completions 的接口，所有配置均来自 `.env`，项目没有硬编码 API Key、模型名或 base URL：

```dotenv
LLM_API_KEY=替换为服务端密钥
LLM_MODEL=替换为模型名称
LLM_BASE_URL=https://你的兼容服务地址/v1
LLM_TIMEOUT_SECONDS=90
LLM_MAX_RETRIES=1
```

前三项未完整配置时，服务仍能正常启动，健康检查、消息接收和本地识别不受影响；提取接口会返回 HTTP 503 和明确配置提示。`LLM_TIMEOUT_SECONDS` 是等待模型返回的单次读超时，默认 90 秒；连接阶段最多等待 10 秒。`LLM_MAX_RETRIES` 默认 1，允许范围为 0–3，只对超时、网络瞬时错误、HTTP 429 和 5xx 自动重试，不会重试 400 等确定性请求错误。日志不会输出 API Key、`response_url` 或完整日报正文。

在 Swagger <http://127.0.0.1:8000/docs> 中：

- 调用 `POST /api/messages/{msgid}/extract-report` 手动触发单条提取；
- 调用 `GET /api/project-reports/{msgid}` 查询一条结果；
- 调用 `GET /api/project-reports` 查询结果，支持 `project_name`、`report_date`、`extraction_status`、`chatid`、`limit`、`offset`；
- 调用 `PATCH /api/project-reports/{msgid}` 人工修正字段，保存后 `extraction_source` 自动变为 `manual`。

人工修正示例：

```json
{
  "project_name": "人工确认后的项目名称",
  "worker_count": 120,
  "work_items": [
    {
      "location": "3号墩",
      "content": "模板安装",
      "progress": null
    }
  ]
}
```

单条结构化结果可以进入下一节的确定性汇总预览，但项目字段提取本身仍不会自动触发。

## 群聊施工图片识别与项目关联

群聊中的纯图片和 mixed 图片可以通过独立视觉识别流程提取图片内文字和可见施工信息。该流程不会改变原消息、规则识别结果或结构化日报，也不会把图片识别出的人员、机械数量直接写入数值统计。

每张图片的识别结果保存在 `message_image_recognitions`，包括图片中可见的项目名称、日期、拍摄时间、天气、地点、施工内容、OCR 文字、现场描述、置信度和图片 SHA-256。图片与结构化项目日报的关系独立保存在 `project_report_images`。

自动关联使用以下确定性评分：

| 关联依据 | 分数 |
| --- | ---: |
| 图片项目名称完全匹配 | +5 |
| 项目名称高度相似 | +4 |
| 项目名称部分相似 | +3 |
| 图片和日报包含相同施工关键词 | +2 |
| 图片与日报发送人相同 | +2 |
| 发送时间相差不超过 10 分钟 | +2 |
| 发送时间相差不超过 30 分钟 | +1 |
| 图片日期与日报日期一致 | +1 |

同一 `chatid` 和日期是候选范围。自动关联必须同时满足：最高分不低于 5、命中项目名称或施工关键词等内容证据，并且领先第二候选至少 2 分。只有“同一发送人、时间接近”而没有内容证据时会标记为 `needs_review`，不会自动绑定项目。

关联状态：

- `linked`：证据充分，已自动关联；
- `needs_review`：存在候选项目，但需要人工确认；
- `unmatched`：没有可信候选；
- `manual`：人工指定的项目关联。

视觉模型使用兼容 Chat Completions 的多模态接口，需支持 `image_url` data URL：

```dotenv
VISION_API_KEY=替换为视觉模型密钥
VISION_MODEL=替换为支持图片的模型名称
VISION_BASE_URL=https://你的兼容服务地址/v1
VISION_TIMEOUT_SECONDS=90
VISION_MAX_RETRIES=1
IMAGE_DOWNLOAD_TIMEOUT_SECONDS=15
IMAGE_MAX_BYTES=10000000
ENABLE_AUTO_IMAGE_RECOGNITION=false
```

`VISION_API_KEY` 和 `VISION_BASE_URL` 未单独设置时可沿用对应的 `LLM_*` 配置，但 `VISION_MODEL` 必须明确配置为支持图片的模型。未配置视觉模型时服务仍能启动，手动识别接口返回 HTTP 503。

接口：

- `POST /api/messages/{msgid}/recognize-images`：识别一条群聊消息中的全部图片并执行项目关联；
- `GET /api/image-recognitions`：按 `chatid`、`recognition_status`、`association_status` 查询；
- `PATCH /api/image-recognitions/{attachment_id}/association`：通过 `project_report_id` 人工指定项目，传 `null` 可取消关联。

默认 `ENABLE_AUTO_IMAGE_RECOGNITION=false`，避免产生未预期费用。启用后，图片消息仍会先成功入库并立即完成交建通回调，随后使用后台任务和独立数据库会话识别；识别失败不会造成原消息丢失。

远程图片只允许 HTTPS，不跟随重定向，并拒绝解析到内网、回环或链路本地地址；下载有超时和 10MB 默认上限，仅接受 PNG、JPEG、WEBP。真实群图片在识别时只加载到内存，不会永久下载到磁盘。开发页面上传的模拟图片保存在 `JJT_MESSAGE_DATA_DIR/dev-images`，用于离线联调。

汇总 JSON 会把已确认图片放入对应 `projects[].images`。Markdown 在项目施工内容下增加“现场图片与识别补充”，包含图片、施工补充、地点、拍摄时间和 OCR 文字。`needs_review`、`unmatched` 或识别失败的图片不会贴入项目，而是进入 `image_reviews` 和“待人工确认图片”。这些内容只用于展示和人工核对，不改变项目数量、人员、机械或进度统计。

## 按群聊和日期生成 Markdown 汇总预览

汇总范围由 `chatid + report_date` 精确确定。程序查询该范围内的 `project_reports`，只让非重复且 `extraction_status=completed` 的日报进入项目数量、人员和机械统计。`needs_review`、`failed`、`pending` 不进入数值汇总，但会出现在 `warnings`、`review_reports` 和 Markdown 的“待确认和缺失信息”部分。

统计全部由确定性 Python 代码完成，不调用大模型，也不访问外部 API：

- `project_count` 表示实际纳入汇总展示的非重复 completed 项目数，并进一步拆分为字段完整的 `fully_complete_project_count` 和仍有缺失信息的 `partial_project_count`，三者始终满足 `project_count = fully_complete_project_count + partial_project_count`；
- 管理人员和施工人员只累加非空的已知数量；`null` 不会当作 0，如果部分项目缺失则结果明确标为“仅汇总已知数据”，全部缺失时总数为 `null`；
- 机械按 `name + unit` 分组，同名但单位不同的机械不会合并；
- 各项目施工内容、明日计划、安全和质量情况按保存的结构化字段展示；
- 原有 `missing_fields` 以及实际检测到的空字段都会进入缺失信息和警告。
- `normalization_warnings` 会进入汇总告警和待确认列表，但有效日报仍按原有人数、机械规则参与统计。

系统按 `project_name + report_date` 检查重复。只要同一项目同一天关联多条结构化日报，相关记录全部暂不进入数值合计，项目会进入 `duplicate_projects`，返回每条来源的 `msgid` 和 `project_report_id`，汇总状态设置为 `needs_review`，等待人工先处理单条日报。

汇总状态：

- `completed`：来源均为可用的非重复 completed 日报，且没有缺失或待确认警告；
- `needs_review`：存在重复、非 completed 来源、字段缺失或没有有效日报。

预览请求：

```http
POST /api/daily-reports/preview
Content-Type: application/json
```

```json
{
  "chatid": "construction-group-001",
  "report_date": "2026-08-06"
}
```

预览只读取数据并返回统计结果、来源、重复项、警告和 Markdown，不写入 `daily_report_summaries`。人工检查后，可使用同样请求体调用 `POST /api/daily-reports` 保存当前快照。

查询接口：

- `GET /api/daily-reports`：支持 `chatid`、`report_date`、`generation_status`、`limit`、`offset`；
- `GET /api/daily-reports/{summary_id}`：返回保存时的来源快照和 Markdown；
- 相同 `chatid + report_date` 可以重复保存，每次都会生成新的快照，并通过 `daily_report_summary_items` 保存当次来源及顺序，不覆盖历史记录。

Markdown 固定包含标题日期、总体人数、机械汇总、各项目施工情况、已关联现场图片及识别补充、明日计划、安全质量以及待确认和缺失信息。预览和快照保存阶段不会调用 `response_url`；只有下一节的显式人工确认和手动发送接口可以进入离线发送闭环。

## 聊天式本地验收页面

项目内置一个轻量的微信聊天框风格验收页面，不需要安装或启动独立的 React/Vue 项目。先在 `.env` 中确认：

```dotenv
APP_ENV=development
ENABLE_MOCK_API=true
```

然后启动本地服务：

```powershell
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

浏览器访问 <http://127.0.0.1:8000/dev/chat>。默认群聊为 `construction-group-001`，也可以从左侧切换其他预置测试群；页面刷新后会按当前 `chatid` 从消息、识别结果和结构化日报查询接口重新加载数据。

可以依次选择发送人，将下面三条示例作为三条独立消息发送。页面会自动生成 `msgid`、`aibotid`、`chatid`、`chattype` 和消息 JSON，用户无需手写请求体：

```text
桥梁一标2026年8月6日施工日报，天气晴，管理人员3人，施工人员20人，挖掘机2台，今日完成1号桥桩基浇筑80%，明日继续桩基施工，安全正常，质量合格。
```

```text
隧道二标2026年8月6日施工日报，天气多云，管理人员4人，施工人员30人，装载机1台，今日完成隧道掌子面开挖5米，明日继续开挖支护，安全正常，质量合格。
```

```text
路基三标2026年8月6日施工日报，天气晴，管理人员2人，施工人员18人，压路机2台，今日完成K12路基填筑200米，明日进行下一层填筑，安全正常，质量合格。
```

发送后，消息卡片会先展示本地规则初筛状态、分数、命中规则和原因。明显的“日报呢”“日报发了吗”等询问会在本地直接作为普通聊天过滤。对其余“日报候选”或“待大模型复核”消息，页面会自动调用现有 `POST /api/messages/{msgid}/extract-report`，由大模型同时判断 `report`、`related_update`、`ordinary_chat`、`uncertain` 并提取字段。大模型确认的普通聊天不会进入汇总，也不要求人工补齐字段；“提取结构化字段”按钮仍保留用于手动重试。点击“生成汇总预览”时，页面也会先检查当前群聊中尚未结构化的候选消息并补做复核。

在 Mock 聊天输入框中还可以直接发送日报命令，例如：

```text
生成8月10日施工日报
汇总2026年8月10日日报
发送今天施工日报
查看8月10日日报
```

月日命令会选择距离本机当天最近的年份；“今天”“今日”“当天”使用本机当天日期。命令消息本身会被本地规则标记为普通聊天，不会被误提取成项目日报。页面随后自动执行：补提取当前群聊候选消息 → 生成指定日期预览 → 保存汇总快照 → 以当前发送人自动确认 → 创建服务端 Mock 触发消息 → 使用 `MockResponseUrlClient` 模拟发送。完成后可在聊天区和右侧工作台看到 `sent` 状态及发送记录，全程不访问真实外部网络。

以下情况属于硬阻断，自动流程只停留在预览并展示告警，不会保存、确认或发送：没有可汇总的有效日报、同一项目同一天存在重复日报、存在 `pending`、`failed` 或 `needs_review` 等未完成提取记录。只有天气、安全、质量、明日计划等非关键字段缺失，且项目仍是非重复 `completed` 日报时，允许保留告警继续完成本地 Mock 发送；缺失人数仍不会按 0 统计。

Mock 模式启动时还会扫描带有历史大模型原始 JSON 的可恢复结构化记录。若旧记录只是“原文仅写月日”或“单个施工子项内容为空”，服务会仅依据已保存原文和原始 JSON 重新校验并原位修复，不调用大模型、不产生调用费用，也不修改原消息；非法 JSON 和错误字段类型会继续保持 `failed`，等待后续重试或人工检查。

输入框旁的“发送图片”支持 PNG、JPEG、WEBP，最大 10MB。开发页面会调用 `POST /api/dev/mock-image-message` 保存本地图片，再调用视觉识别接口并显示 OCR、施工内容和项目关联状态；已可靠关联的图片会在下一次汇总预览中出现在对应项目下方。

自动提取需要提前配置 `LLM_API_KEY`、`LLM_MODEL` 和 `LLM_BASE_URL`。长日报超过单次等待时间时会按 `LLM_MAX_RETRIES` 自动重试；仍失败时原消息和失败审计都会保留，页面会显示中文错误和手动重试入口。下次点击“生成汇总预览”也会自动重试上次因超时或网络错误失败的日报，不会伪造提取结果。规则初筛的 `needs_review` 只表示特征不足、需要先交给大模型复核；只有大模型提取后仍缺少项目名称、日报日期或施工内容等关键字段时，才进入真正的人工处理流程。

自动结构化提取成功后，页面会把右侧日期切换为大模型识别出的 `report_date` 并自动刷新汇总。也可在右侧手动选择 `2026-08-06` 并点击“生成汇总预览”。页面调用现有 `POST /api/daily-reports/preview` 后，日报机器人会立即在聊天区左侧发送一张完整的汇总日报卡片，展示项目数、人数、机械、告警以及安全渲染的 Markdown，并自动滚动到最新消息。同一群聊同一天重复生成时更新当前本地卡片，避免重复刷屏；右侧工作台继续提供原始 JSON、保存快照和人工确认。

聊天中的机器人汇总卡片标有“本地预览 · 未发送真实群聊”，它只是当前页面会话中的可视化结果，刷新页面后可重新生成；需要持久留存时点击右侧“保存汇总快照”。该行为不会调用 `response_url` 或任何真实消息发送接口。聊天消息区有独立纵向滚动条，顶部提供“查看最早”和“跳到最新”，也可使用鼠标滚轮、触控板或 `Ctrl+Home`/`Ctrl+End` 浏览完整的已加载上下文。

人工确认按以下步骤进行：

1. 检查预览中的 `warnings`、重复项目和缺失字段；
2. 点击“保存汇总快照”，保存成功后页面显示 `summary_id`；
3. 在出现的“人工确认与模拟发送”区域填写确认人 ID，可选填核对备注；
4. 点击“我已核对，确认汇总”，页面调用 `POST /api/daily-reports/{summary_id}/confirm`；
5. 确认成功后显示确认人、确认时间和备注，同时出现“模拟发送”和“查看发送记录”；
6. 点击“模拟发送”后，页面先调用仅开发环境存在的 `POST /api/dev/mock-trigger-message`，再调用 `POST /api/daily-reports/{summary_id}/send`；
7. 发送成功后，聊天区会收到一条“模拟发送成功”的机器人消息，并明确标注“仅本地 Mock，未发送真实群聊”；
8. 点击“查看发送记录”可读取 `GET /api/daily-reports/{summary_id}/send-attempts`，查看时间、传输方式、状态和错误信息。响应地址只显示“已脱敏”。

需要重新检查时可在发送前点击“取消人工确认”，调用 `/unconfirm` 将快照退回待确认状态。已经发送成功的快照不能重复发送；发送失败的快照需要重新人工确认后才会再次显示模拟发送按钮。

即使 `generation_status=needs_review`，后端也允许在人工逐项核对后确认，确认责任由填写的确认人和备注记录。人工确认只改变已保存快照的本地 `publication_status`，不会修改原始消息、伪造缺失数据或发送真实群消息。

底部“开发调试”面板默认折叠，可查看最近请求的接口、HTTP 状态码及脱敏后的请求/响应 JSON。页面不接受 `response_url`；模拟触发接口只接收 `chatid` 和 `summary_id`，Mock 地址由服务端随机生成且不会返回页面。默认 Mock 传输不访问外部 URL，页面不保存浏览器密钥，Markdown 不执行原始 HTML。只有同时满足 `APP_ENV=development` 和 `ENABLE_MOCK_API=true` 时才注册页面、静态资源和模拟触发接口；production 环境访问这些地址均返回 404。

该页面只用于本地业务验收，是对现有 API 的可视化调用入口，不是真实交建通界面，也不具备真实消息发送能力。

## 汇总日报人工确认与 response_url 模拟发送

保存后的汇总快照拥有两套互不混用的状态：

- `generation_status` 描述汇总数据质量，取值为 `completed` 或 `needs_review`。它不会因为确认或发送而改变；`needs_review` 快照也允许人工核对后确认。
- `publication_status` 描述人工确认和发送进度，取值为 `draft`、`confirmed`、`sending`、`sent`、`send_failed`。新快照和旧库迁移后的历史快照均默认为 `draft`。

发布状态流转如下：

```text
draft ──人工确认──> confirmed ──发送抢占──> sending ──成功──> sent
  ^                     |                         └─失败──> send_failed
  └────取消确认─────────┘                                      |
                         <────────再次人工确认──────────────────┘
```

人工确认使用：

```http
POST /api/daily-reports/{summary_id}/confirm
Content-Type: application/json
```

```json
{
  "confirmed_by": "admin-user-001",
  "confirmation_note": "已人工核对"
}
```

`draft` 和 `send_failed` 可以确认，确认时保存确认人、确认时间和备注。`POST /api/daily-reports/{summary_id}/unconfirm` 可将 `confirmed` 退回 `draft`，同时清除本次确认信息；`sending` 或 `sent` 返回 HTTP 409，不允许退回。已发送快照也不能重复确认。

默认配置为完全离线模拟发送：

```dotenv
ENABLE_REAL_RESPONSE_SEND=false
RESPONSE_SEND_TIMEOUT_SECONDS=10
```

此时应用使用 `MockResponseUrlClient`，支持 `mock://` 地址，只改变本地状态并保存审计记录，不会进行 DNS 解析或 HTTP 请求。模拟触发消息可通过 Swagger 的 `POST /api/dev/mock-message` 提交：

```json
{
  "msgid": "generate-report-command-001",
  "aibotid": "bot-001",
  "chatid": "construction-group-001",
  "chattype": "group",
  "from": {
    "userid": "admin-user-001"
  },
  "response_url": "mock://response-url/generate-report-command-001",
  "msgtype": "text",
  "text": {
    "content": "@机器人 生成2026年8月6日施工日报"
  }
}
```

确认汇总后，使用数据库中这条触发消息的 `msgid` 发送：

```http
POST /api/daily-reports/{summary_id}/send
Content-Type: application/json
```

```json
{
  "trigger_msgid": "generate-report-command-001"
}
```

接口不接受 URL 参数。服务只能读取已入库消息的 `response_url`，并校验消息与汇总的 `chatid` 一致；发送前通过数据库条件更新原子抢占 `sending`，避免已发送、正在发送或并发请求重复进入传输。当前直接使用快照的 `markdown_content`，不调用大模型润色或决定发送对象。超过一小时的触发消息会返回可能过期警告，但 Mock 模式仍可用于离线验证。

每次实际进入发送流程都会写入 `daily_report_send_attempts`。可通过以下方式查看：

- `GET /api/daily-reports/{summary_id}`：详情中的 `send_attempts`；
- `GET /api/daily-reports/{summary_id}/send-attempts`：单独查询全部发送尝试；
- `GET /api/daily-reports`：可额外按 `publication_status` 筛选快照。

发送记录只保存 SHA-256 `response_url_hash`，不会保存完整 `response_url`；日志也不会输出完整 URL、API Key 或 Markdown 正文。失败只会把发布状态置为 `send_failed` 并记录受控错误，不会删除或改写汇总 Markdown、来源关联、结构化日报和原始消息。

虽然提供了 `ENABLE_REAL_RESPONSE_SEND` 安全开关，但本地交建通材料没有给出可核验的 `response_url` 请求体协议。为遵守“不猜测协议字段”的要求，真实模式当前会在网络请求前以 `response_protocol_not_confirmed` 失败并留存审计记录；待交建通回调联调确认请求体、目标域名和返回码语义后才能启用真实 HTTP 传输。后续真实实现必须只允许 HTTPS、设置超时且禁止跨域重定向。

`response_url` 通常具有一次性和时效性，只应用于紧邻当前回调的发送，不应把历史消息中的地址当作长期凭据反复使用。当前只有 `/dev/chat` 会识别“生成日报”等本地验收命令并使用服务端生成的 `mock://` 地址自动完成离线闭环；真实交建通回调不会自动识别命令，也不会自动或定时发送。

## 企业微信自建应用验证

在企业微信管理后台创建自建应用并配置“接收消息”时：

1. URL 填写 `https://你的公网HTTPS域名/api/jjt/callback`；
2. 将后台的 Token 填入 `.env` 的 `JJT_CALLBACK_TOKEN`；
3. 将后台的 EncodingAESKey 填入 `JJT_ENCODING_AES_KEY`；
4. 将企业 CorpID 填入 `JJT_RECEIVE_ID`；
5. 重启服务后再点击后台的“保存”。

同时必须设置 `ENABLE_JJT_CALLBACK=true`；否则真实回调路由不会注册并返回 404。

企业微信发送 POST 时，接口接收如下加密信封：

```xml
<xml>
  <ToUserName><![CDATA[企业CorpID]]></ToUserName>
  <Encrypt><![CDATA[加密消息]]></Encrypt>
  <AgentID>自建应用AgentID</AgentID>
</xml>
```

服务使用 `<Encrypt>` 参与签名验证并解密内部 XML。成功保存后返回裸文本 `success`，不附加换行，避免企业微信重试。XML 解析使用 `defusedxml`，会拒绝 DTD、外部实体和实体扩展。

当前进程只使用一套 Token、EncodingAESKey 和 receiveid。如果交建通与企业微信使用不同凭据，需要修改 `.env` 并重启以切换测试环境；当前版本不支持两套凭据同时在线。

## 使用 Cloudflare Quick Tunnel 完成真实验证

下面的流程不需要公司服务器，也不需要开放路由器端口。Cloudflare 会给当前电脑上的 `127.0.0.1:8000` 临时分配一个公网 HTTPS 地址。Quick Tunnel 地址每次重启都可能变化，只适合联调。

### 第一步：同步依赖并配置 CorpID

在项目目录打开 PowerShell：

```powershell
Set-Location F:\desk\zhongjiao\chat_robot\jjt-daily-report-bot
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

在企业微信管理后台进入“我的企业 → 企业信息”，复制企业 ID（CorpID），将 `.env` 配置为：

```dotenv
JJT_CALLBACK_TOKEN=你本地已经准备好的Token
JJT_ENCODING_AES_KEY=你本地已经准备好的43位EncodingAESKey
JJT_RECEIVE_ID=企业微信后台的CorpID
JJT_MESSAGE_DATA_DIR=./data/messages
JJT_LOG_LEVEL=INFO
JJT_TIMEZONE=Asia/Shanghai
APP_ENV=development
ENABLE_MOCK_API=true
ENABLE_JJT_CALLBACK=true
DATABASE_URL=sqlite:///./data/jjt_bot.db
```

Token 和 EncodingAESKey 可以继续使用本地已有值，但稍后在企业微信后台必须逐字填写相同值。不要在企业微信后台重新随机生成 AESKey，除非也同步更新 `.env`。不要把 `.env` 内容发到聊天、截图或提交到 Git。

### 第二步：启动服务

在第一个 PowerShell 窗口执行：

```powershell
.\run.ps1
```

保持窗口运行。另开一个 PowerShell 检查：

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
```

应看到 `status` 为 `ok`。

### 第三步：先做本地加密回调自检

在第二个 PowerShell 窗口执行：

```powershell
.\verify-wecom.ps1
```

脚本不会显示 Token 或 AESKey，会依次验证：

1. `.env` 格式及 CorpID；
2. `/health`；
3. 加密 `echostr` 的 GET URL 验证；
4. 企业微信加密 XML POST 和裸文本 `success` 应答；
5. 自检消息是否写入当天的 JSONL。

全部完成时最后一行是“企业微信兼容回调端到端自检全部通过”。自检会写入一条 `local-wecom-check-...` 消息，这是预期行为。

### 第四步：启动 Cloudflare Tunnel

如果 `cloudflared.exe` 已加入 PATH，在第三个 PowerShell 窗口执行：

```powershell
cloudflared tunnel --url http://127.0.0.1:8000
```

如果没有加入 PATH，使用你下载文件的完整路径，例如：

```powershell
& "C:\你的下载目录\cloudflared.exe" tunnel --url http://127.0.0.1:8000
```

等待输出类似下面的临时地址：

```text
https://随机名称.trycloudflare.com
```

保持 tunnel 窗口运行，不要关闭。无需在 Windows 防火墙开放 8000 入站端口，Cloudflare Tunnel 使用出站连接。

### 第五步：验证公网链路

把下面地址替换成实际的 TryCloudflare 地址：

```powershell
Invoke-RestMethod https://随机名称.trycloudflare.com/health
.\verify-wecom.ps1 -BaseUrl "https://随机名称.trycloudflare.com"
```

第二条命令会把模拟企业微信加密回调真正经过公网 Tunnel 再送回本地服务，并核对本地 JSONL。两条命令都成功后，公网链路和回调代码均已准备好。

### 第六步：在企业微信后台保存回调

进入“应用管理 → 自建应用 → 你的应用 → 接收消息/API 接收消息”，填写：

- URL：`https://随机名称.trycloudflare.com/api/jjt/callback`
- Token：与 `.env` 的 `JJT_CALLBACK_TOKEN` 完全相同
- EncodingAESKey：与 `.env` 的 `JJT_ENCODING_AES_KEY` 完全相同

点击保存时，企业微信会立即发送 GET 验证。成功时本地服务日志会出现 `callback URL verified`。URL 末尾必须是 `/api/jjt/callback`，不能填写 `/health` 或 `/docs`。

### 第七步：发送真实企业微信消息

确保自建应用的可见范围包含你的企业微信账号，然后在企业微信客户端进入该应用，从支持向应用发送消息的入口发送一条文本。服务日志应记录 `source=wecom_xml`，并返回 `success`。查看最新数据：

```powershell
$Today = Get-Date -Format "yyyy-MM-dd"
Get-Content ".\data\messages\$Today.jsonl" -Tail 5
```

记录中的 `message.source` 应为 `wecom_xml`，`message.text.content` 是发送的文字，`message.raw_xml` 是完整解密 XML。

### 第八步：结束验证

分别在 Uvicorn 和 Cloudflare Tunnel 窗口按 `Ctrl+C`。Quick Tunnel 停止后临时网址即失效；下次启动得到新网址时，需要回企业微信后台更新 URL。

若 `trycloudflare.com` 能从浏览器访问但企业微信保存仍持续超时，可能是企业微信回调服务器到该临时域名的网络可达性问题，而不是本项目验签错误。此时可改用绑定自有域名的 Cloudflare Named Tunnel，或选择中国大陆可稳定访问的 HTTPS 隧道服务。

## 数据文件

消息保存到：

```text
data/messages/YYYY-MM-DD.jsonl
```

文件采用 UTF-8，每条记录独占一行，中文不转义。交建通的完整解密业务 JSON 保存在 `message` 字段。企业微信 XML 会保存为统一结构，其中包括：

- `source: "wecom_xml"`；
- 从 `MsgId`、`FromUserName`、`MsgType`、`AgentID` 映射出的通用字段；
- 文本消息的 `text.content`；
- `xml`：完整 XML 字段的递归字典，重复节点会保留为数组；
- `raw_xml`：完整解密明文 XML。

JSONL 审计文件仍使用进程内有限 LRU 去重，进程重启后这部分状态不会保留；没有 `MsgId` 的企业微信事件仍会写入 JSONL。交建通 JSON 和模拟消息另有下述 SQLite 数据库级唯一约束。

结构化交建通/模拟消息还会保存到：

```text
data/jjt_bot.db
```

SQLite 以 `messages` 和 `message_attachments` 保存消息及附件，并以独立表保存规则识别、单条结构化日报、图片识别与项目关联、汇总快照、来源关联和发送尝试。`messages.msgid` 有数据库唯一约束，可在多请求并发时防止重复；附件与所属消息在同一事务内写入。文件附件仍只保存 URL、类型、下载状态和 MD5 元数据；施工图片仅在显式或已启用的后台识别流程中按安全限制读取。数据库文件、WAL 和 journal 文件均已加入 `.gitignore`。

## 测试

```powershell
pytest -q
```

服务运行后执行企业微信端到端自检：

```powershell
.\verify-wecom.ps1
```

测试只使用本地生成的协议兼容密文，不访问真实交建通服务。

## 常见错误排查

- **Token 不一致**：确认 `.env` 的 `JJT_CALLBACK_TOKEN` 与交建通后台完全一致。
- **EncodingAESKey 不是 43 位**：不要填写 AES 原始字节、引号或多余空格。
- **误用了大模型 API Key**：这里必须填写交建通的 EncodingAESKey，不是任何模型密钥。
- **长日报大模型调用超时**：保持 `LLM_TIMEOUT_SECONDS=90` 和 `LLM_MAX_RETRIES=1`，重启服务后在消息卡片点击“提取结构化字段”，或再次点击“生成汇总预览”。如果仍连续超时，再检查模型服务负载和网络，不建议无限增加重试次数。
- **公网无法访问**：交建通必须能通过公网 HTTPS 访问回调地址，并由防火墙或网关放行。
- **返回值被 JSON 序列化**：GET 验证成功必须返回裸明文，不能变成 `"明文"`。
- **`msg_signature` 校验失败**：检查 Token、时间戳、nonce、密文是否原样传递，且网关没有改写查询参数。
- **AES 解密失败**：检查 EncodingAESKey 是否匹配，密文 URL Decode 是否正常，以及 Base64 内容是否被截断。
- **企业微信 receiveid 不匹配**：确认 `JJT_RECEIVE_ID` 填写的是企业 CorpID，而不是 AgentID、Secret 或应用名称。
- **企业微信 XML 返回 400**：确认网关没有改写 XML，请求体包含 `<Encrypt>`，解密后的消息为 UTF-8 XML。

## 协议假设

本项目根据交建通及企业微信兼容协议采用 WXBizMsgCrypt 规则：签名为 Token、timestamp、nonce、encrypt 字典序拼接后的 SHA1；加密为 AES-256-CBC，IV 是 AES Key 前 16 字节，PKCS#7 block size 为 32；明文结构为 16 字节随机数、4 字节大端消息长度、消息和 receiveid。当前目录未提供官方 Python 加解密源码，因此实现了可替换的 `JJTCryptoService` 适配层。交建通智能机器人 `receiveid` 为空时不做 CorpID 校验；配置企业微信 CorpID 后会进行常量时间比对。
