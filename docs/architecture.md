# DDL-Killer 架构设计文档

## 整体架构

DDL-Killer 采用**单进程异步架构**，FastAPI 作为宿主进程，通过 lifespan 管理 Bot WebSocket 连接和定时任务调度器两个后台协程。

```
┌─────────────────────────────────────────────────────────────┐
│                    FastAPI 宿主进程                          │
│                                                              │
│  ┌──────────────────┐  ┌──────────────────┐                 │
│  │   REST API       │  │   Lifespan       │                 │
│  │   /tasks CRUD    │  │   管理后台任务    │                 │
│  └──────────────────┘  └────────┬─────────┘                 │
│                                 │                            │
│         ┌───────────────────────┼────────────────────┐      │
│         ▼                       ▼                    │      │
│  ┌──────────────┐    ┌──────────────────┐           │      │
│  │  Bot WS      │    │  Scheduler       │           │      │
│  │  长连接客户端 │    │  定时提醒循环     │           │      │
│  │              │    │                  │           │      │
│  │  on_message  │    │  每 60s 检查     │           │      │
│  │  on_event    │    │  早晚窗口推送     │           │      │
│  └──────┬───────┘    └────────┬─────────┘           │      │
│         │                     │                     │      │
│         └──────────┬──────────┘                     │      │
│                    ▼                                │      │
│         ┌──────────────────┐                        │      │
│         │   服务层          │                        │      │
│         │                  │                        │      │
│         │  risk_score      │ 风险评分               │      │
│         │  llm_service     │ LLM 调用 + Tool Exec  │      │
│         │  task_analyzer   │ 任务拆解               │      │
│         │  conversation    │ 对话状态               │      │
│         │  memory_service  │ 向量记忆               │      │
│         │  file_manager    │ 文件管理               │      │
│         │  short_code      │ 短编码                 │      │
│         │  display         │ 消息格式化             │      │
│         └────────┬─────────┘                        │      │
│                  ▼                                  │      │
│         ┌──────────────────┐                        │      │
│         │   数据层          │                        │      │
│         │                  │                        │      │
│         │  SQLAlchemy ORM  │ ──▶ PostgreSQL         │      │
│         │  pgvector        │ ──▶ 向量检索           │      │
│         │  文件系统         │ ──▶ uploads/           │      │
│         └──────────────────┘                        │      │
└─────────────────────────────────────────────────────────────┘
```

## 消息处理流程

```
用户消息到达
    │
    ▼
┌─────────────────────┐
│  文件消息？          │──是──▶ 下载解密 → 存磁盘 → 入文件队列
│  (image/file)       │         返回文件面板 + 关联提示
└────────┬────────────┘
         │否
         ▼
┌─────────────────────┐
│  有待关联文件？      │──是──▶ 尝试匹配任务名/短编码
│  + 非命令文本？      │         成功则自动关联
└────────┬────────────┘
         │否
         ▼
┌─────────────────────┐
│  会话状态检查        │──有──▶ 按意图处理（如确认清理）
│  (conversation)     │
└────────┬────────────┘
         │无
         ▼
┌─────────────────────┐
│  Tier 1: 正则匹配   │──命中──▶ 快速路径执行
│  帮助/列表/完成/创建 │          返回结果
└────────┬────────────┘
         │未命中
         ▼
┌─────────────────────┐
│  记忆检索            │──task_related──▶ pgvector top-3
│  (memory_service)   │                  注入 system prompt
└────────┬────────────┘
         │
         ▼
┌─────────────────────┐
│  Tier 2: LLM NLU    │──有 tool_calls──▶ 执行 tools → 回复
│  (DeepSeek)         │
│  Tool Calling       │──纯回复──────▶ 更新对话状态 → 回复
└────────┬────────────┘
         │超时/失败
         ▼
┌─────────────────────┐
│  Tier 3: 降级       │
│  "没太理解，试试帮助" │
└─────────────────────┘
```

## 数据模型

### Task（任务主表）

```sql
CREATE TABLE task (
    id          SERIAL PRIMARY KEY,
    chatid      VARCHAR(128) NOT NULL DEFAULT '',
    title       VARCHAR(255) NOT NULL,
    short_code  VARCHAR(16) DEFAULT '' INDEX,
    difficulty  FLOAT DEFAULT 5,
    importance  FLOAT DEFAULT 5,
    risk_score  FLOAT DEFAULT 0,
    status      VARCHAR(20) DEFAULT 'pending',
    description TEXT,
    ddl_time    TIMESTAMP NOT NULL,
    created_at  TIMESTAMP DEFAULT NOW()
);
```

### TaskFile（文件附件）

```sql
CREATE TABLE task_file (
    id            SERIAL PRIMARY KEY,
    task_id       INTEGER REFERENCES task(id) ON DELETE SET NULL,
    chatid        VARCHAR(128) NOT NULL DEFAULT '',
    original_name VARCHAR(512) DEFAULT '',
    label         VARCHAR(128) DEFAULT '',
    stored_path   VARCHAR(1024) DEFAULT '',
    file_type     VARCHAR(20) DEFAULT 'file',
    size          INTEGER DEFAULT 0,
    created_at    TIMESTAMP DEFAULT NOW()
);
```

### ConversationRecord（会话状态）

```sql
CREATE TABLE conversation (
    id          SERIAL PRIMARY KEY,
    chatid      VARCHAR(128) UNIQUE NOT NULL DEFAULT '',
    intent      VARCHAR(50) DEFAULT '',
    context_json TEXT DEFAULT '{}',
    created_at  TIMESTAMP DEFAULT NOW(),
    updated_at  TIMESTAMP DEFAULT NOW()
);
```

### MemoryFragment（向量记忆）

```sql
CREATE TABLE memory_fragment (
    id          SERIAL PRIMARY KEY,
    chatid      VARCHAR(128) DEFAULT '' INDEX,
    content     TEXT NOT NULL,
    embedding   VECTOR(512),          -- pgvector, BAAI/bge-small-zh-v1.5
    memory_type VARCHAR(20) DEFAULT 'task',
    source_id   INTEGER,
    created_at  TIMESTAMP DEFAULT NOW()
);
```

## 风险评分模型

### 公式

```
urgency = 100 × 0.5^(hours_left / 48)

score   = difficulty × 3 + importance × 3 + urgency × 0.4

结果 clamp 到 [0, 100]
```

### 可视化

```
urgency
100 ┤                              ╱
    │                           ╱
 80 ┤                        ╱
    │                     ╱
 60 ┤                  ╱
    │               ╱
 40 ┤            ╱
    │         ╱
 20 ┤      ╱
    │   ╱
  0 ┤╱──────────────────────────────
    0    24    48    72    96   120  hours_left
         ↑           ↑
       1天后       3天后
      urgency≈70  urgency≈25
```

### 48 小时半衰期的选择

- 学生任务的典型 DDL 周期是 1-14 天
- 48h 半衰期意味着：
  - 1 周后紧迫度 ≈ 8.8（低，不会干扰远期任务排序）
  - 2 天后紧迫度 = 50（中等，开始关注）
  - 1 天后紧迫度 ≈ 70.7（高，需要优先处理）
  - 到期时紧迫度 = 100（最高优先）
- 这与学生"DDL 前两天才开始赶"的心理模型吻合

## LLM Tool Calling 设计

### 6 个 Tools

| Tool | 功能 | 参数 |
|------|------|------|
| `create_task` | 创建任务 | title, ddl_time, difficulty, importance, description |
| `query_tasks` | 查询任务列表 | (无) |
| `complete_task` | 完成任务 | task_id 或 title_hint |
| `list_files` | 查看文件 | task_id (可选) |
| `send_files` | 发送文件 | task_id 或 title_hint |
| `associate_file` | 关联文件 | title_hint |

### 设计原则

1. **LLM 只做意图识别，不直接操作数据库** — 所有 tool 执行逻辑在 Python 层
2. **参数可选** — 用户说"完成数据库实验"，LLM 用 `title_hint` 匹配，不需要知道 ID
3. **幂等性** — 重复调用同一 tool 不会产生重复数据
4. **降级** — LLM 超时 8 秒，失败自动降级到正则提示

## 文件存储策略

```
uploads/
└── <chatid>/                    # 按用户隔离
    ├── unassigned/              # 未关联任务的文件（文件队列）
    │   ├── uuid_filename.pdf
    │   └── uuid_image.png
    └── <task_id>/               # 已关联任务的文件
        └── uuid_report.docx
```

- 文件从企微 SDK 下载后解密存储，数据库只记录路径
- 关联任务时自动从 `unassigned/` 移动到 `<task_id>/`
- 完成任务时自动删除关联文件和目录
- 检测到乱码文件名时自动用 `{任务标题}_{文件类型}` 重命名

## 向量记忆系统

### 写入时机（fire-and-forget）

- 任务创建时 → `memory_type: "task"`
- 任务完成时 → `memory_type: "completion"`
- 多轮对话结束时 → `memory_type: "conversation"`

### 检索策略

- 仅在 `_is_task_related()` 为 True 时触发（关键词匹配）
- `cosine_distance <=>` 取 top-3
- 注入 LLM system prompt 作为上下文
- 降级：embedding 失败 → 跳过检索，bot 正常工作

### 为什么用 fastembed 而非 OpenAI Embedding？

- 免费（本地 ONNX 推理，无 API 调用）
- 低延迟（首次加载 ~2s，后续 <50ms）
- 中文效果好（BAAI/bge-small-zh-v1.5 专为中文优化）
- 512 维足够（vs OpenAI 1536 维，存储和检索更高效）
