# Todo List Sync Service

从滴答清单 (Dida365) iCal 订阅链接获取待办数据，保存到本地数据库，并同步转发到 Zectrix Cloud 等第三方平台。

## 功能

- **iCal 抓取** — 定时从滴答清单订阅链接拉取待办数据
- **增量同步** — 基于 UID + LAST-MODIFIED 判断变更，只同步有变化的待办
- **本地存储** — SQLite 持久化，记录同步状态
- **可插拔转发** — 通过 Forwarder 接口对接不同平台，首个支持 Zectrix Cloud
- **Web 管理** — 浏览器查看待办、同步日志、配置管理
- **定时 + 手动** — 支持自动轮询和手动触发同步

## 快速开始

### 前置要求

- Docker & Docker Compose

### 启动

```bash
docker-compose up -d --build
```

访问 http://localhost:8100 打开管理页面。

### 首次配置

1. 打开 Web 页面，切换到「配置管理」Tab
2. 填写以下配置：
   - **iCal 订阅地址** — 滴答清单导出的 webcal 地址（已预填）
   - **Zectrix API Key** — 在 Zectrix Cloud 平台获取的 X-API-Key
   - **设备 ID** — Zectrix 平台上的设备 MAC 地址
   - **Zectrix API 地址** — 默认 `https://cloud.zectrix.com`
   - **同步频率** — 自动同步间隔（分钟），默认 5
3. 点击「保存配置」
4. 点击「手动同步」立即触发一次同步

## 同步流程

```
滴答清单 iCal Feed
       │
       ▼
  ┌──────────┐    ┌──────────┐    ┌────────────────┐
  │ ICal      │───▶│ SQLite   │───▶│ ZectrixForwarder│───▶ Zectrix Cloud
  │ Fetcher   │    │ 本地存储  │    │ (可插拔)        │
  └──────────┘    └──────────┘    └────────────────┘
```

1. 抓取 iCal Feed，解析 VTODO 组件
2. 与本地数据库比对（by UID）：
   - 新增 → INSERT
   - 变更（LAST-MODIFIED 更新）→ UPDATE
   - 删除（iCal 中不存在）→ 标记删除
3. 取所有未同步的待办，调用 Forwarder 转发到目标平台
4. 记录同步日志
5. 若开启双向同步，从 Zectrix 拉取远程变更回写到本地数据库

## 双向同步

默认关闭。在配置管理页面开启「双向同步」后，每次同步周期会额外执行反向同步：

1. 轮询 Zectrix `GET /open/v1/todos` 获取远程待办状态
2. 通过 `remote_id` 匹配本地记录
3. 若远程 `updateDate` 变更 → 更新本地 title、description、due_date、due_time、priority、completed
4. 若远程已删除 → 本地标记为已完成

**冲突策略**：远程（Zectrix）优先。iCal 订阅是只读的，无法回写到滴答清单。

**配置项**：
- `bidirectional_enabled` — 设为 `true` 开启

## API 接口

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/todos` | 待办列表（支持 `status`, `page`, `size` 参数） |
| GET | `/api/todos/{uid}` | 待办详情 |
| GET | `/api/logs` | 同步日志 |
| POST | `/api/sync` | 手动触发同步 |
| GET | `/api/config` | 获取配置 |
| PUT | `/api/config` | 更新配置 |

## 项目结构

```
todo-list-trans/
├── app/
│   ├── main.py                  # FastAPI 入口
│   ├── config.py                # 配置常量
│   ├── database.py              # SQLite 连接与初始化
│   ├── models.py                # 数据模型
│   ├── routers/
│   │   ├── todos.py             # 待办查询 API
│   │   ├── config.py            # 配置管理 API
│   │   ├── logs.py              # 同步日志 API
│   │   └── sync.py              # 手动同步 API
│   ├── services/
│   │   ├── fetcher.py           # iCal 抓取与解析
│   │   ├── sync_engine.py      # 增量同步引擎
│   │   └── forwarders/
│   │       ├── base.py          # Forwarder 抽象基类
│   │       └── zectrix.py       # Zectrix Cloud 实现
│   └── scheduler.py             # APScheduler 定时任务
├── frontend/
│   ├── index.html               # Vue 3 + TailwindCSS SPA
│   └── assets/                  # 本地化的前端资源
│       ├── vue.global.prod.js
│       └── tailwindcss.min.js
├── data/                        # SQLite 数据文件（运行时生成）
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
└── openapi.yaml                 # Zectrix Cloud API 文档
```

## 添加新的转发目标

1. 在 `app/services/forwarders/` 下创建新文件，继承 `BaseForwarder`：

```python
from app.services.forwarders.base import BaseForwarder
from app.models import Todo

class MyForwarder(BaseForwarder):
    async def create_todo(self, todo: Todo) -> str:
        # 调用目标平台 API 创建待办
        ...
    async def update_todo(self, remote_id: str, todo: Todo):
        ...
    async def complete_todo(self, remote_id: str):
        ...
    async def delete_todo(self, remote_id: str):
        ...
```

2. 在 `sync_engine.py` 中注册新的 Forwarder
3. 在配置表中添加对应的配置项

## 数据存储

SQLite 数据库文件位于 `data/todos.db`，通过 Docker Volume 持久化。

### 数据表

- **todos** — 待办数据，含 iCal 原始数据和同步状态
- **sync_logs** — 同步日志，记录每次抓取和转发的结果
- **config** — 配置项，键值对存储

## 技术栈

| 组件 | 技术 |
|------|------|
| 后端 | Python 3.12 / FastAPI |
| 数据库 | SQLite (aiosqlite) |
| 定时任务 | APScheduler |
| HTTP 客户端 | httpx (async) |
| iCal 解析 | icalendar |
| 前端 | Vue 3 + TailwindCSS (本地资源) |
| 部署 | Docker / Docker Compose |

## 本地开发（不使用 Docker）

```bash
pip install -r requirements.txt
python -m uvicorn app.main:app --reload --port 8000
```

## 常见问题

**Q: 同步失败怎么办？**
检查同步日志 Tab 中的错误信息，常见原因：iCal 地址失效、API Key 错误、网络不通。

**Q: 如何修改同步频率？**
在 Web 配置页面修改「同步频率」并保存，会立即生效无需重启。

**Q: 数据如何备份？**
备份 `data/todos.db` 文件即可。
