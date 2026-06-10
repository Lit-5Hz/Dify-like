# Setup Guide

这个文档专门记录 Windows 本地开发需要你手动操作的步骤，尤其是 Docker、数据库连接和启动命令。

## 1. 进入项目目录

```powershell
cd F:\my_folder\Work\LLM\Project\Dify-like
```

## 2. 启动数据库

本项目的 MVP 使用 Docker 启动 PostgreSQL + Redis：

```powershell
docker compose up -d
```

启动后检查状态：

```powershell
docker compose ps
```

默认服务：

```text
PostgreSQL: localhost:5432
Redis:      localhost:6379
```

## 3. 数据库连接信息

默认 PostgreSQL 配置：

```text
host: localhost
port: 5432
database: dify_like
user: postgres
password: postgres
```

后端使用的连接串：

```text
postgresql+psycopg://postgres:postgres@localhost:5432/dify_like
```

Redis 连接串：

```text
redis://localhost:6379/0
```

## 4. 配置环境变量

复制根目录的 `.env.example` 为 `.env`：

```powershell
Copy-Item .env.example .env
```

如果你修改了数据库密码、端口或库名，需要同步修改 `.env` 里的 `DATABASE_URL`。

## 5. 清理数据

只停止容器：

```powershell
docker compose down
```

停止容器并删除数据库数据：

```powershell
docker compose down -v
```

注意：`-v` 会删除 PostgreSQL volume，之前的应用、文档和日志都会丢失。

## 6. 后端启动方式

进入后端目录、激活虚拟环境、安装依赖、启动后端：

```powershell
cd F:\my_folder\Work\LLM\Project\Dify-like\backend
conda activate dify
python -m pip install -e .
python -m uvicorn app.main:app --reload --port 8000
```

```powershell
cd F:\my_folder\Work\LLM\Project\Dify-like\backend
conda activate dify
python -m uvicorn app.main:app --reload --port 8000
```


启动 Celery worker：

```
cd F:\my_folder\Work\LLM\Project\Dify-like\backend
conda activate dify
celery -A app.worker.celery_app worker --loglevel=info --pool=solo
```

健康检查：

```text
http://localhost:8000/health
```

API 文档：

```text
http://localhost:8000/docs
```

## 7. 前端启动方式

进入前端目录、安装依赖、启动前端：

```powershell
cd F:\my_folder\Work\LLM\Project\Dify-like\frontend
npm install
npm run dev
```

```powershell
cd F:\my_folder\Work\LLM\Project\Dify-like\frontend
npm run dev
```

默认访问：

```text
http://localhost:5173
```
