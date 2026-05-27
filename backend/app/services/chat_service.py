import json  # 用于把事件数据转成 JSON 字符串，方便 SSE 发送
from collections.abc import AsyncIterator  # 用于标注异步生成器的返回类型
from time import perf_counter  # 用于统计一次聊天运行耗时

from sqlalchemy import select  # 用于构造 SQL 查询
from sqlalchemy.orm import Session  # 数据库会话类型

from app.db.models import Conversation, Message  # 导入会话和消息的数据库模型
from app.runtime.workflow_executor import WorkflowExecutor  # 导入 workflow 执行器
from app.services.app_service import get_enabled_tool_names  # 导入读取当前 app 已启用工具的函数
from app.services.run_log_service import add_step, create_run, finish_run  # 导入 run 和 step 相关的日志服务


def _sse(event: str, data: dict) -> str:  # 把一个事件包装成 SSE 文本格式
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"  # 按 SSE 协议拼出 event/data 块
    """
    SSE 的基本格式：
        event: run_started
        data: {"conversation_id": "...", "run_id": "..."}
    """


def get_conversation_for_user(db: Session, conversation_id: str, user_id: str) -> Conversation | None:
    return db.scalar(select(Conversation).where(Conversation.id == conversation_id, Conversation.user_id == user_id))


def get_or_create_conversation(db: Session, app_id: str, user_id: str, conversation_id: str | None) -> Conversation:  # 先找会话，找不到就创建
    if conversation_id:  # 如果前端已经带了会话 id
        conversation = db.get(Conversation, conversation_id)  # 直接按主键查现有会话
        if conversation and conversation.app_id == app_id and conversation.user_id == user_id:  # 如果查到了，并且确实属于当前用户和 App
            return conversation  # 直接复用这个会话
    conversation = Conversation(app_id=app_id, user_id=user_id)  # 如果没有传，或者没查到，就创建一个新的会话
    db.add(conversation)  # 加入当前事务
    db.commit()  # 写入数据库
    db.refresh(conversation)  # 刷新对象，拿到数据库生成的字段，比如 id
    return conversation  # 返回会话对象


def add_message(db: Session, conversation_id: str, role: str, content: str, metadata_json: dict | None = None) -> Message:  # 给会话新增一条消息
    message = Message(  # 构造消息 ORM 对象
        conversation_id=conversation_id,  # 这条消息属于哪个会话
        role=role,  # 消息角色：user / assistant 等
        content=content,  # 消息正文
        metadata_json=metadata_json or {},  # 附加元数据，没有就用空字典
    )
    db.add(message)  # 加入事务
    db.commit()  # 提交到数据库
    db.refresh(message)  # 刷新对象，拿到数据库生成的 id 和时间
    return message  # 返回新消息


async def chat_once(db: Session, app, query: str, user_id: str, conversation_id: str | None = None) -> dict:  # 一次性聊天接口，返回完整结果
    started = perf_counter()  # 记录开始时间，用于计算耗时
    conversation = get_or_create_conversation(db, app.id, user_id, conversation_id)  # 获取或创建会话
    user_message = add_message(db, conversation.id, "user", query)  # 先把用户输入存成一条消息
    run = create_run(db, app.id, conversation.id, user_message.id)  # 创建一次 run 记录

    enabled_tools = get_enabled_tool_names(db, app.id)  # 查出当前 app 已启用的工具列表
    executor = WorkflowExecutor(db, app, run.id)  # 创建 workflow 执行器，绑定这次 run
    adapter_error = ""
    try:
        async for event in executor.execute(query, enabled_tools, conversation.id, user_id):  # 执行 workflow，但这里不逐个处理事件
            if event["type"] == "adapter_error":
                adapter_error = str(event.get("message", "Agent adapter error"))
                break
    except Exception as exc:
        finish_run(db, run, started, status="error", error=str(exc))
        add_step(db, run.id, "error", "runtime_error", {}, {}, error=str(exc))
        raise

    if adapter_error:
        finish_run(db, run, started, status="error", error=adapter_error)
        raise ValueError(adapter_error)

    output = add_message(  # 把最终回答存成 assistant 消息
        db,
        conversation.id,
        "assistant",
        executor.result.answer,  # workflow 执行器累积出的最终答案
        {
            "tool_calls": executor.result.tool_calls,  # 顺便把工具调用记录进消息元数据
            "retrieved_chunks": executor.result.retrieved_chunks,  # 顺便把检索到的片段记录进消息元数据
        },
    )
    finish_run(db, run, started, output_message_id=output.id)  # 结束 run，并写入输出消息 id 和耗时
    return {  # 返回给调用方的完整结果
        "conversation_id": conversation.id,  # 当前会话 id
        "run_id": run.id,  # 当前运行 id
        "answer": executor.result.answer,  # 最终答案
        "tool_calls": executor.result.tool_calls,  # 工具调用列表
        "retrieved_chunks": executor.result.retrieved_chunks,  # 检索结果列表
    }


async def chat_stream(db: Session, app, query: str, user_id: str, conversation_id: str | None = None) -> AsyncIterator[str]:  # 流式聊天接口，逐段输出 SSE
    """
    写在前面：
    整个函数的作用是接住 workflow_executor 吐出来的事件，再转成 SSE 发给前端。
        函数流程：
            1. 创建/复用 conversation
            2. 先把 user 消息落库
            3. 创建 run
            4. 先发 run_started SSE
            5. 调用 WorkflowExecutor.execute()
            6. 把每个 RuntimeEvent 转成 SSE
            7. final 时把 assistant 落库
            8. finish_run()
        也就是说：
            WorkflowExecutor 给它 RuntimeEvent
            chat_stream 把 RuntimeEvent 变成 SSE event

        其中的变量内涵：
            Conversation = 一整段对话
            Message = 对话中的一条消息
            Run = 针对某条用户消息的一次 workflow 执行过程
            RunStep = 这次执行过程里的每一步 trace

        举个例子：
            Conversation A
                Message 1: user: 我的订单 10086 到哪了？
                Run 1: 执行这条用户消息
                    RunStep: start
                    RunStep: rag
                    RunStep: tool_call
                    RunStep: agent
                    RunStep: end
                Message 2: assistant: 订单 10086 已发货...

        而run类中的字段可以这样理解：
            id                 :这次运行的唯一 id。
            app_id             :这次运行属于哪个 App。比如“电商客服 Agent”。
            conversation_id    :这次运行属于哪个会话。
            input_message_id   :这次 run 是由哪条用户消息触发的。
            output_message_id  :这次 run 最终生成了哪条 assistant 消息。
            status             :运行状态，比如默认 running，结束后变成 success，出错后变成 error。
            latency_ms         :这次运行耗时多少毫秒。
            error              :如果运行失败，这里记录错误信息。
            created_at         :这次运行创建时间。

        在 Dify 这类平台里，Run 就是一次执行的 trace 总入口。当前前端的 “Logs / 最近 Runs” 展示的就是这些记录。
    """
    started = perf_counter()  # 记录开始时间
    conversation = get_or_create_conversation(db, app.id, user_id, conversation_id)  # 获取或创建会话
    user_message = add_message(db, conversation.id, "user", query)  # 保存用户消息
    run = create_run(db, app.id, conversation.id, user_message.id)  # 创建 run 记录 （可以理解成一次 workflow 执行记录）

    yield _sse("run_started", {"conversation_id": conversation.id, "run_id": run.id})  # 先告诉前端 run 已开始
    """
    yield 的作用是边做边交卷（return 是一次性交卷）。例如：
        def demo():
            yield 1
            yield 2
            yield 3
    这个函数不会一次性返回 1, 2, 3，而是变成一个生成器，外面可以逐个拿到值。
    """
    enabled_tools = get_enabled_tool_names(db, app.id)  # 取出当前 app 可用工具
    executor = WorkflowExecutor(db, app, run.id)  # 创建 workflow 执行器
    final_sent = False  # 标记是否已经收到了 final 事件
    try:  # try能避免运行时异常直接把流打断
        async for event in executor.execute(query, enabled_tools, conversation.id, user_id):  # 依次接收 workflow 的事件
            if event["type"] == "rag":  # 如果是 RAG 事件，转成 SSE 发给前端
                yield _sse("rag", event)
            elif event["type"] == "tool_call":  # 如果是工具调用事件，转成 SSE 发给前端
                yield _sse("tool_call", event)
            elif event["type"] == "message_delta":  # 如果是模型输出的增量文本，只把增量内容发给前端
                yield _sse("message_delta", {"content": event["content"]})
            elif event["type"] == "workflow_warning":  # 如果是 workflow 警告，转成 SSE 发给前端
                yield _sse("workflow_warning", event)
            elif event["type"] == "workflow_node":  # workflow_node 是节点级执行事件，目前不推送给前端
                # 这里保留显式忽略，是为了说明 WorkflowExecutor 仍会产出完整节点事件；
                # 后期开发后台 run worker、workflow 可视化执行面板时，可以复用这类事件。
                continue
            elif event["type"] == "adapter_error":  # 如果是 agent adapter 错误，把错误发给前端
                final_sent = True
                finish_run(db, run, started, status="error", error=str(event["message"]))
                yield _sse("error", {"message": event["message"], "adapter": event["adapter"]})
                break
            elif event["type"] == "final":  # 如果收到了最终答案，标记 final 已经处理过
                final_sent = True
                output = add_message(  # 把最终答案写入 assistant 消息表
                    db,
                    conversation.id,
                    "assistant",
                    str(event["content"]),
                    {
                        "tool_calls": executor.result.tool_calls,  # 保存工具调用记录
                        "retrieved_chunks": executor.result.retrieved_chunks,  # 保存检索记录
                    },
                )
                finish_run(db, run, started, output_message_id=output.id)  # 完成 run，记录输出消息和耗时
                yield _sse(  # 把 final 结果再发给前端，前端据此结束流式展示
                    "final",
                    {
                        "conversation_id": conversation.id,
                        "run_id": run.id,
                        "answer": event["content"],
                        "tool_calls": executor.result.tool_calls,
                        "retrieved_chunks": executor.result.retrieved_chunks,
                    },
                )

        if not final_sent:  # 如果 workflow 没有主动发 final，说明需要在这里兜底收尾
            output = add_message(  # 仍然把当前累积答案写入 assistant 消息
                db,
                conversation.id,
                "assistant",
                executor.result.answer,
                {
                    "tool_calls": executor.result.tool_calls,
                    "retrieved_chunks": executor.result.retrieved_chunks,
                },
            )
            finish_run(db, run, started, output_message_id=output.id)  # 结束 run
            yield _sse(  # 再发一个 final 给前端
                "final",
                {
                    "conversation_id": conversation.id,
                    "run_id": run.id,
                    "answer": executor.result.answer,
                    "tool_calls": executor.result.tool_calls,
                    "retrieved_chunks": executor.result.retrieved_chunks,
                },
            )
    except Exception as exc:  # 如果运行过程出错
        finish_run(db, run, started, status="error", error=str(exc))  # 把 run 标记为失败
        add_step(db, run.id, "error", "runtime_error", {}, {}, error=str(exc))  # 额外写一条错误 step 方便排查
        yield _sse("error", {"message": str(exc)})  # 把错误消息发给前端


def list_messages(db: Session, conversation_id: str, user_id: str) -> list[Message]:  # 查询某个会话的全部消息
    return list(
        db.scalars(
            select(Message)
            .join(Conversation, Conversation.id == Message.conversation_id)
            .where(Message.conversation_id == conversation_id, Conversation.user_id == user_id)
            .order_by(Message.created_at.asc())
        )
    )  # 按时间正序返回消息列表
