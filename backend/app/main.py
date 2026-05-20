from fastapi import FastAPI  # 导入 FastAPI 应用类，用来创建后端服务实例
from fastapi.middleware.cors import CORSMiddleware  # 导入 CORS 中间件，允许前端跨域访问

from app.api.routes import apps, auth, chat, knowledge, model_credentials, runs, tools  # 导入各个功能模块的路由
from app.core.config import get_settings  # 导入读取环境配置的函数
from app.db.session import init_db  # 导入数据库初始化函数


settings = get_settings()  # 读取配置文件和环境变量，生成全局设置对象

app = FastAPI(title="Dify-like API", version="0.1.0")  # 创建 FastAPI 应用实例

# 配置CORS: Cross-Origin Resource Sharing (跨域资源共享), 解决前后端分离时，浏览器阻止接口调用的问题
app.add_middleware(  # 给应用挂上中间件
    CORSMiddleware,  # 这个中间件负责处理跨域请求
    allow_origins=settings.cors_origin_list,  # 允许这些前端地址访问后端
    allow_credentials=True,  # 允许携带 cookie 或其他凭证
    allow_methods=["*"],  # 允许所有 HTTP 方法
    allow_headers=["*"],  # 允许所有请求头
)

app.include_router(apps.router, prefix="/api")  # 注册应用管理相关接口
app.include_router(auth.router, prefix="/api")  # 注册登录注册相关接口
app.include_router(model_credentials.router, prefix="/api")  # 注册模型凭据管理相关接口
app.include_router(tools.router, prefix="/api")  # 注册工具管理相关接口
app.include_router(knowledge.router, prefix="/api")  # 注册知识库相关接口
app.include_router(chat.router, prefix="/api")  # 注册聊天接口
app.include_router(runs.router, prefix="/api")  # 注册运行日志接口


@app.on_event("startup")  # 在应用启动时执行一次
def on_startup() -> None:
    init_db()  # 初始化数据库表结构


@app.get("/health")  # 定义健康检查接口
def health():
    return {"status": "ok"}  # 返回简单的正常状态
