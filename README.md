# Dify-like

一个基于 AgentScope 思路构建的 Dify-like 智能体应用平台 Demo。

## MVP 目标

- 应用管理
- Agent 配置与运行
- 工具调用
- 知识库检索
- 聊天调试
- 运行日志
- API 调用

## 项目结构

- `backend/`: FastAPI 后端与 Agent runtime
- `frontend/`: React 控制台
- `docs/`: 架构与启动文档
- `scripts/`: 后续放置初始化脚本

## MVP 链路

当前 demo 使用配置驱动的默认链路：

```text
User Input -> Retrieval -> ReAct-like Agent Runner + Tools -> Stream Output -> Logs
```

这不是最终写死流程，而是通过 `workflow_spec` 存在应用配置中。后续可以扩展成模板选择、JSON/YAML workflow，或者由 Planner Agent 自动生成 workflow。

## Windows 开发前置

1. 安装 Docker Desktop
2. 安装 Python 3.10+
3. 安装 Node.js 18+

## 启动数据库

在项目根目录执行：

```powershell
cd F:\my_folder\Work\LLM\Project\Dify-like
docker compose up -d
```

如果你本机没有 Docker，先安装 Docker Desktop，再执行上面的命令。

## 本地配置

复制 `.env.example` 为 `.env`：

```powershell
Copy-Item .env.example .env
```

默认数据库连接串：

```text
postgresql+psycopg://postgres:postgres@localhost:5432/dify_like
```

## 启动后端

```powershell
cd F:\my_folder\Work\LLM\Project\Dify-like\backend
conda activate dify
python -m pip install -e .
python -m uvicorn app.main:app --reload --port 8000
```

后端地址：

- Health: `http://localhost:8000/health`
- API Docs: `http://localhost:8000/docs`

## 启动前端

```powershell
cd F:\my_folder\Work\LLM\Project\Dify-like\frontend
npm install
npm run dev
```

前端地址：

```text
http://localhost:5173
```

## 文档

- [Setup Guide](docs/setup.md)
- [Architecture](docs/architecture.md)
