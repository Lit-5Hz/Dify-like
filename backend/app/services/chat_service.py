import json
from collections.abc import AsyncIterator
from time import perf_counter

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import App, Conversation, Message, Workflow, WorkflowVersion
from app.runtime.workflow_executor import WorkflowExecutor
from app.services.run_log_service import add_step, create_run, finish_run


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


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
    user_message = add_message(db, conversation.id, "user", query)
    run = create_run(db, app.id, workflow.id, workflow_version.id, conversation.id, user_message.id)

    executor = WorkflowExecutor(db, app, workflow_version.spec_json, run.id)
    adapter_error = ""
    try:
        async for event in executor.execute(query, conversation.id, user_id):
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

    output = add_message(
        db,
        conversation.id,
        "assistant",
        executor.result.answer,
        {
            "tool_calls": executor.result.tool_calls,
            "retrieved_chunks": executor.result.retrieved_chunks,
        },
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
    executor = WorkflowExecutor(db, app, workflow_version.spec_json, run.id)
    final_sent = False
    try:
        async for event in executor.execute(query, conversation.id, user_id):
            if event["type"] == "retrieval":
                yield _sse("retrieval", event)
            elif event["type"] == "tool_call":
                yield _sse("tool_call", event)
            elif event["type"] == "message_delta":
                yield _sse("message_delta", {"content": event["content"]})
            elif event["type"] == "workflow_warning":
                yield _sse("workflow_warning", event)
            elif event["type"] == "workflow_node":
                continue
            elif event["type"] == "adapter_error":
                final_sent = True
                finish_run(db, run, started, status="error", error=str(event["message"]))
                yield _sse("error", {"message": event["message"], "adapter": event["adapter"]})
                break
            elif event["type"] == "final":
                final_sent = True
                output = add_message(
                    db,
                    conversation.id,
                    "assistant",
                    str(event["content"]),
                    {
                        "tool_calls": executor.result.tool_calls,
                        "retrieved_chunks": executor.result.retrieved_chunks,
                    },
                )
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

        if not final_sent:
            output = add_message(
                db,
                conversation.id,
                "assistant",
                executor.result.answer,
                {
                    "tool_calls": executor.result.tool_calls,
                    "retrieved_chunks": executor.result.retrieved_chunks,
                },
            )
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
        finish_run(db, run, started, status="error", error=str(exc))
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
