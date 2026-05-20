from fastapi import APIRouter, Depends, HTTPException  # 导入路由器、依赖注入和 HTTP 异常
from fastapi.responses import StreamingResponse  # 导入流式响应对象，用于 SSE 输出
from sqlalchemy.orm import Session  # 导入数据库会话类型

from app.api.dependencies import get_current_user
from app.db.models import User
from app.db.session import get_db  # 导入获取数据库会话的依赖函数
from app.schemas import ChatRequest, ChatResponse  # 导入聊天请求和响应的 schema
from app.services.app_service import get_app  # 导入查询 App 的业务函数
from app.services.chat_service import chat_once, chat_stream, get_conversation_for_user, list_messages  # 导入聊天和消息相关的 service

router = APIRouter(tags=["chat"])  # 创建 chat 路由组，这里不额外加 prefix，具体路径直接写在装饰器里


@router.post("/apps/{app_id}/chat", response_model=ChatResponse | None)  # 定义聊天接口，支持返回完整响应（ChatResponse）或流式响应
async def chat(
    app_id: str,
    payload: ChatRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):  # app_id 来自路径，payload 来自请求体，db 由 FastAPI 注入
    """
    async：把函数标记成 异步函数，告诉 Python：这个函数里面可能会有等待操作，不要阻塞整个程序。
    提高并发性能:
        这个接口会访问数据库、调用 AI 模型、网络请求，这些都是慢 I/O 操作。
        使用 async，服务器在等待时可以处理其他请求，吞吐量更高。
    """
    app = get_app(db, app_id, current_user.id)
    if not app:
        raise HTTPException(status_code=404, detail="App not found")
    if payload.stream:  # 如果前端要求流式输出
        return StreamingResponse(  # 返回一个"流式响应对象"，前端可以边接收边显示
            chat_stream(db, app, payload.query, current_user.id, payload.conversation_id),  # 一个异步生成器，负责一段段吐数据
            media_type="text/event-stream",  # 告诉浏览器这是 SSE 格式(SSE = Server-Sent Events （服务器发送事件）, 就是后端到前端的单向实时推送流。)
        )  # 真正发给前端的内容，是后面由这个"流式响应对象"去不断消费 chat_stream() 产生的 yield
    """
    await：用在异步函数内部，等待一个异步操作完成，同时让出执行权，让程序可以去做别的事。
    """
    try:
        return await chat_once(db, app, payload.query, current_user.id, payload.conversation_id)  # 如果不是流式，就一次性返回完整结果
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/conversations/{conversation_id}/messages")  # 获取某个会话下的消息列表
def messages(
    conversation_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):  # conversation_id 来自路径，db 由 FastAPI 注入
    conversation = get_conversation_for_user(db, conversation_id, current_user.id)
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return list_messages(db, conversation_id, current_user.id)  # 调用 service 层查询会话里的历史消息
