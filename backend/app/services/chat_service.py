import json
from collections.abc import AsyncIterator
from time import perf_counter
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import App, Conversation, Message, Workflow, WorkflowVersion
from app.runtime.workflow_executor import WorkflowExecutor
from app.services.run_log_service import add_step, create_run, finish_run


MAX_AGENT_HISTORY_MESSAGES = 20


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _ensure_generation(timeline: list[dict[str, Any]], message_id: str) -> dict[str, Any]:
    for item in timeline:
        if item.get("kind") == "generation" and item.get("message_id") == message_id:
            return item
    generation = {
        "id": f"generation-{message_id}",
        "kind": "generation",
        "message_id": message_id,
        "phase": "resume" if any(item.get("kind") == "generation" for item in timeline) else "start",
        "thinking": "",
    }
    timeline.append(generation)
    return generation


def _record_timeline_event(timeline: list[dict[str, Any]], event: dict[str, Any]) -> None:
    event_type = event.get("type")
    if event_type == "retrieval":
        timeline.append(
            {
                "id": f"retrieval-{len(timeline)}",
                "kind": "retrieval",
                "chunks": event.get("chunks") if isinstance(event.get("chunks"), list) else [],
            }
        )
    elif event_type == "thinking_delta":
        generation = _ensure_generation(timeline, str(event.get("message_id") or ""))
        generation["thinking"] = str(generation.get("thinking") or "") + str(event.get("content") or "")
    elif event_type == "message_delta":
        _ensure_generation(timeline, str(event.get("message_id") or ""))
    elif event_type == "tool_call":
        _ensure_generation(timeline, str(event.get("message_id") or ""))
        tool_call_id = str(event.get("tool_call_id") or "")
        if not any(item.get("kind") == "tool" and item.get("tool_call_id") == tool_call_id for item in timeline):
            timeline.append(
                {
                    "id": f"tool-{tool_call_id}",
                    "kind": "tool",
                    "tool_call_id": tool_call_id,
                    "name": str(event.get("name") or "unknown"),
                    "input": event.get("input") if isinstance(event.get("input"), dict) else {},
                    "status": "running",
                }
            )
    elif event_type == "tool_result":
        tool_call_id = str(event.get("tool_call_id") or "")
        for item in reversed(timeline):
            if item.get("kind") == "tool" and item.get("tool_call_id") == tool_call_id:
                item["output"] = event.get("output")
                item["status"] = "completed"
                break
    elif event_type == "workflow_warning":
        timeline.append(
            {
                "id": f"warning-{len(timeline)}",
                "kind": "notice",
                "level": "warning",
                "message": str(event.get("message") or "工作流警告"),
            }
        )


def _append_error(timeline: list[dict[str, Any]], message: str) -> None:
    timeline.append(
        {
            "id": f"error-{len(timeline)}",
            "kind": "notice",
            "level": "error",
            "message": message,
        }
    )


def _assistant_metadata(
    status: str,
    timeline: list[dict[str, Any]],
    executor: WorkflowExecutor,
) -> dict[str, Any]:
    return {
        "status": status,
        "timeline": timeline,
        "tool_calls": executor.result.tool_calls,
        "retrieved_chunks": executor.result.retrieved_chunks,
    }


def get_conversation_for_user(db: Session, conversation_id: str, user_id: str) -> Conversation | None:
    return db.scalar(select(Conversation).where(Conversation.id == conversation_id, Conversation.user_id == user_id))


def get_or_create_conversation(
    db: Session,
    app_id: str,
    workflow_id: str,
    user_id: str,
    conversation_id: str | None,
) -> Conversation:
    if conversation_id:
        conversation = db.get(Conversation, conversation_id)
        if (
            conversation
            and conversation.app_id == app_id
            and conversation.workflow_id == workflow_id
            and conversation.user_id == user_id
        ):
            return conversation
    conversation = Conversation(app_id=app_id, workflow_id=workflow_id, user_id=user_id)
    db.add(conversation)
    db.commit()
    db.refresh(conversation)
    return conversation


def add_message(db: Session, conversation_id: str, role: str, content: str, metadata_json: dict | None = None) -> Message:
    message = Message(
        conversation_id=conversation_id,
        role=role,
        content=content,
        metadata_json=metadata_json or {},
    )
    db.add(message)
    db.commit()
    db.refresh(message)
    return message


async def chat_once(
    db: Session,
    app: App,
    workflow: Workflow,
    workflow_version: WorkflowVersion,
    query: str,
    user_id: str,
    conversation_id: str | None = None,
) -> dict:
    started = perf_counter()
    conversation = get_or_create_conversation(db, app.id, workflow.id, user_id, conversation_id)
    history_messages = [
        {"role": message.role, "content": message.content}
        for message in list_messages(db, conversation.id, user_id)[-MAX_AGENT_HISTORY_MESSAGES:]
    ]
    user_message = add_message(db, conversation.id, "user", query)
    run = create_run(db, app.id, workflow.id, workflow_version.id, conversation.id, user_message.id)

    executor = WorkflowExecutor(db, app, workflow_version.spec_json, run.id, workflow.id)
    adapter_error = ""
    timeline: list[dict[str, Any]] = []
    partial_answer = ""
    try:
        async for event in executor.execute(query, conversation.id, user_id, history_messages=history_messages):
            _record_timeline_event(timeline, event)
            if event["type"] == "message_delta":
                partial_answer += str(event.get("content") or "")
            if event["type"] == "adapter_error":
                adapter_error = str(event.get("message", "Agent adapter error"))
                break
    except Exception as exc:
        error = str(exc)
        _append_error(timeline, error)
        output = add_message(
            db,
            conversation.id,
            "assistant",
            partial_answer,
            _assistant_metadata("error", timeline, executor),
        )
        finish_run(db, run, started, status="error", output_message_id=output.id, error=error)
        add_step(db, run.id, "error", "runtime_error", {}, {}, error=str(exc))
        raise

    if adapter_error:
        _append_error(timeline, adapter_error)
        output = add_message(
            db,
            conversation.id,
            "assistant",
            partial_answer,
            _assistant_metadata("error", timeline, executor),
        )
        finish_run(db, run, started, status="error", output_message_id=output.id, error=adapter_error)
        raise ValueError(adapter_error)

    output = add_message(
        db,
        conversation.id,
        "assistant",
        executor.result.answer,
        _assistant_metadata("completed", timeline, executor),
    )
    finish_run(db, run, started, output_message_id=output.id)
    return {
        "conversation_id": conversation.id,
        "run_id": run.id,
        "answer": executor.result.answer,
        "tool_calls": executor.result.tool_calls,
        "retrieved_chunks": executor.result.retrieved_chunks,
    }


async def chat_stream(
    db: Session,
    app: App,
    workflow: Workflow,
    workflow_version: WorkflowVersion,
    query: str,
    user_id: str,
    conversation_id: str | None = None,
) -> AsyncIterator[str]:
    started = perf_counter()
    conversation = get_or_create_conversation(db, app.id, workflow.id, user_id, conversation_id)
    history_messages = [
        {"role": message.role, "content": message.content}
        for message in list_messages(db, conversation.id, user_id)[-MAX_AGENT_HISTORY_MESSAGES:]
    ]
    user_message = add_message(db, conversation.id, "user", query)
    run = create_run(db, app.id, workflow.id, workflow_version.id, conversation.id, user_message.id)

    yield _sse(
        "run_started",
        {
            "conversation_id": conversation.id,
            "run_id": run.id,
            "workflow_id": workflow.id,
            "workflow_version_id": workflow_version.id,
        },
    )
    executor = WorkflowExecutor(db, app, workflow_version.spec_json, run.id, workflow.id)
    timeline: list[dict[str, Any]] = []
    partial_answer = ""
    output_saved = False
    try:
        async for event in executor.execute(query, conversation.id, user_id, history_messages=history_messages):
            _record_timeline_event(timeline, event)
            if event["type"] == "message_delta":
                partial_answer += str(event.get("content") or "")
            if event["type"] == "retrieval":
                yield _sse("retrieval", event)
            elif event["type"] == "tool_call":
                yield _sse("tool_call", event)
            elif event["type"] == "tool_result":
                yield _sse("tool_result", event)
            elif event["type"] == "thinking_delta":
                yield _sse(
                    "thinking_delta",
                    {"message_id": event["message_id"], "content": event["content"]},
                )
            elif event["type"] == "message_delta":
                yield _sse(
                    "message_delta",
                    {"message_id": event["message_id"], "content": event["content"]},
                )
            elif event["type"] == "workflow_warning":
                yield _sse("workflow_warning", event)
            elif event["type"] == "workflow_node":
                continue
            elif event["type"] == "adapter_error":
                error = str(event["message"])
                _append_error(timeline, error)
                output = add_message(
                    db,
                    conversation.id,
                    "assistant",
                    partial_answer,
                    _assistant_metadata("error", timeline, executor),
                )
                output_saved = True
                finish_run(db, run, started, status="error", output_message_id=output.id, error=error)
                yield _sse("error", {"message": event["message"], "adapter": event["adapter"]})
                break
            elif event["type"] == "final":
                if not output_saved:
                    output = add_message(
                        db,
                        conversation.id,
                        "assistant",
                        str(event["content"]),
                        _assistant_metadata("completed", timeline, executor),
                    )
                    output_saved = True
                    finish_run(db, run, started, output_message_id=output.id)
                yield _sse(
                    "final",
                    {
                        "conversation_id": conversation.id,
                        "run_id": run.id,
                        "answer": event["content"],
                        "tool_calls": executor.result.tool_calls,
                        "retrieved_chunks": executor.result.retrieved_chunks,
                    },
                )

        if not output_saved:
            output = add_message(
                db,
                conversation.id,
                "assistant",
                executor.result.answer,
                _assistant_metadata("completed", timeline, executor),
            )
            output_saved = True
            finish_run(db, run, started, output_message_id=output.id)
            yield _sse(
                "final",
                {
                    "conversation_id": conversation.id,
                    "run_id": run.id,
                    "answer": executor.result.answer,
                    "tool_calls": executor.result.tool_calls,
                    "retrieved_chunks": executor.result.retrieved_chunks,
                },
            )
    except Exception as exc:
        error = str(exc)
        if not output_saved:
            _append_error(timeline, error)
            output = add_message(
                db,
                conversation.id,
                "assistant",
                partial_answer,
                _assistant_metadata("error", timeline, executor),
            )
            output_saved = True
            finish_run(db, run, started, status="error", output_message_id=output.id, error=error)
        add_step(db, run.id, "error", "runtime_error", {}, {}, error=str(exc))
        yield _sse("error", {"message": str(exc)})


def list_messages(db: Session, conversation_id: str, user_id: str) -> list[Message]:
    return list(
        db.scalars(
            select(Message)
            .join(Conversation, Conversation.id == Message.conversation_id)
            .where(Message.conversation_id == conversation_id, Conversation.user_id == user_id)
            .order_by(Message.created_at.asc())
        )
    )
