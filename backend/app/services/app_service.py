from copy import deepcopy

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import App, AppTool
from app.schemas import AppCreate, AppUpdate, DEFAULT_WORKFLOW_SPEC
from app.services.model_credential_service import get_model_credential


def _collect_workflow_credential_ids(workflow_spec: dict | None) -> set[str]:
    if not isinstance(workflow_spec, dict):
        return set()

    credential_ids: set[str] = set()
    nodes = workflow_spec.get("nodes", [])
    if not isinstance(nodes, list):
        return credential_ids

    for node in nodes:
        if not isinstance(node, dict):
            continue
        model = node.get("model")
        if not isinstance(model, dict):
            continue
        credential_id = str(model.get("credential_id") or "").strip()
        if credential_id:
            credential_ids.add(credential_id)
    return credential_ids


def _validate_app_credentials(
    db: Session,
    owner_user_id: str,
    model_credential_id: str | None,
    workflow_spec: dict | None,
) -> None:
    credential_ids = {str(model_credential_id or "").strip()} if str(model_credential_id or "").strip() else set()
    credential_ids.update(_collect_workflow_credential_ids(workflow_spec))
    for credential_id in credential_ids:
        if not get_model_credential(db, credential_id, owner_user_id):
            raise ValueError(f"Model credential not found: {credential_id}")


def create_app(db: Session, payload: AppCreate, owner_user_id: str) -> App:  # db 负责数据库交互，payload 负责描述“要创建什么应用”
    workflow_spec = deepcopy(payload.workflow_spec or DEFAULT_WORKFLOW_SPEC)
    _validate_app_credentials(db, owner_user_id, payload.model_credential_id, workflow_spec)
    app = App(  # 先根据 payload 里的字段，组装一个新的 App ORM 对象
        owner_user_id=owner_user_id,
        name=payload.name,  # 应用名称
        description=payload.description,  # 应用描述
        system_prompt=payload.system_prompt,  # 该应用的 system prompt
        model_provider=payload.model_provider,  # 模型提供方，例如 mock
        model_name=payload.model_name,  # 模型名称
        model_credential_id=payload.model_credential_id,  # 模型凭据引用，只保存 id，不保存密钥明文
        model_base_url=payload.model_base_url,  # OpenAI-compatible / vLLM 等模型服务地址
        temperature=payload.temperature,  # 采样温度
        top_p=payload.top_p,  # top_p 参数
        max_tokens=payload.max_tokens,  # 最大输出 token 数
        workflow_spec=workflow_spec,  # 如果没有传 workflow，就使用默认 workflow；这里要 deepcopy，避免多个 App 共享同一份嵌套结构
    )
    db.add(app)  # 把这个 App 对象加入当前数据库会话，准备写入数据库
    db.flush()  # 先把 App 刷到数据库，生成 app.id，后面创建 AppTool 时要用这个 id
    db.add(AppTool(app_id=app.id, tool_name="query_order", enabled=True))  # 默认给新应用绑定一个 query_order 工具
    db.commit()  # 提交事务，把 App 和 AppTool 一起真正写入数据库
    """
    以上做法不是不能一次性做，而是当前写法更清晰、更显式。也可以通过 relationship/cascade 组织成另一种写法。
    """
    db.refresh(app)  # 从数据库重新读取 app，确保拿到最新状态
    return app  # 返回创建好的应用对象


def list_apps(db: Session, owner_user_id: str) -> list[App]:
    return list(db.scalars(select(App).where(App.owner_user_id == owner_user_id).order_by(App.created_at.desc())))


def get_app(db: Session, app_id: str, owner_user_id: str) -> App | None:
    return db.scalar(select(App).where(App.id == app_id, App.owner_user_id == owner_user_id))


def update_app(db: Session, app: App, payload: AppUpdate, owner_user_id: str) -> App:
    next_model_credential_id = payload.model_credential_id if payload.model_credential_id is not None else app.model_credential_id
    next_workflow_spec = payload.workflow_spec if payload.workflow_spec is not None else app.workflow_spec
    _validate_app_credentials(db, owner_user_id, next_model_credential_id, next_workflow_spec)
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(app, key, value)
    db.commit()
    db.refresh(app)
    return app


def delete_app(db: Session, app: App) -> None:
    db.delete(app)
    db.commit()


def set_app_tools(db: Session, app_id: str, tool_names: list[str]) -> list[AppTool]:
    existing = {tool.tool_name: tool for tool in db.scalars(select(AppTool).where(AppTool.app_id == app_id))}
    for tool in existing.values():
        tool.enabled = tool.tool_name in tool_names
    for tool_name in tool_names:
        if tool_name not in existing:
            db.add(AppTool(app_id=app_id, tool_name=tool_name, enabled=True))
    db.commit()
    return list(db.scalars(select(AppTool).where(AppTool.app_id == app_id).order_by(AppTool.tool_name)))


def get_enabled_tool_names(db: Session, app_id: str) -> list[str]:
    rows = db.scalars(
        select(AppTool).where(AppTool.app_id == app_id, AppTool.enabled.is_(True)).order_by(AppTool.tool_name)
    )
    return [row.tool_name for row in rows]
