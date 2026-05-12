"""
FastAPI 应用入口模块

这个文件是整个 Web 服务的入口点。它负责：
  1. 创建 FastAPI 应用实例
  2. 注册所有 API 路由（Router）
  3. 挂载前端静态文件
  4. 配置应用启动和关闭时的生命周期事件

【Python / FastAPI 知识点】
  - FastAPI 是一个现代、高性能的 Python Web 框架，类似于 Java 的 Spring Boot 或 Node.js 的 Express。
    它基于 ASGI（异步服务器网关接口）标准，天生支持异步处理。

  - @app.get("/") 是路由装饰器，将一个函数绑定到 HTTP GET 请求的 "/" 路径。
    类似于 Spring 的 @GetMapping("/")。

  - lifespan 是 FastAPI 的生命周期管理机制：
    yield 之前的代码在应用启动时执行，yield 之后的代码在应用关闭时执行。
    类似于 Spring 的 @PostConstruct / @PreDestroy。
"""

# 导入 Python 标准库的日志模块
import logging
# 导入上下文管理器工具（用于 lifespan 函数）
from contextlib import asynccontextmanager

# 导入 FastAPI 框架核心类
from fastapi import FastAPI
# 导入静态文件服务（用于托管前端 JS/CSS 文件）
from fastapi.staticfiles import StaticFiles
# 导入文件响应类（用于返回 HTML 文件）
from fastapi.responses import FileResponse

# 导入项目内部模块
from app.config import FRONTEND_DIR          # 前端文件目录路径
from app.database import init_db             # 数据库初始化函数
from app.scheduler import start_scheduler    # 定时任务启动函数
# 导入所有 API 路由模块
from app.routers import todos, config, logs, sync, feed, dida

# 配置全局日志格式
# %(asctime)s  = 时间戳
# %(levelname)s = 日志级别（INFO/WARNING/ERROR）
# %(name)s     = 日志记录器的名称（通常是模块名）
# %(message)s  = 日志消息内容
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    应用生命周期管理器。

    【Python 知识点】
      @asynccontextmanager 是一个装饰器，将一个包含 yield 的异步函数
      转换为异步上下文管理器。

      yield 之前 = 启动阶段（startup）
      yield 之后 = 关闭阶段（shutdown）
    """
    # ─── 启动阶段 ───
    await init_db()          # 初始化数据库（建表 + 默认配置 + 迁移）
    await start_scheduler()  # 启动定时同步调度器
    yield  # 在这里应用开始接收请求
    # ─── 关闭阶段 ───
    # （当前没有需要清理的资源）


# 创建 FastAPI 应用实例
# title 参数会显示在自动生成的 API 文档页面上（访问 /docs 可查看）
app = FastAPI(title="Todo List Sync Service", lifespan=lifespan)

# ─── 注册 API 路由 ──────────────────────────────────────────────────
# 每个 Router 负责一组相关的 API 端点。
# include_router 相当于把这个 Router 中定义的所有路由注册到主应用上。

app.include_router(todos.router)   # /api/todos — 待办任务的增删查
app.include_router(config.router)  # /api/config — 配置的读写
app.include_router(logs.router)    # /api/logs — 同步日志的查询和清空
app.include_router(sync.router)    # /api/sync — 手动触发同步
app.include_router(feed.router)    # /feed/{token}.ics — iCal 订阅源
app.include_router(dida.router)    # /api/dida/projects — 滴答清单项目列表

# ─── 挂载前端静态文件 ────────────────────────────────────────────────
# 将 frontend/assets 目录映射到 /assets URL 路径
# 这样前端 HTML 中的 <script src="/assets/vue.global.prod.js"> 就能正确加载
app.mount("/assets", StaticFiles(directory=f"{FRONTEND_DIR}/assets"), name="assets")


@app.get("/")
async def serve_frontend():
    """
    根路径处理器：返回前端 SPA 的 HTML 页面。

    当用户在浏览器中访问 http://localhost:8100/ 时，
    FastAPI 返回 frontend/index.html 文件。
    浏览器加载 HTML 后，其中的 Vue.js 代码会通过 AJAX 调用 /api/* 接口获取数据。
    """
    return FileResponse(f"{FRONTEND_DIR}/index.html")
