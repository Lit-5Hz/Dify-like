from __future__ import annotations

import re
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from time import perf_counter
from typing import Any

from app.tools.registry import build_agentscope_toolkit, run_tool


RuntimeEvent = dict[str, Any]


@dataclass
class AgentInvocation:
    app_name: str
    query: str
    system_prompt: str
    model_provider: str
    model_name: str
    model_config: dict[str, Any] = field(default_factory=dict)
    model_credential_id: str = ""
    api_key: str = ""
    node_config: dict[str, Any] = field(default_factory=dict)
    enabled_tools: list[str] = field(default_factory=list)
    retrieved_chunks: list[dict[str, Any]] = field(default_factory=list)


class BaseAgentAdapter:
    name = "base"

    async def run(self, invocation: AgentInvocation) -> AsyncIterator[RuntimeEvent]:
        raise NotImplementedError


class MockAgentAdapter(BaseAgentAdapter):
    name = "mock"

    async def run(self, invocation: AgentInvocation) -> AsyncIterator[RuntimeEvent]:
        answer_parts: list[str] = []
        query = invocation.query
        enabled_tools = set(invocation.enabled_tools)

        order_match = re.search(r"\b(\d{4,})\b", query)
        if order_match and "query_order" in enabled_tools:
            order_id = order_match.group(1)
            tool_result = run_tool("query_order", {"order_id": order_id})
            yield {
                "type": "tool_call",
                "name": "query_order",
                "input": {"order_id": order_id},
                "output": tool_result,
                "source": "mock",
            }
            answer_parts.append(str(tool_result.get("status", tool_result)))

        if any(word in query for word in ["几点", "时间", "现在"]) and "current_time" in enabled_tools:
            tool_result = run_tool("current_time", {})
            yield {
                "type": "tool_call",
                "name": "current_time",
                "input": {},
                "output": tool_result,
                "source": "mock",
            }
            answer_parts.append(f"当前时间是 {tool_result['result']}。")

        if any(word in query for word in ["天气", "气温"]) and "mock_weather" in enabled_tools:
            city = "上海"
            tool_result = run_tool("mock_weather", {"city": city})
            yield {
                "type": "tool_call",
                "name": "mock_weather",
                "input": {"city": city},
                "output": tool_result,
                "source": "mock",
            }
            answer_parts.append(tool_result["weather"])

        if invocation.retrieved_chunks:
            context = invocation.retrieved_chunks[0]["content"]
            answer_parts.append(f"根据知识库：{context[:260]}")

        if not answer_parts:
            answer_parts.append("我已经收到你的问题。当前 demo 使用 mock adapter；接入模型后会由 AgentScope 执行 agent。")

        final_answer = "\n\n".join(answer_parts)
        for token in final_answer:
            yield {"type": "message_delta", "content": token, "source": "mock"}
        yield {"type": "final", "content": final_answer, "source": "mock"}


class AgentScopeAdapter(BaseAgentAdapter):
    name = "agentscope"

    async def run(self, invocation: AgentInvocation) -> AsyncIterator[RuntimeEvent]:
        # 函数作用：把项目内部的 AgentInvocation 转成 AgentScope 调用，
        # 再把 AgentScope 的输出转回项目统一的 RuntimeEvent。
        # tool_events 用来接住 AgentScope 内部工具调用产生的 trace，再穿透给 workflow/chat_stream。
        tool_events: list[RuntimeEvent] = []
        try:
            # 1.创建 AgentScope agent，并把 trace_sink 传给工具层，让工具执行时能回推 tool_call 事件
            agent = self._create_agent(invocation, tool_events.append)
            # 2.导入 AgentScope 的消息和流式工具:
            from agentscope.message import Msg  # Msg 用来把用户输入包装成 AgentScope 能识别的消息
            from agentscope.pipeline import stream_printing_messages  # stream_printing_messages 用来接收 AgentScope 运行中的流式输出
        except ImportError as exc:
            yield {
                "type": "adapter_error",
                "adapter": self.name,
                "message": (
                    "AgentScope is not installed. Run `pip install -e \".[agentscope]\"` first. "
                    f"Details: {exc}"
                ),
            }
            return
        except Exception as exc:
            yield {
                "type": "adapter_error",
                "adapter": self.name,
                "message": str(exc),
            }
            return

        # 3.关闭 AgentScope 自己往控制台打印；不同版本可能没有这个方法，所以这里做一层兼容。
        set_console_output_enabled = getattr(agent, "set_console_output_enabled", None)
        if callable(set_console_output_enabled):
            set_console_output_enabled(False)

        # 4.把用户输入包装成一条 user 消息，启动 AgentScope 的 ReActAgent 执行。
        # task 会被交给 stream_printing_messages() 去流式消费。
        task = agent(Msg("user", invocation.query, "user"))
        previous = ""
        latest_text = ""

        try:
            # 5.把 AgentScope 输出(Msg 流)转换成项目事件(RuntimeEvent 流)，这是 adapter 的核心：
            # AgentScope 给出的通常是“当前完整文本”，而前端需要的是“增量文本”。
            # 所以代码用 previous 记录上一次文本，然后算出这次新增的部分 delta。
            async for msg, last in stream_printing_messages(agents=[agent], coroutine_task=task):
                # 工具调用 trace 是从工具函数里同步塞进 tool_events 的；这里在模型消息之间尽快吐给上层。
                while tool_events:
                    yield tool_events.pop(0)

                current = self._extract_text(msg)
                if current:
                    latest_text = current
                # delta 是从累计文本里切出来的新增文本片段。
                # 前端只需要不断 append delta，就能形成流式输出。
                delta = current[len(previous) :] if current.startswith(previous) else current
                previous = current
                if delta:
                    yield {
                        "type": "message_delta",
                        "content": delta,
                        "source": "agentscope",
                    }

                if last:
                    # 注意：AgentScope 的 last=True 表示“当前打印消息结束”，不等于“整个 agent 结束”。
                    # 因此这里不能直接 yield final，只把这一段期间累积的工具 trace 先吐出去。
                    while tool_events:
                        yield tool_events.pop(0)
        except Exception as exc:
            yield {
                "type": "adapter_error",
                "adapter": self.name,
                "message": str(exc),
            }
            return

        while tool_events:
            yield tool_events.pop(0)

        # 6.只有当 stream_printing_messages 整体结束后，才说明 agent 本轮执行真正完成。
        # 这里统一发一次 final，避免 ReAct 工具调用过程中提前出现空 final。
        yield {
            "type": "final",
            "content": latest_text or previous,
            "source": "agentscope",
        }

    def _create_agent(self, invocation: AgentInvocation, trace_sink):
        """
        创建模型
        创建 formatter
        创建 Toolkit
        注册平台工具
        拼 system prompt
        创建 ReActAgent
        """
        from agentscope.agent import ReActAgent
        from agentscope.memory import InMemoryMemory

        # formatter 是 LLM 消息协议适配器，告诉 AgentScope 怎么把消息整理给这个品牌的 LLM 模型。
        model, formatter = self._build_model_and_formatter(invocation)
        # 创建 AgentScope 工具箱，并把当前 app 启用的平台工具注册进去。
        # register_tool_function() 会把 Python 函数解析成 AgentScope 能理解的工具对象，再存进 toolkit.tools。
        toolkit = build_agentscope_toolkit(invocation.enabled_tools, trace_sink=trace_sink)
        sys_prompt = self._build_system_prompt(invocation)
        return ReActAgent(
            name=invocation.app_name or "assistant",
            sys_prompt=sys_prompt,
            model=model,
            formatter=formatter,
            toolkit=toolkit,
            memory=InMemoryMemory(),
        )

    def _build_model_and_formatter(self, invocation: AgentInvocation):
        # 统一解析 provider/model/base_url/API key，并按 AgentScope 1.0.19.post1 的真实签名创建模型对象。
        provider = self._resolve_provider(invocation)
        model_name = str(invocation.model_config.get("model_name") or invocation.model_name or "").strip()
        base_url = str(invocation.model_config.get("base_url") or "").strip()
        generate_kwargs = self._build_generate_kwargs(invocation)
        reasoning_effort = str(invocation.model_config.get("reasoning_effort") or "").strip()  # 部分 OpenAI 模型支持的推理强度参数
        api_key = str(invocation.api_key or "").strip()
        credential_id = str(invocation.model_credential_id or invocation.model_config.get("credential_id") or "").strip()

        if not api_key:
            raise ValueError(
                f"Missing API key for provider '{provider}'. "
                f"Choose or create a model credential in app/node config (credential_id={credential_id or 'empty'})."
            )

        if provider in {"openai", "openai_compatible", "deepseek", "vllm"}:
            from agentscope.formatter import DeepSeekChatFormatter, OpenAIChatFormatter
            from agentscope.model import OpenAIChatModel

            kwargs: dict[str, Any] = {
                "model_name": model_name,
                "api_key": api_key,
                "stream": True,
            }
            if generate_kwargs:
                kwargs["generate_kwargs"] = generate_kwargs
            if reasoning_effort:
                kwargs["reasoning_effort"] = reasoning_effort
            if base_url:
                # AgentScope 的 OpenAIChatModel 不收顶层 base_url，需要放进 client_kwargs。
                kwargs["client_kwargs"] = {"base_url": base_url}
            formatter = DeepSeekChatFormatter() if self._should_use_deepseek_formatter(provider, model_name, base_url) else OpenAIChatFormatter()
            return OpenAIChatModel(**kwargs), formatter

        if provider in {"dashscope", "qwen"}:
            from agentscope.formatter import DashScopeChatFormatter
            from agentscope.model import DashScopeChatModel

            kwargs: dict[str, Any] = {
                "model_name": model_name,
                "api_key": api_key,
                "stream": True,
            }
            if generate_kwargs:
                kwargs["generate_kwargs"] = generate_kwargs
            if base_url:
                # DashScopeChatModel 对应的自定义 HTTP 地址参数名是 base_http_api_url。
                kwargs["base_http_api_url"] = base_url
            return DashScopeChatModel(**kwargs), DashScopeChatFormatter()

        raise ValueError(f"Unsupported AgentScope model provider: {provider}")

    def _build_system_prompt(self, invocation: AgentInvocation) -> str:
        if not invocation.retrieved_chunks:
            return invocation.system_prompt
        context = "\n\n".join(chunk["content"] for chunk in invocation.retrieved_chunks)
        return f"{invocation.system_prompt}\n\nKnowledge context:\n{context}"

    def _build_generate_kwargs(self, invocation: AgentInvocation) -> dict[str, Any]:
        # 前端目前沿用 70/100 这种百分比式数值；真实模型 API 通常需要 0.7/1.0。
        # 所以 temperature/top_p 大于 1 时会自动除以 100。
        generate_kwargs: dict[str, Any] = {}
        for key in ("temperature", "top_p", "max_tokens"):
            value = invocation.model_config.get(key)
            if value in {None, ""}:
                continue
            if key in {"temperature", "top_p"}:
                numeric = float(value)
                if numeric > 1:
                    numeric = numeric / 100.0
                generate_kwargs[key] = numeric
            else:
                generate_kwargs[key] = int(value)

        return generate_kwargs

    def _should_use_deepseek_formatter(self, provider: str, model_name: str, base_url: str) -> bool:
        # DeepSeek reasoning/tool-call 回合需要把 thinking block 重新格式化成 reasoning_content。
        # 通用 OpenAIChatFormatter 会跳过 thinking block，工具调用后的第二次请求会被 DeepSeek 拒绝。
        text = f"{provider} {model_name} {base_url}".lower()
        return "deepseek" in text

    def _resolve_provider(self, invocation: AgentInvocation) -> str:
        # provider 优先读合并后的 model_config；没有时回退到 invocation.model_provider。
        provider = invocation.model_config.get("provider") or invocation.model_provider or ""
        return str(provider).strip().lower()

    def _extract_text(self, msg: Any) -> str:
        # AgentScope 的 Msg 可能有 get_text_content()，也可能直接暴露 content blocks。
        # 这里做一层兼容，把不同形状都统一抽成纯文本，方便上层做 delta。
        if hasattr(msg, "get_text_content"):
            try:
                return str(msg.get_text_content() or "")
            except Exception:
                pass

        content = getattr(msg, "content", msg)
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for block in content:
                if isinstance(block, dict):
                    text = block.get("text")
                else:
                    text = getattr(block, "text", None)
                if text:
                    parts.append(str(text))
            return "".join(parts)
        return str(content or "")


def build_agent_adapter(adapter_name: str | None, model_provider: str) -> BaseAgentAdapter:
    selected = (adapter_name or "").lower()
    provider = (model_provider or "").lower()
    if selected == "mock":
        return MockAgentAdapter()
    if selected == "agentscope":
        return AgentScopeAdapter()
    if provider not in {"", "mock"}:
        return AgentScopeAdapter()
    return MockAgentAdapter()
