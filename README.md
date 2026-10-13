# AI Character Chat

一个面向自主学习与研究的开源角色聊天项目。长期目标是研究角色一致性、世界观知识与记忆，再逐步整合成自己的系统。

**当前版本：0.2.0，带网页入口的离线原型。** 可以选择角色、看到开场白、发送消息、查看回复和清空本页对话。默认回复为明确标注的离线输入回显，不调用模型、不生成角色人格。后端仍是单轮接口；网页中的记录不会发送给模型，刷新后清空。无需密钥或付费服务。

## 快速开始：Windows PowerShell

需要Python3.13或以上、Git。先克隆并进入仓库：

~~~powershell
git clone https://github.com/zwsss152/ai-character-chat.git
Set-Location -LiteralPath ai-character-chat
py -3.13 -m venv .venv
./.venv/Scripts/python.exe -m pip install -e ".[dev]"
./.venv/Scripts/python.exe -m pytest
./.venv/Scripts/python.exe -m uvicorn ai_character_chat.app:app --host 127.0.0.1 --port 8000
~~~

已在本地目录使用时，从创建虚拟环境那一步开始。命令逐行运行。直接调用虚拟环境里的解释器，无需更改PowerShell执行策略。VS Code选择 .venv/Scripts/python.exe。

打开 http://127.0.0.1:8000/ 使用网页：选择露米，输入消息并点击“发送”。“清空对话”恢复该角色的开场白；开场白来自配置，并非模型生成。

也可以打开 http://127.0.0.1:8000/docs，在POST /api/chat中点“Try it out”，输入：

~~~json
{"character_id": "lumi", "message": "你好"}
~~~

预期回复：

~~~json
{
  "character_id": "lumi",
  "reply": "[离线模拟｜露米] 收到：你好",
  "mode": "mock"
}
~~~

也可以在第二个终端调用：

~~~powershell
$body = @{ character_id = 'lumi'; message = '你好' } | ConvertTo-Json
Invoke-RestMethod -Uri 'http://127.0.0.1:8000/api/chat' -Method Post -ContentType 'application/json; charset=utf-8' -Body ([System.Text.Encoding]::UTF8.GetBytes($body))
~~~

用Ctrl+C停止服务器。macOS/Linux使用 python3 -m venv .venv，随后把上面的解释器路径改为 ./.venv/bin/python。

## 接口

| 接口 | 用途 |
|---|---|
| GET / | 网页聊天入口 |
| GET /health | 查看服务状态与回复模式 |
| GET /api/characters | 查看角色配置 |
| POST /api/chat | 发送角色标识和单轮文字，返回回复与模式 |

字段只接收字符串。输入两端空白会去掉，消息长度为1—2000字符。未知角色返回404，无效请求返回422。当前返回角色卡供本地学习观察；以后正式产品的角色信息暴露范围需要另行设计。

## 第一次读代码

1. [角色JSON](src/ai_character_chat/data/characters/lumi.json)：先区分资料与程序。
2. [配置读取](src/ai_character_chat/characters.py)：JSON怎样成为Python对象。
3. [模型适配](src/ai_character_chat/providers.py)：离线回复在哪里产生。
4. [对话流程](src/ai_character_chat/chat.py)：怎样找角色、调用适配器并返回结果。
5. [HTTP入口](src/ai_character_chat/app.py)：怎样把请求交给对话流程。
6. [接口测试](tests/test_api.py)：哪些行为已得到验证。

先画一次请求流，修改模拟前缀，运行测试并说明为什么某个测试需要改变。读懂一小段后再继续，不必一次理解全部类型标注和框架细节。

## 文档与后续学习

- [架构与边界](docs/architecture.md)
- [学习与研究路线](docs/roadmap.md)
- [实验记录模板](docs/experiment-template.md)
- [骨架与个人贡献记录](docs/contributions.md)
- [验证记录](docs/verification.md)
- [实验目录说明](experiments/README.md)
- [每日开发任务](docs/project-backlog.md)：当前两周任务与后续几个月的迭代方向。

后续真实模型、上下文、记忆、RAG、工具调用、评测和网页客户端按学习成果推进。新增模块前先明确问题、前置知识、简单基线和验收证据。

## 运行与开源范围

目前供本机使用，README绑定127.0.0.1。已有基础网页聊天界面；身份认证、持久化、流式输出、线上部署和训练尚未实现。公开的是源码仓库，GitHub发布不等于部署了在线聊天服务。

本骨架由AI助手按维护者授权搭建；维护者的后续学习、实现与研究贡献另行记录，不把文件存在当成本人已经掌握。

代码与原创示例角色采用[MIT许可证](LICENSE)。不含商业游戏角色或素材。
