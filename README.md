# AI Character Chat

面向学习与研究的开源角色对话项目。长期目标是研究游戏中 AI 角色的身份、性格、知识与记忆；当前使用原创角色露米和本地网页验证基础流程。

**0.3.0 是角色对话基础版本。** 角色规格参与模型输入，SQLite 保存会话及成功问答，网页可以恢复、删除会话，开发界面可检查实际模型上下文。默认仍是明确标注的离线回显，**没有模型生成能力**。可配置兼容聊天消息接口的 HTTP 服务；工程测试不证明模型会保持人设。

## 快速开始：Windows PowerShell

需要 Python 3.13 或以上、Git。命令逐行运行：

~~~powershell
git clone https://github.com/zwsss152/ai-character-chat.git
Set-Location -LiteralPath ai-character-chat
py -3.13 -m venv .venv
./.venv/Scripts/python.exe -m pip install -e ".[dev]"
./.venv/Scripts/python.exe -m pytest
./.venv/Scripts/python.exe -m uvicorn ai_character_chat.app:app --host 127.0.0.1 --port 8000
~~~

已在仓库目录时从创建虚拟环境开始。直接使用虚拟环境解释器，无需更改 PowerShell 执行策略。macOS/Linux 用 `python3 -m venv .venv`，随后改用 `./.venv/bin/python`。

打开 [本地网页](http://127.0.0.1:8000/)：选择露米、新建会话、发送消息。刷新后恢复上次选中的会话；也可从列表恢复或删除会话。删除会同时移除消息与调用追踪。开场白来自角色配置。用 Ctrl+C 停止服务。

默认数据库为当前工作目录下 `local_data/chat.sqlite3`，已被 Git 忽略。从同一目录重新启动可以续聊；换目录运行时建议配置数据库绝对路径。

## 配置真实模型服务

不配置时使用 mock，无需密钥且不产生模型费用。环境变量启动时读取，修改后重启：

| 环境变量 | 默认值与用途 |
|---|---|
| `AI_CHAT_PROVIDER` | `mock`；可选择 `http` |
| `AI_CHAT_BASE_URL` | HTTP 模式必填，兼容服务根地址，例如 `https://your-service.example/v1` |
| `AI_CHAT_MODEL` | HTTP 模式必填，服务支持的模型标识 |
| `AI_CHAT_API_KEY` | HTTP 模式必填，仅在本地环境提供 |
| `AI_CHAT_DATABASE_PATH` | `local_data/chat.sqlite3` |
| `AI_CHAT_MAX_HISTORY_TURNS` | `10`，模型输入最多保留的历史完整问答轮次 |
| `AI_CHAT_MAX_CONTEXT_CHARACTERS` | `16000`，上下文字符预算；不是 token 数 |
| `AI_CHAT_REQUEST_TIMEOUT_SECONDS` | `20`，上游 HTTP 超时秒数 |

~~~powershell
$env:AI_CHAT_PROVIDER = 'http'
$env:AI_CHAT_BASE_URL = 'https://your-service.example/v1'
$env:AI_CHAT_MODEL = 'your-model-id'
$env:AI_CHAT_API_KEY = 'replace-locally'
./.venv/Scripts/python.exe -m uvicorn ai_character_chat.app:app --host 127.0.0.1 --port 8000
~~~

示例地址不能直接使用。按服务自己的说明填写并确认计费。适配器发送 `POST <BASE_URL>/chat/completions` 和有序 `messages`，不自动重试。HTTP 模式缺少配置会明确报错，不会静默改为 mock。密钥不得写入角色卡、代码、追踪或提交记录。

## 接口

可在 [本地接口文档](http://127.0.0.1:8000/docs)中试用：

| 接口 | 用途 |
|---|---|
| `GET /health` | 查看服务状态与回复模式 |
| `GET /api/characters` | 查看包内角色规格 |
| `POST /api/sessions` | `{"character_id":"lumi"}` 创建会话 |
| `GET /api/sessions` | 列出会话，可用 `?character_id=lumi` 筛选 |
| `GET /api/sessions/{id}` | 恢复会话与消息 |
| `DELETE /api/sessions/{id}` | 删除会话、消息及追踪，返回 204 |
| `POST /api/sessions/{id}/messages` | 提交 `message` 和 UUID 字符串 `request_id` |
| `GET /api/sessions/{id}/traces/{request_id}` | 查看实际模型输入、裁剪和结果信息 |
| `POST /api/chat` | 保留单轮接口，不保存会话 |

一轮会话示例：

~~~powershell
$sessionBody = @{ character_id = 'lumi' } | ConvertTo-Json
$session = Invoke-RestMethod -Uri 'http://127.0.0.1:8000/api/sessions' -Method Post -ContentType 'application/json; charset=utf-8' -Body ([System.Text.Encoding]::UTF8.GetBytes($sessionBody))
$requestId = [guid]::NewGuid().ToString()
$messageBody = @{ message = '你好'; request_id = $requestId } | ConvertTo-Json
Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/sessions/$($session.id)/messages" -Method Post -ContentType 'application/json; charset=utf-8' -Body ([System.Text.Encoding]::UTF8.GetBytes($messageBody))
Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/sessions/$($session.id)/traces/$requestId"
~~~

消息为字符串，去除两端空白后长度为 1—2000 字符。成功请求以请求 ID 去重：同一会话同 ID、同文字重试返回原结果；同 ID、不同文字返回 409。生成期间向同一会话再次发送或删除返回 409，其他会话仍可使用。失败不留下半轮消息，但保存错误追踪。上游超时不自动重试，手动重试仍可能产生费用。

旧接口返回 `character_id`、`reply`、`mode` 三字段；mock 输入 `{"character_id":"lumi","message":"你好"}` 返回 `[离线模拟｜露米] 收到：你好`。旧接口先前消息不会进入下一次调用。

## 从哪里开始学习

1. 看 [架构与边界](docs/architecture.md)，追踪一次 HTTP 到模型调用。
2. 看 [完整追踪示例](docs/request-trace-example.md)，区分数据库历史与模型上下文。
3. 运行测试，修改模拟回复并解释测试变化。
4. 按 [学习路线](docs/roadmap.md)记录本人尝试。

其他文档：[开发清单](docs/project-backlog.md)、[贡献记录](docs/contributions.md)、[验证记录](docs/verification.md)、[实验模板](docs/experiment-template.md)、[实验目录](experiments/README.md)。

## 当前边界

采用单进程、本机运行方式，绑定 `127.0.0.1`；不包含认证、公开部署、流式输出、长期记忆、检索、游戏引擎或训练。数据库保存聊天原文，追踪保存模型输入，仅供本地开发。并发保护是进程内的，不能用多个 worker 保证此行为。

会话保存创建时的角色快照；修改包内角色卡只影响新会话。历史裁剪只改变模型输入，数据库原文仍在。字符预算不能保证任意模型的 token 限制。

基础设施由 AI 助手按维护者授权实现，不据文件存在推断本人已掌握。工程功能与真实模型效果分别验证。代码和原创角色采用 [MIT 许可证](LICENSE)，不含商业角色或素材。

