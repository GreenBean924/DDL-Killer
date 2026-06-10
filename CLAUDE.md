# DDL-Killer

企微智能机器人，接收用户的 DDL 任务消息，按紧急度+难度+重要度评分排序，存储到 PostgreSQL，通过 WebSocket 长连接回复结果。

## 技术栈

| 层 | 选型 |
|---|---|
| Web 框架 | FastAPI (lifespan 管理 Bot 后台任务) |
| ORM | SQLAlchemy 2.0 (Mapped + mapped_column) |
| 数据库 | PostgreSQL (psycopg2-binary 驱动) + pgvector 扩展 |
| 迁移 | Alembic (autogenerate) |
| 企微 SDK | `wecom-aibot-sdk-python` — 长连接模式，非 URL 回调 |
| LLM | DeepSeek API (`openai` SDK) — 自然语言理解 + 意图识别 |
| Embedding | fastembed (`BAAI/bge-small-zh-v1.5`, 512 维) — 免费本地 ONNX |
| 加解密 | `pycryptodome` (WXBizMsgCrypt 协议，已废弃) |

## 目录结构

```
DDL-killer/
├── main.py                    # FastAPI 入口 + lifespan 启动 Bot WS
├── alembic.ini                # Alembic 配置 (script_location 指向 app/database/migrations)
├── .env                       # DB 凭据 + WECOM_BOT_ID + WECOM_BOT_SECRET
├── app/
│   ├── models/
│   │   ├── task.py            # Task ORM (9列，含 chatid 多用户隔离)
│   │   ├── task_file.py       # TaskFile ORM — 文件附件 (含 label 字段)
│   │   ├── conversation.py    # ConversationRecord ORM — 会话 DB 持久化
│   │   └── memory.py          # MemoryFragment ORM — pgvector 向量记忆
│   ├── schemas/task.py        # Pydantic: TaskCreate / TaskUpdate / TaskResponse
│   ├── api/task.py            # Task CRUD 路由 (prefix="/tasks")
│   ├── api/bot.py             # ❌ 废弃 — URL 回调方式，与当前项目无关
│   ├── services/
│   │   ├── risk_score.py      # 风险评分：指数衰减公式，48h 半衰期
│   │   ├── llm_service.py     # ✅ LLM 调用 (DeepSeek Tool Calling，6 tools)
│   │   ├── display.py         # ✅ 共享消息面板格式化（regex 和 LLM 路径共用）
│   │   ├── conversation.py    # ✅ 会话管理 (内存缓存 + DB 双写，重启不丢)
│   │   ├── file_manager.py    # ✅ 文件下载/存储/关联/标签生成/重命名
│   │   ├── task_analyzer.py   # ✅ 任务自动拆解（文件提取 + LLM 分析）
│   │   ├── scheduler.py       # ✅ 定时智能提醒（早晚推送 + LLM 建议）
│   │   ├── short_code.py      # ✅ 任务短编码生成（替代数字 ID）
│   │   ├── embedding_service.py # ✅ Embedding 生成 (fastembed/openai/none 多后端)
│   │   ├── memory_service.py  # ✅ 记忆存储+检索 (pgvector cosine 相似度)
│   │   ├── bot_ws_client.py   # ✅ Bot 客户端（WebSocket + 文件消息处理 + 三层路由）
│   │   ├── bot_notify.py      # ❌ 废弃 — webhook 主动通知，未启用
│   │   └── wxcrypt.py         # ❌ 废弃 — URL 回调的加解密，长连接模式不需要
│   └── database/
│       ├── database.py        # engine + SessionLocal + Base + get_db
│       ├── init_db.py         # 模型导入入口（给 Alembic 注册 metadata）
│       └── migrations/        # Alembic 迁移脚本
├── requirements.txt           # pip freeze 输出
└── venv/                      # Python 虚拟环境
```

## 关键约定

### 数据库迁移

- **永远用 Alembic**，不要手动改表结构
- 新增模型后，在 `app/database/init_db.py` 和 `app/database/migrations/env.py` 中 import 该模型
- 工作流：`alembic revision --autogenerate -m "描述"` → `alembic upgrade head`
- `alembic.ini` 中的 `sqlalchemy.url` 是占位符，实际 URL 由 `env.py` 从 `.env` 读取

### Bot 架构

- 当前使用**长连接模式（WebSocket）**，不是 URL 回调
- `bot_ws_client.py` 是唯一的 Bot 入口，通过 `main.py` lifespan 启动
- SDK 用法：`WSClient(WSClientOptions(bot_id=..., secret=...))` → `client.on("message", handler)` → `client.connect_async()`
- 回复用 `client.reply(frame, body)`，body 格式为 `{"chatid": ..., "msgtype": "markdown", "markdown": {"content": ...}}`
- 主动推送用 `client.send_message(chatid, body)`，body 格式同上，**不需要 frame**
- 发送文件用 `client.reply_media(frame, path)`（回复场景）或 `client.send_media_message(chatid, path)`（主动推送）
- `get_client()` 公开函数供外部模块获取 `_client` 实例（scheduler、task_analyzer 等需要）
- 启动前确保 `.env` 中 `WECOM_BOT_ID` 和 `WECOM_BOT_SECRET` 已配置

### 消息处理流程（三层路由 + 文件关联中转）

```
用户消息 → 1. 正则精确匹配（帮助 / 列表 / 完成 #编码 / 完成 N / 创建 @... 难度N 重要度N / 文件 / 文件 N / 关联 ID ID / 清理已完成）
         → 1.5 文件关联中转：有待关联文件 + 用户发了任务名/短编码 → 自动关联最新文件（不设 conversation 状态）
         → 2. LLM NLU（DeepSeek Tool Calling，覆盖所有自然语言，6 个 tool）
         → 3. 降级（LLM 挂了时用正则命令格式）
```

- 正则只覆盖固定命令格式，范围已缩小（V4：`_is_command_or_task()` 不再拦截 task-related text）
- LLM 是所有自然语言和非明确命令的**主路径**（V4）
- **难度和重要度：用户未提时默认值 5，不追问**（只有用户主动说「很难」「不太重要」才映射数值）
- 多轮对话状态由 `conversation.py` 管理，**按意图分超时**（create_task 10分钟，其他 5分钟），内存缓存 + DB 双写，重启不丢失
- LLM 超时 8 秒，失败自动降级到正则提示
- 支持 `image` / `file` 消息类型：下载解密 → 保存到 `uploads/<chatid>/` → 入文件队列
- 所有 DB 查询按 chatid 过滤，不同用户/群数据隔离
- 记忆检索（PGVector）仅在任务相关消息时触发，避免闲聊延迟
- 任务列表底部展示已完成任务（折叠），完成任务时自动清理附件
- 创建任务/关联文件时自动拆解为子步骤（LLM 分析 + 文件内容提取），fire-and-forget

### 定时智能提醒

- `scheduler.py` — asyncio 后台循环，每 60 秒检查是否到推送时间
- 推送窗口：早 8-9 点、晚 8-9 点，每周期每用户只推一次（内存去重）
- 流程：查所有有 pending 任务的 chatid → 取风险最高的任务 → LLM 生成个性化建议 → `_client.send_message()` 主动推送
- LLM 提示词要求：像朋友聊天、根据紧迫度调整语气、给具体阶段性安排
- `main.py` lifespan 中 `asyncio.create_task(reminder_loop())` 启动
- 失败降级：LLM 超时/报错时用模板文案替代
- 速率控制：每用户间隔 0.5s 避免 API 限流

### 任务自动拆解

- `task_analyzer.py` — 文件内容提取（PDF/DOCX/TXT）+ LLM 分析拆解
- 触发时机：LLM 路径创建任务时、文件关联到任务时（fire-and-forget）
- 流程：提取文件文本（截断 3000 字）→ LLM 输出 JSON 子任务列表 → 格式化推送
- 拆解结果只展示不存储，不建新表，不创建子任务记录
- LLM 输出格式：`{"subtasks": [{"title", "estimated_hours", "difficulty", "order"}, ...]}`
- JSON 解析容错：先 json.loads → 失败则 regex 提取 `{...}` 块 → 再失败则放弃

### 文件上传

- 存储目录：`uploads/<chatid>/unassigned/`（未关联） → `uploads/<chatid>/<task_id>/`（已关联）
- 收到图片/文件自动下载解密，`_generate_label()` 从文件名提取可读标签（内部使用，不展示给用户）
- 关联任务时自动检测乱码文件名，自动用 `{任务标题}_{文件类型}` 重命名
- 命令：`文件` 查看文件面板（按任务分组，只显示文件名），`文件 <任务ID>` 查看附件，`关联 <文件ID> <任务ID>` 手动绑定，`清理已完成` 删除已完成任务的附件（需确认）
- 完成任务时自动删除关联文件
- 用户说「论文文件发我」→ LLM 调用 `send_files` → bot 通过 `reply_media` 逐个发送保存的文件（send 前复制到临时目录用干净名字）
- **V4 文件队列**：`bot_ws_client.py` 中 `_file_queue: dict[str, list[int]]`，per-chatid FIFO 存未关联文件 ID
  - 文件到达时不设 conversation 状态，用户可自由操作其他命令
  - 自然语言「关联到 XX」→ LLM `associate_file` tool 自动关联最新文件
  - 直接回复任务名/短编码 → `handle_message` 快速路径匹配并关联最新文件
  - **队列只在内存**，重启丢失（文件保留在磁盘，可在 DB 中找回）

### LLM Tools（DeepSeek Tool Calling）

共 6 个 tool：`create_task` / `query_tasks` / `complete_task` / `list_files` / `send_files` / `associate_file`
新增 tool 时同步更新 `SYSTEM_PROMPT` 中的意图列表（第 7 条）和 `TOOLS` 列表。

### risk_score 公式

- 指数衰减：`urgency = 100 * (0.5 ^ (hours_left / 48))` — DDL 越近，urg 指数级增长
- `score = difficulty * 3 + importance * 3 + urgency * 0.4`
- 返回值 clamp 到 [0, 100]
- 修改公式后记得同步更新 `bot_ws_client.py` 中的 HELP_TEXT（如果里面有公式说明）

### 任务状态

- `pending` — 待完成，默认值
- `completed` — 已完成
- Task 模型所有字段 NOT NULL，`created_at` 使用 `lambda: datetime.now(timezone.utc).replace(tzinfo=None)` 避免 tz-aware 差异

### 任务短编码（Short Code）

- 每个任务有唯一短编码（`short_code` 字段，VARCHAR(16)，带索引）
- 创建任务时自动生成：`short_code.py` 的 `generate_short_code_sync()` (regex 路径) 或 `generate_short_code()` (LLM 路径)
- 生成策略：主题词映射（数据库→DB, 数据挖掘→DM）+ 动作后缀（实验→LAB, 考试→FINAL）
- 用户可通过短编码引用任务：`完成 #DB-LAB4`、`完成 DB-LAB4`
- 消息面板展示格式：`**标题** #短编码`
- **内部数字 ID 永远不展示给用户**
- 常见映射在 `short_code.py` 的 `_SUBJECT_MAP` 和 `_SUFFIX_MAP` 中维护

### PGVector 记忆系统

- `MemoryFragment` 表：`chatid` + `content`(文本) + `embedding`(Vector(512)) + `memory_type`(task/conversation/completion) + `source_id`
- Embedding 后端：默认 `fastembed`（`BAAI/bge-small-zh-v1.5`，512 维），通过 `.env` 中 `EMBEDDING_PROVIDER` 切换
- 记忆写入：任务创建和完成时 fire-and-forget（`asyncio.create_task`），不阻塞回复
- 记忆检索：仅在 `_is_task_related()` 为 True 时触发，`cosine_distance <=>` 取 top-3 注入 system prompt
- 降级：embedding 失败 → 跳过检索，bot 正常工作
- 检索关键词列表在 `bot_ws_client.py` 的 `_is_task_related()` 中维护

### 消息面板风格

- 所有回复用 `msgtype: "markdown"`，企微支持 `<font color="info|warning|comment">` 颜色标签
- 任务列表：引用块风格，每任务间空一行，只展示 🔴/🟡/🟢 + **标题** + #短编码 + `> 📅 DDL` + 到期状态
- 创建确认：引用块风格，展示短编码 + 标题 + DDL + 难度 + 重要度 + 描述
- 文件面板只显示文件名，不显示 icon 和 file_id
- **内部数字 ID（task.id, task_file.id）永远不展示给用户**，统一用 short_code 或标题引用
- **所有格式化函数集中在 `app/services/display.py`**，`bot_ws_client.py` 和 `llm_service.py` 都从此导入
- 修改面板时只改 `display.py` 一处即可

### 开发常用命令

```bash
# 启动 (先确保没有残留 uvicorn 进程)
uvicorn main:app --reload

# 数据库迁移
alembic revision --autogenerate -m "描述"
alembic upgrade head

# 清理字节码缓存 (代码改了但不生效时用)
rm -rf app/**/__pycache__

# 杀掉残留进程 (Windows)
taskkill //F //PID <pid>

# 安装依赖
pip install -r requirements.txt

# Git
git add -A && git commit -m "描述"
git push origin main
```

### 版本控制

- `.gitignore` 排除：`.env`（含凭据）、`venv/`、`__pycache__/`、`uploads/`（用户文件）
- 永远不要提交 `.env`，模板用 `.env.example`（无真实凭据）
- 新增依赖后运行 `pip freeze > requirements.txt` 更新锁文件

## 废弃文件

以下文件是 URL 回调方案的产物，切换到长连接后不再使用，**不要修改或依赖它们**：
- `app/api/bot.py` — HTTP 回调路由 (GET/POST /api/bot/callback)
- `app/services/wxcrypt.py` — WXBizMsgCrypt 加解密
- `app/services/bot_notify.py` — Webhook 主动通知

## 历史计划文件

- `C:\Users\OUYANG\.claude\plans\sorted-tumbling-wombat.md` — V4 计划（文件队列 + 显示格式 + LLM 路由），注意：**显示格式部分已回退**到 `>` 引用块风格，不要再用 `<font color="comment">` 替代方案

## 已知坑

1. **uvicorn 进程容易留多个** — 启动前检查 `Task Manager` 或 `netstat -ano | findstr :8000`
2. **`__pycache__` 导致旧代码继续执行** — 怀疑代码没生效时先清缓存
3. **企微个人号无法过域名备案** — URL 回调方案不可行，不用再试 ngrok/natapp 等方案
4. **SDK 事件回调必须用 async 函数** — `client.on("message", sync_func)` 会报 `NoneType can't be used in 'await'`
5. **文件队列重启丢失** — `_file_queue` 只存内存，bot 重启后队列清空，已下载的文件变孤儿（文件在磁盘，可在 `list_unassociated()` 找回，但需手动关联）
6. **scheduler 的 `_get_bot_client()` 用私有访问** — 应统一用 `bot_ws_client.get_client()`，当前 scheduler 走 `getattr(bot_ws_client, '_client', None)` 绕过了公开 API
7. **`deepseek-chat` 模型偶尔返回非 JSON 格式的 tool call arguments** — `json.loads` 已做容错，但极端情况会丢参数
8. **`.env` 含真实凭据，已加入 `.gitignore`** — 任何情况下不要把 `.env` 提交到 git

## 待实现

- [x] LLM 自然语言解析（DeepSeek Tool Calling + 多轮会话追问）
- [x] 多用户隔离（Task/TaskFile/Conversation 按 chatid 隔离）
- [x] 文件上传/存储（下载解密 + 自动标签 + 关联任务 + 文件面板）
- [x] PGVector 记忆系统（fastembed embedding + cosine 检索 + system prompt 注入）
- [x] 消息面板优化（企微 markdown 颜色分层 + 统一风格）
- [x] 文件再发送（send_files tool + reply_media）
- [x] 清理已完成任务附件（手动触发 + 二次确认）
- [x] V2：到期状态标记 + 面板精简 + 已完成折叠 + 文件简化 + 完成自动删文件 + 记忆扩展 + 会话超时分级
- [x] V3 定时智能提醒（早晚推送 + LLM 个性化建议 + 主动发送）
- [x] V3 任务自动拆解（文件内容提取 + LLM 分析子步骤 + 创建/关联时触发）
- [x] 任务短编码体系（short_code 替代数字 ID）
- [x] V4 文件队列 + associate_file tool + LLM 灵活路由（regex 范围缩小）
- [ ] 单元测试和集成测试
- [ ] scheduler `_get_bot_client()` 改用 `get_client()` 公开 API
- [ ] 图片 OCR 内容提取（当前只支持 PDF/DOCX/TXT）
- [ ] 文件队列持久化（当前重启丢失，可存 DB）
- [ ] Docker 化部署（Dockerfile + docker-compose.yml，含 PostgreSQL + pgvector）
- [ ] 华为云部署
