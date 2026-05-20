from fastapi import APIRouter, Depends, HTTPException  # 导入路由器、依赖注入和异常类
from sqlalchemy.orm import Session  # 导入 SQLAlchemy 的数据库会话类型

from app.api.dependencies import get_current_user
from app.db.models import User
from app.db.session import get_db  # 导入获取数据库会话的依赖函数
from app.schemas import AppCreate, AppOut, AppUpdate  # 导入请求和响应的数据结构
from app.services.app_service import create_app, delete_app, get_app, list_apps, update_app  # 导入应用服务层函数

router = APIRouter(prefix="/apps", tags=["apps"])  # 创建 /apps 路由分组

"""
前端点击“创建电商客服 Agent”
-> main.tsx 里的 createDemoApp()
-> api.createApp()
-> POST http://localhost:8000/api/apps
-> body 是 JSON.stringify({...})
-> 后端 FastAPI 自动解析成 payload: AppCreate
-> create_app(db, payload, current_user.id)
"""
@router.post("", response_model=AppOut)  # 创建应用接口，返回应用详情
def create(
    payload: AppCreate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)
):  # 接收创建参数，并注入数据库会话(接口的请求体按 AppCreate 的结构来解析)
    try:
        return create_app(db, payload, current_user.id)  # 调用服务层，把应用写入数据库
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("", response_model=list[AppOut])  # 查询应用列表接口
def list_all(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):  # 注入数据库会话
    return list_apps(db, current_user.id)  # 从数据库中按创建时间倒序取出所有应用


@router.get("/{app_id}", response_model=AppOut)  # 根据 app_id 查询单个应用
def get_one(app_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):  # 接收路径参数并注入数据库会话
    app = get_app(db, app_id, current_user.id)  # 先去数据库里查这个应用是否存在
    if not app:  # 如果查不到返回 404
        raise HTTPException(status_code=404, detail="App not found")
    return app


@router.patch("/{app_id}", response_model=AppOut)  # 局部更新某个应用
def update(
    app_id: str, payload: AppUpdate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)
):  # 接收更新内容和数据库会话
    app = get_app(db, app_id, current_user.id)  # 先查应用
    if not app:  # 如果不存在
        raise HTTPException(status_code=404, detail="App not found")  # 返回 404
    try:
        return update_app(db, app, payload, current_user.id)  # 调用服务层更新并保存
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/{app_id}")  # 删除某个应用
def delete(app_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):  # 接收 app_id 和数据库会话
    app = get_app(db, app_id, current_user.id)  # 先确认应用存在
    if not app:  # 如果不存在
        raise HTTPException(status_code=404, detail="App not found")  # 返回 404
    delete_app(db, app)  # 删除数据库中的应用
    return {"ok": True}  # 返回一个简单的成功结果
