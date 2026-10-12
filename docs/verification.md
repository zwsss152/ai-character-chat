# 骨架验证

此文件记录实施时的实际检查；学习者本人的能力验收另行进行。

- 验证日期：2026-10-12；Windows，Python 3.13.9。
- 环境与依赖：在独立 `.venv` 中安装 `.[dev]` 成功；FastAPI 0.143.0、Pydantic 2.14.1、Uvicorn 0.54.0、pytest 9.1.1。
- 离线接口测试：`python -m pytest`，15项通过；无需模型API或密钥。
- README启动与HTTP调用：Uvicorn成功启动；通过实际HTTP请求核对 `/health`、`/api/chat` 与 `/docs`，验证后停止服务。检查使用本机临时端口8765。
- Windows命令：`py -3.13`可用；README中的PowerShell UTF-8聊天请求返回预期中文模拟回复，使用临时端口8766验证后停止服务。
- 安装包角色资料：构建并安装wheel，在不加载源码可编辑安装的隔离Python进程中成功读取露米的JSON配置。
- 源码与文档：Python语法解析通过，本地Markdown文件链接检查通过。
- GitHub Actions：待发布后确认。

本地测试出现Starlette针对httpx测试客户端的弃用提示；当前测试全部通过，后续更新测试客户端时需复核兼容性。沙箱内pytest缓存写入曾受限，不影响接口结果。

边界：不涉及真实模型、角色生成质量、RAG、记忆、训练或线上服务压测。
