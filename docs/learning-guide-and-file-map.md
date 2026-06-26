# Learning Guide and File Map

这份文档用于回答两个问题：

1. 学这个项目应该从哪里开始看？
2. 每个主要文件的作用是什么？

建议阅读顺序：

```text
main.py
  -> api/routes/chat.py
    -> services/chat_service.py
      -> runtime/workflow_executor.py
        -> runtime/agent_adapters.py
          -> tools/registry.py
          -> services/rag_service.py
          -> services/run_log_service.py
```

## 1. 后端主入口

### backend/app/main.py

后端程序入口。

作用：

- 创建 FastAPI app
- 配置 CORS
- 注册 API 路由
- 启动时初始化数据库表
- 提供 `/health` 健康检查

你启动后端时执行的是：

```powershell
python -m uvicorn app.main:app --reload --port 8000
```

这里的 `app.main:app` 指的就是这个文件里的 FastAPI 实例。

## 2. API 路由层

API 路由层负责接收 HTTP 请求，不应该写太多业务逻辑。

### backend/app/api/routes/apps.py

应用管理接口。

负责：

- 创建 App
- 查询 App 列表
- 查询单个 App
- 更新 App
- 删除 App

核心路径：

```text
POST   /api/apps
GET    /api/apps
GET    /api/apps/{app_id}
PATCH  /api/apps/{app_id}
DELETE /api/apps/{app_id}
```

### backend/app/api/routes/chat.py

聊天接口入口。

负责：

- 接收用户 query
- 判断是否流式返回
- 调用 chat_service
- 返回 JSON 或 SSE stream

核心路径：

```text
POST /api/apps/{app_id}/chat
GET  /api/conversations/{conversation_id}/messages
```

这是你学习聊天主链路时最重要的 API 文件。

### backend/app/api/routes/tools.py

工具接口。

负责：

- 返回内置工具列表
- 查询某个 App 启用的工具
- 更新某个 App 启用的工具

核心路径：

```text
GET /api/tools
GET /api/apps/{app_id}/tools
PUT /api/apps/{app_id}/tools
```

### backend/app/api/routes/knowledge.py

知识库文档接口。

负责：

- 上传 `.txt` / `.md`
- 查询 App 下的文档列表

核心路径：

```text
POST /api/apps/{app_id}/documents
GET  /api/apps/{app_id}/documents
```

### backend/app/api/routes/runs.py

运行日志接口。

负责：

- 查询某个 App 的 run 列表
- 查询某个 run 的 step 明细

核心路径：

```text
GET /api/apps/{app_id}/runs
GET /api/runs/{run_id}/steps
```

## 3. Service 层

Service 层负责业务编排。它连接 API、数据库、runtime。

### backend/app/services/chat_service.py

聊天主链路服务。

现在它的职责是：

- 创建或获取 conversation
- 保存 user message
- 创建 run
- 获取 App 启用的工具
- 调用 WorkflowExecutor
- 保存 assistant message
- 结束 run
- 处理 SSE event

它现在不再直接写死：

- 先检索
- 再 agent
- 再工具

这些已经交给 `WorkflowExecutor`。

这是理解当前系统主流程的第二个关键文件。

### backend/app/services/app_service.py

应用管理业务逻辑。

负责：

- 创建 App
- 默认启用部分工具
- 查询 App
- 更新 App
- 删除 App
- 设置 App 工具
- 查询启用的工具

### backend/app/services/rag_service.py

简化知识库服务。

负责：

- 保存上传文件
- 文本切 chunk
- 保存 Document / DocumentChunk
- 简单关键词检索

当前还不是正式 RAG。

后续会在这里升级：

- embedding
- pgvector
- 相似度检索

### backend/app/services/run_log_service.py

运行日志服务。

负责：

- 创建 run
- 写入 run_step
- 结束 run
- 查询 run 列表
- 查询 run_steps

在 Dify-like 平台里，日志非常重要。因为低代码 workflow 必须能看到每个节点到底发生了什么。

## 4. Runtime 层

Runtime 层是当前项目最核心的部分。

### backend/app/runtime/workflow_executor.py

workflow 解释器。

负责：

- 读取 App 的 `workflow_spec`
- 根据 `edges` 排出节点顺序
- 执行不同类型的节点
- 维护本次执行的结果
- 产生 SSE event
- 写 run_step 日志

当前支持的节点类型：

- `start`
- `retrieval`
- `tool`
- `react_agent` / `agent`
- `end`

这个文件是理解“低代码 workflow 如何被执行”的核心。

你可以重点看这些方法：

- `execute`
- `_ordered_nodes`
- `_execute_retrieval`
- `_execute_agent`
- `_execute_end`

### backend/app/runtime/agent_adapters.py

agent 节点适配层。

负责：

- 定义 agent 执行输入 `AgentInvocation`
- 定义基础 adapter 接口
- 提供 `AgentScopeAdapter`
- 根据配置校验并创建 AgentScope adapter

重要类：

- `AgentInvocation`
- `BaseAgentAdapter`
- `AgentScopeAdapter`

当前 agent 运行统一走 `AgentScopeAdapter`，`MockAgentAdapter` 已移除。

### backend/app/runtime/agent_runner.py

兼容旧代码的薄封装。

之前项目直接使用 `AgentRunner`。现在真正的逻辑已经迁到 `agent_adapters.py` 和 `workflow_executor.py`。

这个文件只是保留旧接口，避免后续有旧引用时直接报错。

后续可以删除，或者完全替换为更清晰的 adapter 调用。

## 5. 工具层

### backend/app/tools/registry.py

工具注册中心。

负责：

- 声明内置工具
- 返回工具列表
- 根据工具名称执行工具

当前内置工具：

- `calculator`
- `current_time`
- `query_order`
- `mock_weather`

后续接 AgentScope 时，会把这些工具注册进 AgentScope Toolkit。

### backend/app/tools/calculator.py

当前是占位文件。

后续可以把 calculator 工具从 `registry.py` 拆出来，放进这个文件。

## 6. 数据库层

### backend/app/db/session.py

数据库连接和 session 管理。

负责：

- 创建 SQLAlchemy engine
- 创建 SessionLocal
- 提供 `get_db`
- 初始化数据库表

### backend/app/db/models.py

数据库模型定义。

主要表：

- `apps`
- `app_tools`
- `documents`
- `document_chunks`
- `conversations`
- `messages`
- `runs`
- `run_steps`

其中最重要的是：

- `apps.workflow_spec`: 保存工作流配置
- `runs`: 每次运行
- `run_steps`: 每个节点的运行记录

## 7. 配置和 Schema

### backend/app/core/config.py

应用配置读取。

负责：

- 数据库连接串
- Redis 连接串
- 文件存储路径
- CORS origins

### backend/app/schemas.py

Pydantic schema。

负责：

- API 请求体
- API 响应结构
- 默认 workflow_spec

这里的 `DEFAULT_WORKFLOW_SPEC` 很重要，因为新建 App 时会用它作为默认流程。

## 8. 前端入口

### frontend/src/main.tsx

前端主入口和当前唯一页面。

负责：

- 应用列表
- 创建 demo app
- Agent 配置表单
- 工具勾选
- 文档上传
- 聊天窗口
- SSE stream 处理
- Trace 展示
- Runs 展示

目前为了 MVP 快速跑通，前端还没有拆组件。

后续可以拆成：

- `pages/Playground.tsx`
- `components/AppSidebar.tsx`
- `components/AgentConfigPanel.tsx`
- `components/ChatPanel.tsx`
- `components/TracePanel.tsx`
- `components/WorkflowEditor.tsx`

### frontend/src/api.ts

前端 API 客户端。

负责：

- 请求后端 REST API
- 上传文档
- 处理 SSE 流式聊天

如果你想知道前端怎么调用后端，看这个文件。

### frontend/src/types.ts

前端 TypeScript 类型定义。

包括：

- `AppItem`
- `ToolItem`
- `AppTool`
- `RunItem`
- `ChatMessage`

### frontend/src/styles.css

前端样式文件。

当前是手写 CSS，没有引入 Ant Design 或 shadcn/ui。

## 9. 推荐学习路线

### 第一遍：理解主链路

按这个顺序读：

1. `backend/app/main.py`
2. `backend/app/api/routes/chat.py`
3. `backend/app/services/chat_service.py`
4. `backend/app/runtime/workflow_executor.py`
5. `backend/app/runtime/agent_adapters.py`

目标：

看懂一次聊天请求怎么从 HTTP 进入，怎么执行 workflow，怎么返回 SSE。

### 第二遍：理解平台配置

按这个顺序读：

1. `backend/app/schemas.py`
2. `backend/app/db/models.py`
3. `backend/app/services/app_service.py`
4. `frontend/src/main.tsx`

目标：

看懂 App 配置、工具配置、workflow_spec 是怎么保存和展示的。

### 第三遍：理解知识库和日志

按这个顺序读：

1. `backend/app/services/rag_service.py`
2. `backend/app/api/routes/knowledge.py`
3. `backend/app/services/run_log_service.py`
4. `backend/app/api/routes/runs.py`

目标：

看懂上传文档、检索、run、run_step 是怎么形成 trace 的。

### 第四遍：理解前端

按这个顺序读：

1. `frontend/src/main.tsx`
2. `frontend/src/api.ts`
3. `frontend/src/types.ts`
4. `frontend/src/styles.css`

目标：

看懂前端如何创建应用、配置工具、上传文档、发送聊天请求、消费 SSE。

## 10. 一次聊天请求的完整调用链

```text
用户在前端输入问题
  -> frontend/src/main.tsx
    -> streamChat()
      -> frontend/src/api.ts
        -> POST /api/apps/{app_id}/chat
          -> backend/app/api/routes/chat.py
            -> chat_service.chat_stream()
              -> create conversation
              -> save user message
              -> create run
              -> WorkflowExecutor.execute()
                -> start node
                -> retrieval node
                  -> rag_service.retrieve_chunks()
                -> agent node
                  -> AgentScopeAdapter
                  -> tools.registry.run_tool()
                -> end node
              -> save assistant message
              -> finish run
              -> return SSE events
```

## 11. 学习时最应该抓住的主线

不要从数据库配置或 Docker 开始学。

先抓这条线：

```text
workflow_spec
  -> WorkflowExecutor
    -> Agent Adapter
      -> AgentScope
    -> run_steps
```

这是这个项目区别于普通聊天应用的核心。

## 12. 当前最值得重点阅读的 5 个文件

如果时间有限，先读：

1. `backend/app/main.py`
2. `backend/app/api/routes/chat.py`
3. `backend/app/services/chat_service.py`
4. `backend/app/runtime/workflow_executor.py`
5. `backend/app/runtime/agent_adapters.py`

这 5 个文件读懂后，你就能理解项目的主干。
