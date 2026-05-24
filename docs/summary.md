# 后端架构总结

## 一、数据库表关系

```
User ───1:N──→ ModelCredential            owner_user_id FK → users.id

User ───1:N──→ App                         owner_user_id FK → apps.id
                 │
                 ├──1:N──→ AppTool          app_id FK → apps.id
                 │
                 ├──1:N──→ Document         app_id FK → apps.id
                 │            │
                 │            └──1:N──→ DocumentChunk   document_id FK → documents.id
                 │
                 └──1:N──→ Conversation     app_id FK → apps.id
                              │
                              ├──1:N──→ Message         conversation_id FK → conversations.id
                              │
                              └──1:N──→ Run             conversation_id FK → conversations.id
                                          │               app_id FK → apps.id
                                          │               input_message_id FK → messages.id
                                          │               output_message_id FK → messages.id
                                          │
                                          └──1:N──→ RunStep  run_id FK → runs.id
```

### 表间关系说明

- **User → App**：一个用户拥有多个应用。
- **User → ModelCredential**：一个用户持有多个加密的模型 API Key，按 provider 区分。
- **App → AppTool**：一个应用启用哪些内置工具。
- **App → Document → DocumentChunk**：一个应用上传多份文档，每份文档被切分成多个块。
- **App → Conversation**：一个应用下有多段对话。
- **Conversation → Message**：一段对话包含多条消息（user / assistant）。
- **Conversation → Run**：一段对话中每条用户消息触发一次 Run（一次 workflow 执行）。
- **Run → RunStep**：一次 Run 包含多个执行步骤（start → retrieval → tool_call → agent → end），即前端的 trace。
- **Run 的双 FK**：Run 同时持有 `conversation_id` 和 `app_id`。`app_id` 虽然可以通过 `Run → Conversation → App` 间接拿到，但直接存储免去每次按 App 查 Run 时多 JOIN 一张表。
- **Run 的 message FK**：`input_message_id` 指向触发本次执行的用户消息，`output_message_id` 指向本次执行产出的 assistant 消息，实现 Run 与具体 Message 的双向追溯。
- **权限隔离**：Message 查询时 JOIN Conversation 校验 `user_id`，确保用户只能看到自己所属会话的消息。

---

## 二、回调机制 — AgentScope 工具注册与执行链路

### 链路总览

```
agent_adapters.run()
  │
  │  tool_events = []
  │  trace_sink = tool_events.append                        ← 列表的 append 方法作为回调
  │
  └──→ _create_agent(invocation, trace_sink)
         │
         └──→ build_agentscope_toolkit(enabled_tools, trace_sink)
                │
                │  遍历 enabled_tools，dict.fromkeys 去重
                │
                │  对每个 tool_name:
                │    tool_builder = builders[tool_name]        ← 取出对应的 _build_xxx_tool 函数
                │    closure_func = tool_builder(trace_sink)   ← 调用 builder，返回闭包函数
                │    toolkit.register_tool_function(closure_func) ← 注册进 AgentScope，暂不调用
                │
                └──→ 返回 toolkit（含 4 个已注册的闭包函数）

  └──→ stream_printing_messages 运行 ReAct：
         │
         │  AgentScope 决定调用 query_order("10086")
         │    └──→ 闭包 query_order 执行
         │           └──→ _run_agentscope_tool("query_order", {"order_id": "10086"}, trace_sink)
         │                  │
         │                  │  output = run_tool("query_order", {"order_id": "10086"})
         │                  │  trace_sink(event)              ← 同步回调发生！
         │                  │  return ToolResponse(...)
         │
         │  AgentScope 继续推理，可能再调工具，最终产出 final
         │
         └──→ 所有工具调用事件已通过 trace_sink 进入 tool_events 列表
```

### 分阶段详解

#### 阶段一：参数传递（setup 前）

`agent_adapters.run()` 中创建空列表 `tool_events = []`，然后将 `tool_events.append` 作为 `trace_sink` 参数一路传递。`tool_events.append` 是 Python 列表的内置方法，本身是一个可调用对象。

```python
tool_events: list[RuntimeEvent] = []
agent = self._create_agent(invocation, tool_events.append)
```

#### 阶段二：build_agentscope_toolkit — 注册工具函数

```python
def build_agentscope_toolkit(enabled_tools, trace_sink=None):
    toolkit = Toolkit()

    builders = {
        "calculator":   _build_calculator_tool,
        "current_time": _build_current_time_tool,
        "query_order":  _build_query_order_tool,
        "mock_weather": _build_mock_weather_tool,
    }

    for tool_name in dict.fromkeys(enabled_tools):  # dict.fromkeys 去重
        if tool_name not in _TOOL_NAMES:
            continue
        tool_builder = builders.get(tool_name)       # 取出 builder 函数
        if tool_builder:
            toolkit.register_tool_function(
                tool_builder(trace_sink)              # 调用 builder(trace_sink)，得到闭包函数
            )                                         # register_tool_function 解析函数签名并注册

    return toolkit
```

`register_tool_function` 会解析传入函数的签名和 docstring，生成 AgentScope 能理解的工具 schema，存入 `toolkit.tools`。此时仅注册，不执行。

#### 阶段三：四个 builder — 闭包工厂

每个 `_build_xxx_tool(trace_sink)` 接收 `trace_sink`，返回一个**内层函数**。内层函数通过闭包捕获了 `trace_sink`，并拥有明确参数签名，供 AgentScope 在运行时调用：

```python
def _build_calculator_tool(trace_sink):
    def calculator(expression: str):
        """计算简单算式。"""
        arguments = {"expression": expression}
        return _run_agentscope_tool("calculator", arguments, trace_sink)
    return calculator


def _build_current_time_tool(trace_sink):
    def current_time():
        """返回服务器当前时间。"""
        return _run_agentscope_tool("current_time", {}, trace_sink)
    return current_time


def _build_query_order_tool(trace_sink):
    def query_order(order_id: str):
        """查询 mock 电商订单状态。"""
        arguments = {"order_id": order_id}
        return _run_agentscope_tool("query_order", arguments, trace_sink)
    return query_order


def _build_mock_weather_tool(trace_sink):
    def mock_weather(city: str = "上海"):
        """查询 mock 天气。"""
        arguments = {"city": city}
        return _run_agentscope_tool("mock_weather", arguments, trace_sink)
    return mock_weather
```

四个 builder 模式完全一致：
1. 接收 `trace_sink`
2. 定义内层函数，内层函数的参数直接对应 AgentScope 调用时传入的实参
3. 内层函数将自身参数整理成 `arguments` 字典，连同硬编码的 `tool_name` 和闭包中的 `trace_sink` 交给 `_run_agentscope_tool`
4. 返回内层函数

#### 阶段四：_run_agentscope_tool — 回调终点

```python
def _run_agentscope_tool(tool_name, arguments, trace_sink):
    output = run_tool(tool_name, arguments)       # 执行真正的工具逻辑

    event = {
        "type": "tool_call",
        "name": tool_name,
        "input": arguments,
        "output": output,
        "source": "agentscope",
        "latency_ms": int((perf_counter() - started) * 1000),
    }

    if trace_sink:
        trace_sink(event)                         # 同步回调 = tool_events.append(event)

    return ToolResponse(
        content=[TextBlock(type="text", text=json.dumps(output, ensure_ascii=False))]
    )                                             # 返回 AgentScope 能识别的 ToolResponse
```

`trace_sink(event)` 是一个普通的同步函数调用，当场执行。此时 AgentScope 的 ReAct agent 正在阻塞等待工具结果，所以事件立刻进入 `tool_events` 列表。`stream_printing_messages` 吐出下一个 msg 之前，事件已经就位。

### 关键设计点

1. **闭包工厂模式**：`_build_xxx_tool` 不执行工具，而是创建一个带有 `trace_sink` 闭包变量的函数，注册进 AgentScope。AgentScope 在运行时才调用该函数。

2. **同步回调**：`trace_sink(event)` 不是异步的，不依赖线程或定时器。它发生在 AgentScope 调用工具函数的过程中，AgentScope 的 ReAct agent 阻塞等待工具返回，期间事件已同步写入 `tool_events`。

3. **注册与执行分离**：`register_tool_function` 发生在 agent 启动前（setup），工具函数的实际调用发生在 ReAct 推理过程中（runtime）。

4. **去重**：`dict.fromkeys(enabled_tools)` 确保即使 `enabled_tools` 中有重复的工具名，也只会注册一次。

5. **解耦**：工具层（`registry.py`）不直接依赖 `tool_events` 这个变量名，它只认 `trace_sink` 这个函数签名。换成任何其他可调用对象都能工作——比如换成 `print`，事件就会直接打印到控制台。
