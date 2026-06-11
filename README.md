<div align="center">

# 🎯 DDL-Killer

**智能 DDL 管理助手 — 让截止日期不再失控**

基于企业微信机器人的 AI 任务管理工具，支持自然语言交互、风险评估、复杂任务拆解和智能提醒。

[Python](https://www.python.org/) · [FastAPI](https://fastapi.tiangolo.com/) · [PostgreSQL](https://www.postgresql.org/) · [DeepSeek LLM](https://platform.deepseek.com/) · [Docker](https://www.docker.com/)

</div>

---

## 📖 项目简介

DDL-Killer 是一个面向大学生的智能任务管理助手，通过企业微信机器人为载体，用自然语言交互替代传统的任务管理工具。

**解决的核心问题：** 大学生面对多门课程的 DDL（截止日期），任务散落在聊天记录、邮件、通知中，缺乏统一管理和优先级排序，经常"赶 DDL"而非"管 DDL"。

**核心思路：** 把任务管理嵌入企业微信——学生最常用的协作工具——用 LLM 理解自然语言、用指数衰减模型量化紧迫度、用 Agent 思维自动拆解复杂任务。

## ✨ 核心功能

| 功能 | 说明 |
|------|------|
| 🗣️ 自然语言创建任务 | 直接说"下周交数据库实验报告，难度 7"，LLM 自动解析意图、提取参数 |
| 📊 DDL 风险评分 | 指数衰减模型（48h 半衰期），综合难度 × 重要度 × 紧迫度，自动排序 |
| 🧩 复杂任务自动拆解 | 发送文件 + 任务描述，LLM 分析后输出阶段性子步骤和时间建议 |
| 📁 文件关联管理 | 发送图片/文件自动保存，支持关联到具体任务，完成时自动清理 |
| ⏰ 智能定时提醒 | 早晚推送风险最高的任务 + LLM 生成的个性化建议，像朋友提醒而非系统通知 |
| 🧠 向量记忆系统 | 基于 pgvector 的语义检索，记住历史任务和对话上下文 |
| 🏷️ 短编码引用 | 每个任务自动生成可读编码（如 `DB-LAB4`），替代数字 ID |
| 🐳 Docker 一键部署 | docker-compose 包含 PostgreSQL + pgvector + Bot 服务，开箱即用 |

## 🏗️ 系统架构

```
┌─────────────────────────────────────────────────────────────────┐
│                     企业微信 (用户交互层)                         │
│              用户消息 / 文件 / 图片 / 群事件                      │
└──────────────────────────┬──────────────────────────────────────┘
                           │ WebSocket 长连接
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│                  bot_ws_client.py (消息路由层)                    │
│                                                                  │
│  ┌──────────┐    ┌──────────────┐    ┌───────────────────────┐  │
│  │ 正则匹配  │───▶│ LLM NLU      │───▶│ 降级提示              │  │
│  │ (Tier 1) │    │ (Tier 2)     │    │ (Tier 3)              │  │
│  └──────────┘    └──────────────┘    └───────────────────────┘  │
│       │                │                                        │
│       ▼                ▼                                        │
│  ┌─────────────────────────────┐                                │
│  │     Tool Calling 执行器      │  create_task / query_tasks    │
│  │     (6 个 tools)             │  complete_task / send_files   │
│  │                              │  list_files / associate_file  │
│  └─────────────────────────────┘                                │
└──────────────────────────┬──────────────────────────────────────┘
                           │
          ┌────────────────┼────────────────┐
          ▼                ▼                ▼
┌──────────────┐  ┌──────────────┐  ┌──────────────────┐
│ risk_score   │  │ llm_service  │  │ task_analyzer    │
│ 风险评分引擎  │  │ DeepSeek API │  │ 任务拆解 + 文件  │
└──────┬───────┘  └──────────────┘  └──────────────────┘
       │
       ▼
┌─────────────────────────────────────────────────────────────────┐
│                    PostgreSQL + pgvector                          │
│                                                                  │
│  ┌────────┐  ┌───────────┐  ┌──────────────┐  ┌──────────────┐ │
│  │  task   │  │ task_file │  │ conversation │  │   memory     │ │
│  │ 任务表  │  │ 文件附件  │  │ 会话状态     │  │ 向量记忆     │ │
│  └────────┘  └───────────┘  └──────────────┘  └──────────────┘ │
└─────────────────────────────────────────────────────────────────┘
```

**模块职责：**

- **bot_ws_client** — 消息入口，三层路由（正则 → LLM → 降级），文件队列管理
- **risk_score** — 指数衰减公式，量化 DDL 紧迫度
- **llm_service** — DeepSeek Tool Calling，自然语言意图识别 + 6 个工具执行
- **task_analyzer** — 文件内容提取（PDF/DOCX/TXT）+ LLM 子任务拆解
- **scheduler** — asyncio 后台循环，早晚窗口推送个性化提醒
- **memory_service** — pgvector cosine 相似度检索，注入 LLM 上下文
- **conversation** — 多轮对话状态管理，内存缓存 + DB 双写

## 🛠️ 技术栈

| 层 | 技术 | 用途 |
|---|---|---|
| Web 框架 | FastAPI | REST API + lifespan 管理后台任务 |
| ORM | SQLAlchemy 2.0 | 声明式模型（Mapped + mapped_column） |
| 数据库 | PostgreSQL 16 + pgvector | 关系存储 + 向量检索 |
| 数据库迁移 | Alembic | schema 版本管理，autogenerate |
| LLM | DeepSeek API (OpenAI SDK) | 自然语言理解、Tool Calling、任务拆解 |
| Embedding | fastembed (BAAI/bge-small-zh-v1.5) | 本地 ONNX 向量化，512 维 |
| 企业微信 SDK | wecom-aibot-sdk-python | WebSocket 长连接，消息收发 |
| 容器化 | Docker + docker-compose | 一键部署 PostgreSQL + Bot 服务 |
| 测试 | pytest | 纯函数单元测试 |

## 🚀 快速开始

### 环境要求

- Python 3.12+
- PostgreSQL 16+（需启用 pgvector 扩展）
- Docker & Docker Compose（推荐）

### 1. 克隆仓库

```bash
git clone https://github.com/<your-username>/DDL-Killer.git
cd DDL-Killer
```

### 2. 配置环境变量

```bash
cp .env.example .env
```

编辑 `.env`，填入以下配置：

```env
# 数据库（Docker 部署时 DB_HOST=db，本地运行时 DB_HOST=localhost）
DB_HOST=localhost
DB_PORT=5432
DB_NAME=ddl_killer
DB_USER=postgres
DB_PASSWORD=your_password_here

# 企业微信智能机器人（长连接模式）
WECOM_BOT_ID=your_bot_id_here
WECOM_BOT_SECRET=your_bot_secret_here

# DeepSeek API
DEEPSEEK_API_KEY=sk-your_key_here
```

### 3. Docker 部署（推荐）

```bash
docker compose up -d --build
```

服务启动后：
- Bot 服务：`http://localhost:8000`
- PostgreSQL：`localhost:5432`（含 pgvector 扩展）
- 数据库迁移在容器启动后自动执行

### 4. 本地运行

```bash
# 创建虚拟环境
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# 安装依赖
pip install -r requirements.txt

# 数据库迁移
alembic upgrade head

# 启动服务
uvicorn main:app --reload
```

### 5. 运行测试

```bash
python -m pytest tests/ -v
```

## 💬 使用示例

### 创建任务

```
用户：下周五交数据库实验报告 难度7 重要度8

Bot：✅ 任务已创建
     > **数据库实验报告** #DB-REPORT
     > 📅 DDL: 2026-06-20 23:59
     > 难度: 7 | 重要度: 8
     > 🟢 剩余 9 天
```

### 查看任务列表

```
用户：列表

Bot：📋 任务列表
     > 🔴 **数据库实验报告** #DB-REPORT
     > 📅 DDL: 明天 23:59 — ⚠️ 紧急！
     >
     > 🟡 **数据挖掘大作业** #DM-PROJECT
     > 📅 DDL: 6月25日 — 还有5天
     >
     > 🟢 **英语阅读作业** #ENG-HW
     > 📅 DDL: 7月1日 — 还有11天
```

### 自然语言交互

正则只能识别固定命令格式，自然语言交给 LLM 理解：

```
用户：这周有什么要交的吗？

Bot：这周有 2 个任务要交：
     数据库实验报告明天就截止了，今晚得抓紧；
     数据挖掘大作业周五交，还有 4 天，建议明后天先做完数据预处理部分。

     回复「完成 #编码」或「完成 任务名」
```

```
用户：帮我把数据库实验标记完成

Bot：任务 #DB-REPORT「数据库实验报告」已完成 ✅
```

### 任务自动拆解

```
用户：[发送: 数据挖掘课程设计.pdf]
     这个大作业帮我拆一下

Bot：📋 任务拆解

     > **数据挖掘大作业**
     > 📅 DDL: 2026-06-25 23:59 — 还有5天

     > 1️⃣ 数据预处理与清洗 — 预计 3h — 难度 5
     > 2️⃣ 特征工程与选择 — 预计 4h — 难度 7
     > 3️⃣ 模型训练与调参 — 预计 5h — 难度 8
     > 4️⃣ 实验报告撰写 — 预计 3h — 难度 4

     > 建议分 4 步完成，共需约 16 小时。
```

### 📸 效果截图

| 任务创建 | 任务列表（风险排序） |
|:---:|:---:|
| ![任务创建](docs/screenshots/task-create.png) | ![风险排序](docs/screenshots/task-list.png) |

| 任务自动拆解 | 智能提醒 |
|:---:|:---:|
| ![任务拆解](docs/screenshots/task-decompose.png) | ![智能提醒](docs/screenshots/reminder.png) |

## 📐 风险评分设计

### 为什么用指数衰减？

传统的线性评分无法反映人类对截止日期的感知——**距离 DDL 越近，紧迫感增长越快**，而不是匀速增长。指数衰减模型恰好捕捉了这种非线性关系。

### 公式

```
urgency = 100 × 0.5^(hours_left / 48)

score   = difficulty × 3 + importance × 3 + urgency × 0.4

结果 clamp 到 [0, 100]
```

**关键参数：**
- **48 小时半衰期**：每过 48 小时，紧迫度减半。1 周后紧迫度 ≈ 8.8，1 天后 ≈ 70.7，到期时 = 100
- **难度和重要度**：用户主动指定（1-10），默认 5，不追问
- **权重分配**：difficulty 和 importance 各占 30%，urgency 占 40%

### 设计优势

| 特性 | 说明 |
|------|------|
| 非线性感知 | 48h 内紧迫感急剧上升，符合"赶 DDL"的真实心理 |
| 三维度平衡 | 不只看时间，难度高的任务即使 DDL 远也会排在前面 |
| 可调参数 | 半衰期、权重均可配置，适不同场景 |
| 计算高效 | 纯数学运算，每次排序 O(n log n)，无需 LLM 调用 |

## 🌟 项目亮点

### 工程化结构

分层清晰：API 层 → 服务层 → 模型层 → 数据库层，每个模块职责单一。消息面板格式化集中在 `display.py`，改一处全局生效。

### AI 驱动但不依赖 AI

三层路由设计保证可靠性：正则精确匹配（零延迟）→ LLM 自然语言理解（主路径）→ 降级提示（LLM 挂了也能用）。AI 是增强而非必需。

### Agent 思维

- **Tool Calling**：LLM 不直接操作数据库，而是调用预定义的 6 个 tools，行为可预测、可审计
- **Fire-and-forget**：记忆存储、任务拆解等非关键路径异步执行，不阻塞用户响应
- **状态管理**：多轮对话按意图分级超时，内存 + DB 双写，重启不丢失

### 可扩展性

新增 LLM 工具只需：定义 tool schema → 在 `execute_tool_calls` 中实现逻辑 → 更新 system prompt。新增消息类型只需在 `on_message` 中加一个 elif 分支。

### 数据安全

- 按 `chatid` 隔离，不同用户/群数据完全独立
- `.env` 含凭据但已 `.gitignore`，不会误提交
- 文件完成自动清理，避免磁盘泄漏

## 🗺️ Roadmap

### 近期

- [ ] 图片 OCR 内容提取（当前支持 PDF/DOCX/TXT）
- [ ] 文件队列持久化（当前重启丢失，可存 DB）
- [ ] 单元测试覆盖 LLM 路径和文件管理模块

### 中期：Multi-Agent 架构

- [ ] 多 Agent 协作：任务分析、文件处理、提醒拆分为独立 Agent，通过消息总线协作
- [ ] Agent 权限控制：每个 Agent 只能访问其职责范围内的 tools 和数据
- [ ] Tool Calling 安全：参数校验、速率限制、敏感操作二次确认

### 远期：Agent 安全研究

- [ ] Prompt Injection 防护：检测并拦截试图覆盖系统指令的用户输入
- [ ] 审计日志：记录所有 LLM 调用、tool 执行、数据访问，支持事后回溯
- [ ] 安全监控：异常行为检测（如批量查询、越权访问），自动告警

> 这个方向的延伸源于对 **Multi-Agent Security** 的研究兴趣——当 AI Agent 越来越多地介入真实工作流，如何保证它们的行为可控、可审计、可信赖，是一个值得深入的问题。

## 📁 项目结构

```
DDL-killer/
├── main.py                          # FastAPI 入口 + lifespan 管理
├── alembic.ini                      # Alembic 迁移配置
├── Dockerfile                       # 容器镜像（Python 3.12-slim）
├── docker-compose.yml               # PostgreSQL + Bot 服务编排
├── .env.example                     # 环境变量模板
├── requirements.txt                 # Python 依赖锁文件
├── app/
│   ├── models/                      # SQLAlchemy ORM 模型
│   │   ├── task.py                  # Task（9 列，含 chatid 隔离）
│   │   ├── task_file.py             # TaskFile（文件附件）
│   │   ├── conversation.py          # ConversationRecord（会话状态）
│   │   └── memory.py                # MemoryFragment（pgvector 向量）
│   ├── schemas/task.py              # Pydantic 请求/响应模型
│   ├── api/task.py                  # Task CRUD REST 路由
│   ├── services/
│   │   ├── bot_ws_client.py         # WebSocket 消息路由（三层）
│   │   ├── llm_service.py           # DeepSeek Tool Calling（6 tools）
│   │   ├── risk_score.py            # 风险评分引擎
│   │   ├── scheduler.py             # 定时智能提醒
│   │   ├── task_analyzer.py         # 任务自动拆解
│   │   ├── conversation.py          # 多轮对话管理
│   │   ├── memory_service.py        # 向量记忆检索
│   │   ├── file_manager.py          # 文件下载/存储/关联
│   │   ├── short_code.py            # 短编码生成
│   │   ├── embedding_service.py     # Embedding 多后端
│   │   └── display.py               # 消息面板格式化
│   └── database/
│       ├── database.py              # 引擎 + 会话工厂
│       ├── init_db.py               # 模型注册
│       └── migrations/              # Alembic 迁移脚本
└── tests/                           # 单元测试
    ├── conftest.py                  # 外部依赖 mock
    ├── test_risk_score.py           # 风险评分公式
    ├── test_short_code.py           # 短编码生成
    └── test_bot_parsers.py          # 正则解析器
```

## 📄 License

MIT

---

<div align="center">

**如果这个项目对你有启发，欢迎 ⭐ Star**

</div>
