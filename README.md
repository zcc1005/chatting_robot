# 交建通施工日报机器人

这是“交建通施工日报机器人”的第一阶段后端。除交建通机器人 JSON 回调外，也兼容企业微信自建应用的 XML 回调，可先用企业微信验证 Token、EncodingAESKey、URL 验证、消息验签、解密和本地保存链路。

当前已实现：

- 回调 URL 验证；
- POST 消息签名校验和 AES-256-CBC 解密；
- 同一 POST 地址自动识别交建通 JSON 与企业微信 `text/xml`/`application/xml`；
- 企业微信 XML 安全解析、通用字段标准化和 `success` 应答；
- 解密业务 JSON 的安全日志（`response_url`、token 类字段会脱敏）；
- 按上海时区写入每日 JSONL 文件；
- 最多保留 10,000 个 `msgid` 的进程内 LRU 去重；
- 将 text、image、mixed、file 明文转换为统一消息模型；
- 使用 SQLite 保存消息与附件元数据，并通过 `msgid` 唯一约束去重；
- 提供开发环境明文模拟接口、消息列表和详情查询接口；
- 健康检查和本地自动化测试。

当前未实现：大模型接入、施工日报抽取或生成、图片或文件下载、OCR、自动汇总、回调主动回复和前端页面。

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

SQLite 默认位于 `data/jjt_bot.db`。模拟接口和真实交建通 JSON 回调都调用同一个 `MessageService.process_plain_message`；真实回调额外执行验签、AES 解密和 JSONL 原始审计备份。当前阶段不会调用大模型、生成施工日报、下载附件或回复交建通群聊。

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

SQLite 包含 `messages` 和 `message_attachments` 两张表。`messages.msgid` 有数据库唯一约束，可在多请求并发时防止重复；附件与所属消息在同一事务内写入。当前只保存图片、文件的 URL、类型、下载状态和 MD5 等元数据，不会访问 URL 或下载文件。数据库文件、WAL 和 journal 文件均已加入 `.gitignore`。

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
- **公网无法访问**：交建通必须能通过公网 HTTPS 访问回调地址，并由防火墙或网关放行。
- **返回值被 JSON 序列化**：GET 验证成功必须返回裸明文，不能变成 `"明文"`。
- **`msg_signature` 校验失败**：检查 Token、时间戳、nonce、密文是否原样传递，且网关没有改写查询参数。
- **AES 解密失败**：检查 EncodingAESKey 是否匹配，密文 URL Decode 是否正常，以及 Base64 内容是否被截断。
- **企业微信 receiveid 不匹配**：确认 `JJT_RECEIVE_ID` 填写的是企业 CorpID，而不是 AgentID、Secret 或应用名称。
- **企业微信 XML 返回 400**：确认网关没有改写 XML，请求体包含 `<Encrypt>`，解密后的消息为 UTF-8 XML。

## 协议假设

本项目根据交建通及企业微信兼容协议采用 WXBizMsgCrypt 规则：签名为 Token、timestamp、nonce、encrypt 字典序拼接后的 SHA1；加密为 AES-256-CBC，IV 是 AES Key 前 16 字节，PKCS#7 block size 为 32；明文结构为 16 字节随机数、4 字节大端消息长度、消息和 receiveid。当前目录未提供官方 Python 加解密源码，因此实现了可替换的 `JJTCryptoService` 适配层。交建通智能机器人 `receiveid` 为空时不做 CorpID 校验；配置企业微信 CorpID 后会进行常量时间比对。
