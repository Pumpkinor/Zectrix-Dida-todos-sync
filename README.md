# Todo List Sync Service

滴答清单 (Dida365) ↔ Zectrix 墨水屏 待办双向同步服务。

## 功能

- **双向同步** — 滴答清单 ↔ Zectrix 任务、完成状态双向同步
- **MCP API** — 通过 Dida365 MCP API 获取精确的任务状态（支持完成/未完成）
- **多项目选择** — 支持选择多个滴答清单项目进行同步
- **增量同步** — 基于 UID + LAST-MODIFIED 判断变更，只同步有变化的数据
- **本地存储** — SQLite 持久化，记录同步状态与来源追踪
- **Web 管理** — 浏览器查看待办列表、同步日志、配置管理、数据清理
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
2. 配置 Zectrix 连接：
   - **API Key** — Zectrix Cloud 平台的 X-API-Key
   - **设备 ID** — 设备 MAC 地址
   - **API 地址** — 默认 `https://cloud.zectrix.com`
3. 配置数据来源（滴答清单 → Zectrix）：
   - 选择 **MCP API** 方式（推荐）
   - 填写 **API 口令**（在滴答清单「设置 → 账户与安全 → API 口令」获取）
   - 点击「获取项目」加载项目列表，勾选需要同步的项目
4. 配置反向同步（Zectrix → 滴答清单）：
   - 选择 **MCP API** 方式（推荐）
   - Zectrix 上完成的任务会自动标记为已完成到滴答清单
   - Zectrix 上新建的任务会自动创建到滴答清单
5. 设置**同步频率**（分钟），默认 5
6. 点击「保存配置」→「手动同步」

## 同步流程

```
┌───────────┐                  ┌──────────┐                  ┌───────────┐
│  滴答清单   │◄──── MCP API ───▶│  SQLite  │◄─── REST API ───▶│  Zectrix  │
│ (Dida365)  │    (双向同步)      │  本地存储  │    (双向同步)      │   Cloud   │
└───────────┘                  └──────────┘                  └───────────┘
```

### 正向同步（滴答清单 → Zectrix）

1. 通过 MCP API 获取选中项目的未完成 + 已完成任务
2. 按 `projectId` 过滤，只保留选中项目的任务
3. 与本地数据库比对（by UID），新增/更新入库
4. 未同步的任务转发到 Zectrix（活跃任务创建，已完成任务创建后标记完成）

### 反向同步（Zectrix → 滴答清单）

5. 从 Zectrix 获取所有待办，与本地通过 `remote_id` 匹配
6. 检测远程变更 → 更新本地记录
7. 检测远程删除 → 本地标记为已完成
8. 导入 Zectrix 新建的待办到本地
9. Zectrix 上完成的任务 → 通过 MCP API 标记滴答清单对应任务为已完成
10. Zectrix 上新建的任务 → 通过 MCP API 在滴答清单创建

## 同步方式

### 滴答清单 → Zectrix（数据来源）

| 方式 | 说明 |
|------|------|
| **MCP API**（推荐） | 精确的任务状态，支持完成/未完成区分，支持多项目选择 |
| iCal 订阅 | 只能获取活跃任务，无法区分完成状态 |

### Zectrix → 滴答清单（反向同步）

| 方式 | 说明 |
|------|------|
| **MCP API**（推荐） | 通过 MCP API 回写完成状态和创建任务 |
| iCal Feed | 生成 iCal 订阅链接供滴答清单导入 |
| 邮件 | 通过 SMTP 发送任务到滴答清单 |
| 关闭 | 不进行反向同步 |

## API 接口

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/todos` | 待办列表（`status`, `page`, `size` 参数） |
| GET | `/api/todos/{uid}` | 待办详情 |
| DELETE | `/api/todos` | 清空本地待办记录 |
| DELETE | `/api/todos/dida-project` | 清空滴答清单选中项目任务 |
| DELETE | `/api/todos/zectrix` | 清空 Zectrix 所有任务 |
| GET | `/api/logs` | 同步日志 |
| DELETE | `/api/logs` | 清空同步日志 |
| POST | `/api/sync` | 手动触发同步 |
| GET | `/api/config` | 获取配置 |
| PUT | `/api/config` | 更新配置 |
| GET | `/api/dida/projects` | 获取滴答清单项目列表 |

## 项目结构

```
todo-list-trans/
├── app/
│   ├── main.py                  # FastAPI 入口
│   ├── config.py                # 配置常量
│   ├── database.py              # SQLite 连接与初始化
│   ├── models.py                # 数据模型
│   ├── scheduler.py             # APScheduler 定时任务
│   ├── routers/
│   │   ├── todos.py             # 待办 CRUD + 数据清理 API
│   │   ├── config.py            # 配置管理 API
│   │   ├── logs.py              # 同步日志 API
│   │   ├── sync.py              # 手动同步 API
│   │   ├── dida.py              # 滴答清单项目列表 API
│   │   └── feed.py              # iCal Feed 导出
│   └── services/
│       ├── fetcher.py           # iCal + MCP 数据抓取与解析
│       ├── sync_engine.py       # 增量同步引擎（正向 + 反向）
│       ├── dida_client.py       # Dida365 MCP API 客户端
│       ├── email_sender.py      # 邮件发送服务
│       └── forwarders/
│           ├── base.py          # Forwarder 抽象基类
│           └── zectrix.py       # Zectrix Cloud 实现
├── frontend/
│   ├── index.html               # Vue 3 + TailwindCSS SPA
│   └── assets/                  # 本地化的前端资源
├── data/                        # SQLite 数据文件（运行时生成）
├── Dockerfile
├── docker-compose.yml
└── requirements.txt
```

## 配置项

| Key | 默认值 | 说明 |
|-----|--------|------|
| `dida_sync_mode` | `mcp` | 数据来源方式：`mcp` / `ical` |
| `dida_mcp_token` | 空 | Dida365 MCP API 口令 |
| `dida_project_id` | 空 | 逗号分隔的项目 ID（空=全部） |
| `reverse_sync_mode` | `mcp` | 反向同步方式：`mcp` / `feed` / `email` / `none` |
| `zectrix_api_key` | 空 | Zectrix Cloud API Key |
| `zectrix_device_id` | 空 | 设备 MAC 地址 |
| `zectrix_base_url` | `https://cloud.zectrix.com` | Zectrix API 地址 |
| `sync_interval_minutes` | `5` | 自动同步间隔（分钟） |
| `ical_url` | 空 | iCal 订阅地址（ical 模式使用） |

## 技术栈

| 组件 | 技术 |
|------|------|
| 后端 | Python 3.12 / FastAPI |
| 数据库 | SQLite (aiosqlite) |
| 定时任务 | APScheduler |
| HTTP 客户端 | httpx (async) |
| MCP 通信 | JSON-RPC over HTTP |
| 前端 | Vue 3 + TailwindCSS (本地资源) |
| 部署 | Docker / Docker Compose |

## 本地开发

```bash
pip install -r requirements.txt
python -m uvicorn app.main:app --reload --port 8000
```
