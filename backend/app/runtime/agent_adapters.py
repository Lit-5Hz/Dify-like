from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from app.tools.registry import build_agentscope_toolkit


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
    enabled_mcp_tools: list[dict[str, Any]] = field(default_factory=list)
    retrieved_chunks: list[dict[str, Any]] = field(default_factory=list)
    context_metadata: dict[str, Any] = field(default_factory=dict)


class BaseAgentAdapter:
    name = "base"

    async def run(self, invocation: AgentInvocation) -> AsyncIterator[RuntimeEvent]:
        raise NotImplementedError


def build_agent_context(invocation: AgentInvocation) -> tuple[str, dict[str, Any]]:
    if not invocation.retrieved_chunks:
        return "", {"used_chunk_ids": [], "dropped_chunk_ids": [], "available_tokens": 0}

    context_window = _resolve_context_window(invocation)
    reserved_output = _to_int(
        invocation.model_config.get("context_reserved_output_tokens")
        or invocation.model_config.get("max_tokens")
        or invocation.node_config.get("context_reserved_output_tokens"),
        1024,
    )
    safety_margin = _to_int(
        invocation.model_config.get("context_safety_margin") or invocation.node_config.get("context_safety_margin"),
        400,
    )
    base_tokens = _estimate_tokens(invocation.system_prompt) + _estimate_tokens(invocation.query)
    available_tokens = max(context_window - reserved_output - safety_margin - base_tokens, 0)

    ranked_chunks = sorted(
        invocation.retrieved_chunks,
        key=lambda item: float(item.get("score", 0.0) or 0.0),
        reverse=True,
    )
    selected: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []
    used_tokens = 0
    for chunk in ranked_chunks:
        block = _format_context_chunk(chunk)
        token_count = _estimate_tokens(block)
        if token_count <= max(available_tokens - used_tokens, 0):
            selected.append({**chunk, "_context_block": block, "_context_tokens": token_count})
            used_tokens += token_count
        else:
            dropped.append(chunk)

    reordered = _lost_in_the_middle_reorder(selected)
    context_block = "\n\n".join(str(chunk.get("_context_block") or "") for chunk in reordered if chunk.get("_context_block"))
    metadata = {
        "context_window": context_window,
        "reserved_output_tokens": reserved_output,
        "safety_margin_tokens": safety_margin,
        "base_tokens": base_tokens,
        "available_tokens": available_tokens,
        "used_tokens": used_tokens,
        "used_chunk_ids": [str(chunk.get("chunk_id") or "") for chunk in reordered],
        "dropped_chunk_ids": [str(chunk.get("chunk_id") or "") for chunk in dropped],
        "reorder": "lost_in_the_middle",
        "source_label_format": "[source_file | page page_num | chunk_type | chunk_role | chunk_id]",
    }
    return context_block, metadata


def _resolve_context_window(invocation: AgentInvocation) -> int:
    configured = _to_int(
        invocation.model_config.get("model_context_window") or invocation.node_config.get("model_context_window"),
        0,
    )
    if configured > 0:
        return configured
    text = f"{invocation.model_provider} {invocation.model_name}".lower()
    if "gpt-4o" in text or "gpt-4.1" in text:
        return 128000
    if "deepseek" in text:
        return 64000
    if "qwen" in text and "72" in text:
        return 32000
    if "qwen" in text:
        return 8192
    return 8192


def _estimate_tokens(text: str) -> int:
    value = str(text or "")
    if not value:
        return 0
    try:
        import tiktoken

        encoding = tiktoken.get_encoding("cl100k_base")
        return int(len(encoding.encode(value)) * 1.2) + 1
    except Exception:
        return max(len(value) // 3, 1)


def _format_context_chunk(chunk: dict[str, Any]) -> str:
    source_file = str(chunk.get("source_file") or "unknown")
    page_num = chunk.get("page_num")
    page_label = f"page {page_num}" if page_num not in {None, "", 0} else "page unknown"
    chunk_type = str(chunk.get("chunk_type") or "text")
    chunk_role = str(chunk.get("chunk_role") or "standalone")
    chunk_id = str(chunk.get("chunk_id") or "unknown")
    section = str(chunk.get("section") or "").strip()
    section_line = f"Section: {section}\n" if section else ""
    return f"[{source_file} | {page_label} | {chunk_type} | {chunk_role} | {chunk_id}]\n{section_line}{chunk.get('content', '')}"


def _lost_in_the_middle_reorder(chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if len(chunks) <= 2:
        return chunks
    reordered: list[dict[str, Any] | None] = [None] * len(chunks)
    left = 0
    right = len(chunks) - 1
    for index, chunk in enumerate(chunks):
        if index % 2 == 0:
            reordered[left] = chunk
            left += 1
        else:
            reordered[right] = chunk
            right -= 1
    return [chunk for chunk in reordered if chunk is not None]


def _to_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


class AgentScopeAdapter(BaseAgentAdapter):
    """
    职责是把 AgentScope 的 ReActAgent 包装成本项目统一认识的事件流。大概流程是：
        输入 AgentInvocation
        -> 创建 AgentScope model + formatter
        -> 注册工具 toolkit
        -> 启动 ReActAgent
        -> 监听 stream_printing_messages
        -> 把 AgentScope 输出转成 RuntimeEvent
    它吐给上层的事件主要有几类：
        message_delta   模型增量输出
        tool_call       AgentScope 内部调用工具
        final           这轮 agent 结束
        adapter_error   AgentScope 出错
    """

    name = "agentscope"

    async def run(self, invocation: AgentInvocation) -> AsyncIterator[RuntimeEvent]:
        # 函数作用：把项目内部的 AgentInvocation 转成 AgentScope 调用，再把 AgentScope 的输出转回项目统一的 RuntimeEvent。

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
        # stream_printing_messages 结束时，agent可能在最后一轮打印之后又调了一个工具（还没等模型输出新文本，循环就结束了）。这个工具事件装在 tool_events里但循环已经退出，没人消费它。
        # 下面这两行代码做了兜底清空——确保没有任何工具事件被漏掉，然后才发 final。
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
        toolkit = build_agentscope_toolkit(
            invocation.enabled_tools,
            invocation.enabled_mcp_tools,
            trace_sink=trace_sink,
        )
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
        context_block, context_metadata = build_agent_context(invocation)
        invocation.context_metadata = context_metadata
        if not context_block:
            return invocation.system_prompt
        return f"{invocation.system_prompt}\n\nKnowledge context:\n{context_block}"

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
    if selected in {"", "agentscope"}:
        return AgentScopeAdapter()
    if selected == "mock":
        raise ValueError("Mock agent adapter has been removed. Configure the agent to use AgentScope.")
    raise ValueError(f"Unsupported agent adapter: {selected}")
