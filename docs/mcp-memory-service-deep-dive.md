# MCP Memory Service 深度技术分析

## 一、项目概览

| 项目 | 信息 |
|------|------|
| 名称 | mcp-memory-service |
| 作者 | Heinrich Krupp (doobidoo) |
| 版本 | v10.36.4 (2026-04-10) |
| 协议 | Apache 2.0 |
| Stars | ~1,200+ |
| 测试 | 1,537 tests |
| 语言 | Python 3.10+ |
| 定位 | 开源 AI 持久化记忆服务，支持 MCP 协议 + REST API |

### 一句话定义

让 AI 助手（Claude/Kiro/Cursor 等）拥有跨会话、跨工具的长期记忆能力，数据完全自主可控，零外部 API 依赖。

---

## 二、解决的核心问题

AI 助手的"失忆症"：

1. **会话隔离**：每次新对话都是白纸，之前讨论的架构决策、踩坑经验全部丢失
2. **工具割裂**：Kiro 里的对话记忆，Cursor 里看不到
3. **Token 浪费**：用户被迫复制粘贴历史对话来恢复上下文
4. **知识流失**：团队的技术决策和故障排查经验没有沉淀

---

## 三、系统架构

### 3.1 整体架构

```
  AI 客户端 (Kiro / Cursor / Claude Code / VS Code / LangGraph / CrewAI ...)
          │
          ├── MCP 协议 (stdio)          → server_impl.py   (15+ 工具，本地连接)
          ├── MCP 协议 (streamable-http) → mcp_server.py    (6 工具，远程连接)
          └── REST API (HTTP)           → web/app.py       (FastAPI，Web Dashboard)
          │
  ┌───────▼──────────────────────────────────────────────┐
  │                  Memory Service 层                    │
  │                                                      │
  │  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌────────┐  │
  │  │ 存储管理  │ │ 语义搜索  │ │ 知识图谱  │ │ 自动整合│  │
  │  └──────────┘ └──────────┘ └──────────┘ └────────┘  │
  │  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌────────┐  │
  │  │ 质量评分  │ │ 文档摄入  │ │ 会话收割  │ │ 冲突检测│  │
  │  └──────────┘ └──────────┘ └──────────┘ └────────┘  │
  └───────┬──────────────────────────────────────────────┘
          │
  ┌───────▼──────────────────────────────────────────────┐
  │                  Embedding 层                         │
  │  all-MiniLM-L6-v2 (ONNX Runtime, 384 维向量)         │
  │  纯本地推理，不调任何外部 API                          │
  └───────┬──────────────────────────────────────────────┘
          │
  ┌───────▼──────────────────────────────────────────────┐
  │                  Storage 层                           │
  │  ┌────────────┐ ┌──────────────┐ ┌────────────────┐  │
  │  │ SQLite-vec │ │ Cloudflare   │ │ Hybrid         │  │
  │  │ (默认)     │ │ D1+Vectorize │ │ 本地+云端混合   │  │
  │  │ 单文件DB   │ │ 全球分布     │ │ 团队协作       │  │
  │  └────────────┘ └──────────────┘ └────────────────┘  │
  └──────────────────────────────────────────────────────┘
```

### 3.2 源码模块结构（18 个子模块）

```
src/mcp_memory_service/
├── api/              # REST API 客户端封装
├── backup/           # 自动备份调度
├── cli/              # 命令行工具 (memory / memory-server)
├── consolidation/    # 自动整合引擎（衰减/压缩/聚类/遗忘/关系推理）
├── discovery/        # mDNS 服务发现
├── embeddings/       # Embedding 引擎（ONNX 本地 / 外部 API）
├── harvest/          # 会话收割（从对话 transcript 提取知识）
├── health/           # 数据完整性检查
├── ingestion/        # 文档摄入（PDF/CSV/JSON/TXT）
├── models/           # 数据模型（Memory/Ontology/Tag/Association）
├── quality/          # 记忆质量评分（AI 评估 + 隐式信号 + ONNX 排序）
├── reasoning/        # 推理引擎（关系推断）
├── server/           # 服务器基础设施（缓存/日志/环境检测）
├── services/         # 核心业务逻辑（MemoryService）
├── storage/          # 存储后端（SQLite-vec / Cloudflare / Hybrid）
├── sync/             # 数据同步（导入/导出/Litestream）
├── utils/            # 工具集（哈希/分片/GPU检测/时间解析等）
├── web/              # Web Dashboard（FastAPI + OAuth 2.1）
├── config.py         # 全局配置（100+ 环境变量）
├── mcp_server.py     # streamable-http 入口（FastMCP，6 个工具）
└── server_impl.py    # stdio 入口（原生 MCP SDK，15+ 工具）
```

### 3.3 两种传输模式对比

| 维度 | stdio 模式 | streamable-http 模式 |
|------|-----------|---------------------|
| 入口 | `server_impl.py` | `mcp_server.py` |
| 框架 | 原生 MCP SDK | FastMCP |
| 工具数 | 15+（完整功能） | 6（精简核心） |
| 额外工具 | memory_update, memory_consolidate, memory_harvest, memory_ingest, memory_quality, memory_graph, memory_conflicts, memory_resolve, memory_store_session | 无 |
| 连接方式 | 进程级 stdin/stdout | HTTP 长连接 |
| 适用场景 | Claude Desktop 本地 | K8s 远程部署、多客户端共享 |
| 会话管理 | 进程生命周期 | HTTP session |

---

## 四、核心能力深度剖析

### 4.1 语义搜索

**Embedding 模型**：`all-MiniLM-L6-v2`
- 384 维向量，ONNX Runtime 推理
- 模型大小 ~90MB，首次加载后缓存
- 支持中英文混合文本

**搜索流程**：
1. 用户调用 `retrieve_memory(query="数据库连接配置")`
2. query 经过 Embedding 模型转为 384 维向量
3. SQLite-vec 计算与所有存储向量的余弦相似度
4. 返回 Top-N 最相似的记忆（默认 5 条）

**搜索性能**（SQLite-vec 暴力扫描）：

| 数据量 | 搜索延迟 | 向量存储大小 |
|--------|---------|-------------|
| 1 万条 | < 10ms | ~15 MB |
| 10 万条 | 50-100ms | ~150 MB |
| 100 万条 | 500ms+ | ~1.5 GB |

**多种搜索方式**：
- `retrieve_memory`：语义搜索（向量相似度）
- `search_by_tag`：标签精确过滤（OR/AND 逻辑）
- `list_memories`：分页浏览（支持 tag + memory_type 过滤）
- FTS5 全文检索：关键词精确匹配（Web API）

### 4.2 记忆类型体系

定义在 `models/ontology.py`，12 个 Base Type + 70+ 子类型：

**软件开发（5 类 26 子类型）**：
- `observation`：code_edit, file_access, search, command, conversation, conversation_turn, session, document, note, reference
- `decision`：architecture, tool_choice, approach, configuration
- `learning`：insight, best_practice, anti_pattern, gotcha
- `error`：bug, failure, exception, timeout
- `pattern`：recurring_issue, code_smell, design_pattern, workflow

**敏捷管理（2 类 12 子类型）**：
- `planning`：sprint_goal, backlog_item, story_point_estimate, velocity, retrospective, standup_note, acceptance_criteria
- `ceremony`：sprint_review, sprint_planning, daily_standup, retrospective_action, demo_feedback

**传统项目管理（2 类 12 子类型）**：
- `milestone`：deliverable, dependency, risk, constraint, assumption, deadline
- `stakeholder`：requirement, feedback, escalation, approval, change_request, status_update

**通用知识（3 类 15 子类型）**：
- `meeting`：action_item, attendee_note, agenda_item, follow_up, minutes
- `research`：finding, comparison, recommendation, source, hypothesis
- `communication`：email_summary, chat_summary, announcement, request, response

支持通过 `MCP_CUSTOM_MEMORY_TYPES` 环境变量扩展自定义类型。无效值自动降级为 `observation`。

### 4.3 知识图谱

内置 SQLite 实现的知识图谱，记忆之间自动建立关联关系。

**6 种关系类型**：

| 关系 | 语义 | 方向性 | 示例 |
|------|------|--------|------|
| `causes` | A 导致 B | 单向 | 配置错误 → 服务崩溃 |
| `fixes` | A 修复 B | 单向 | 加超时配置 → 修复连接泄漏 |
| `supports` | A 支持 B | 单向 | 压测数据 → 支持架构决策 |
| `contradicts` | A 与 B 矛盾 | 双向 | 两个互相矛盾的结论 |
| `follows` | A 在 B 之后 | 单向 | 时序关联 |
| `related` | A 与 B 相关 | 双向 | 通用关联 |

关系推理由 `consolidation/relationship_inference.py` 自动执行，基于语义相似度和类型匹配规则。

### 4.4 自动整合引擎（Consolidation）

`consolidation/` 目录包含 7 个模块，是项目最复杂的子系统：

| 模块 | 功能 |
|------|------|
| `decay.py` | 衰减机制：长期未访问的记忆权重降低，按 memory_type 设置不同保留周期 |
| `compression.py` | 压缩合并：相似度高的记忆自动合并，去除冗余 |
| `clustering.py` | 聚类分析：将相关记忆分组 |
| `associations.py` | 关联发现：自动发现记忆间的隐含关系 |
| `forgetting.py` | 遗忘机制：清理低价值、过期记忆 |
| `relationship_inference.py` | 关系推理：自动推断 causes/fixes/supports 等关系 |
| `scheduler.py` | 调度器：支持 daily/weekly/monthly/quarterly/yearly 定时执行 |

### 4.5 记忆质量评分

`quality/` 模块实现多维度质量评估：

- **AI 评估器**（`ai_evaluator.py`）：通过 Groq API 评估记忆质量
- **隐式信号**（`implicit_signals.py`）：基于访问频率、引用次数等行为信号
- **ONNX 排序器**（`onnx_ranker.py`）：本地模型排序
- **综合评分器**（`scorer.py`）：融合多个信号源

### 4.6 文档摄入

`ingestion/` 支持多种文档格式直接导入为记忆：

| 格式 | 加载器 | 说明 |
|------|--------|------|
| PDF | `pdf_loader.py` | 基于 pypdf |
| CSV | `csv_loader.py` | 按行或按列摄入 |
| JSON | `json_loader.py` | 支持嵌套结构 |
| TXT/MD | `text_loader.py` | 纯文本 |
| 目录 | `directory_ingestion.py` | 递归扫描目录 |

自动分块（`chunker.py`），支持配置 chunk_size 和 overlap。

### 4.7 会话收割（Harvest）

`harvest/` 模块可以从 Claude Code 的会话 transcript 中自动提取有价值的知识：

1. `parser.py`：解析会话 transcript
2. `extractor.py`：基于规则提取候选记忆（最大 500 字符）
3. `classifier.py`：分类（支持 Groq LLM 辅助分类）
4. `harvester.py`：去重后存入记忆库

### 4.8 内容分片

`utils/content_splitter.py` 实现智能分片：

**分割优先级**：
1. 双换行（段落边界）
2. 单换行
3. 句子结尾（. ! ? 后跟空格）
4. 空格（词边界）
5. 字符位置（最后手段）

分片间有可配置的重叠（默认 50 字符），保持上下文连贯。

---

## 五、存储后端对比

### 5.1 三种后端

| 维度 | SQLite-vec | Cloudflare | Hybrid |
|------|-----------|------------|--------|
| 存储 | 本地单文件 .db | Cloudflare D1 + Vectorize | 本地 + 云端 |
| 向量搜索 | sqlite-vec 扩展 | Cloudflare Vectorize | 两端都搜 |
| 全文检索 | FTS5 | D1 SQL | 两端都有 |
| 单条长度限制 | 无限制 | 800 字符 | 800 字符 |
| 多设备同步 | ❌ | ✅ | ✅ |
| 团队协作 | ❌ | ✅ | ✅ |
| 离线可用 | ✅ | ❌ | ✅（本地端） |
| 运维成本 | 几乎为零 | 低（Cloudflare 托管） | 中 |

### 5.2 从 ChromaDB 迁移到 SQLite-vec 的原因

| 维度 | ChromaDB | SQLite-vec |
|------|----------|-----------|
| Docker 镜像 | ~2.5 GB | ~800 MB（缩小 68%） |
| 构建时间 | 10-15 min | 2-3 min（快 5x） |
| 依赖 | PyTorch + sentence-transformers | ONNX Runtime |
| 数据格式 | 专有目录结构 | 单个 .db 文件 |
| 备份 | 需要导出工具 | `cp memory.db backup.db` |
| 索引类型 | HNSW 近似索引 | 暴力扫描 |

ChromaDB 后端代码仍保留在 `storage/` 目录，但 `pyproject.toml` 已移除依赖，Docker 镜像不再包含。

---

## 六、竞品对比

### 6.1 五大记忆方案全景对比

| 维度 | mcp-memory-service | claude-mem | Mem0 | Basic Memory | Claude 内置记忆 |
|------|:---:|:---:|:---:|:---:|:---:|
| Stars | ~1.2K | ~46K | ~47K | ~5K | N/A |
| 定位 | 通用 AI 记忆服务 | Claude Code 专属插件 | Agent 框架记忆层 | Markdown 知识图谱 | 产品内置功能 |
| 开源 | ✅ Apache 2.0 | ✅ MIT | ✅ Apache 2.0 | ✅ MIT | ❌ 闭源 |
| 记忆采集 | prompt 驱动 / harvest | 全自动 Hook 捕获 | LLM 自动提取 | 手动 / prompt 驱动 | 自动摘要 |
| 存储 | SQLite-vec 单文件 | SQLite + ChromaDB | 向量库+图库+KV | Markdown 文件 | 本地 .md / 云端 |
| 搜索 | 语义+标签+FTS5 | 语义+FTS5 | 语义+图遍历 | 语义+文件搜索 | Claude 自身理解 |
| 知识图谱 | ✅ 内置 | ❌ | ✅ 需外部图库 | ✅ 语义图谱 | ❌ |
| 需要 LLM API | ❌ 纯本地 | ✅ Claude API | ✅ OpenAI 等 | ❌ 纯本地 | N/A |
| 客户端兼容 | 20+ 工具 | 仅 Claude Code | 主要 Agent 框架 | MCP 客户端 | 仅 Claude |
| 远程部署 | ✅ K8s | ❌ 本地 | ✅ | ❌ 本地 | N/A |
| 跨工具共享 | ✅ | ❌ | ✅ | ✅ | ❌ |
| 跨机器共享 | ✅ | ❌ | ✅ | ❌ | ❌ |
| 运行成本 | 零 | 有 API 费用 | 有 API 费用 | 零 | 包含在订阅 |
| 部署复杂度 | 低（单容器） | 低（PM2） | 高（多组件） | 低（pip install） | 零 |
| Web Dashboard | ✅ 8 个 tab | ✅ | ✅ 托管版 | ❌ | ❌ |
| 文档摄入 | ✅ PDF/CSV/JSON/TXT | ❌ | ❌ | ✅ Markdown | ❌ |
| 自动整合 | ✅ decay+压缩+遗忘 | ✅ AI 压缩 | ✅ LLM 去重 | ❌ | ✅ Auto Dream |
| 记忆类型体系 | ✅ 12 类 70+ 子类型 | ❌ | ❌ | ❌ | ❌ |
| OAuth 认证 | ✅ OAuth 2.1 | ❌ | ✅ | ❌ | N/A |
| 压缩延迟 | 无（直接存储） | 60-90s（AI 压缩） | 取决于 LLM | 无 | N/A |

### 6.2 各方案适用场景

**mcp-memory-service** — 最适合：
- K8s 自托管，多人多工具共享同一记忆服务
- 不想依赖外部 LLM API，数据完全自主
- 需要结构化记忆类型体系和知识图谱
- 需要文档摄入能力（PDF/CSV 等）

**claude-mem** — 最适合：
- Claude Code 重度用户，想要零配置全自动记忆
- 不在意 API 费用，追求"什么都不用管"的体验
- 单人单机使用，不需要跨工具共享

**Mem0** — 最适合：
- 自建 Agent 流水线（LangGraph/CrewAI/AutoGen）
- 需要强大的图谱推理能力
- 有 LLM API 预算，追求自动化记忆提取

**Basic Memory** — 最适合：
- Obsidian 用户，想让 AI 读写自己的笔记库
- 偏好 Markdown 文件作为存储格式（人类可读）
- 单机使用，不需要远程部署

**Claude 内置记忆** — 最适合：
- 零配置需求，只用 Claude 生态
- 轻度记忆需求，不需要精细控制

---

## 七、Benchmark 数据

### 7.1 LongMemEval 基准测试

| 指标 | 说明 | 分数 |
|------|------|------|
| R@5 (turn-level) | 返回 5 条，正确答案在其中的概率 | 80.4% |
| R@5 (session-level) | 使用 memory_store_session (v10.35.0+) | **86.0%** |
| R@10 | 返回 10 条的召回率 | 90.4% |
| NDCG@10 | 排序质量 | 82.2% |
| MRR | 平均倒数排名 | 89.1% |

### 7.2 与 MemPalace 对比

| | MemPalace | mcp-memory-service |
|---|---|---|
| R@5 (raw) | 96.6%¹ | 86.0% (session) |
| R@5 (with LLM reranking) | 100%² | — |
| 存储粒度 | Session-level | Turn-level + Session-level |
| 依赖 | ChromaDB | SQLite-vec |

> ¹ MemPalace 的 96.6% 是在 "raw mode" 下测的（纯 ChromaDB + 默认 embedding），Palace 结构特性未启用
> ² 100% 使用了 ~500 次 LLM API 调用做 reranking

---

## 八、版本演进（v10.26 → v10.36）

| 版本 | 类型 | 关键变更 |
|------|------|---------|
| v10.26.0 | feat | Dashboard Credentials tab + Sync Owner 选择器 |
| v10.27.0 | fix | 外部 embedding 兼容性修复 |
| v10.28.0 | feat | Session harvest 工具（memory_harvest） |
| v10.29.0 | feat | LLM 分类（Groq）+ 图谱孤儿边清理 |
| v10.30.0 | feat | Memory Evolution：版本化更新 + 陈旧度评分 + 冲突检测 |
| v10.31.0 | feat | Harvest 去重 + Sync-in-Async 重构 |
| v10.32.0 | feat | 传输层健康端点 + 可配置超时 |
| v10.33.0 | refactor | 消除事件循环阻塞 + 修复 SQLite 静默数据丢失 |
| v10.34.0 | feat | LongMemEval benchmark 集成 |
| v10.35.0 | feat | **memory_store_session** 工具，R@5 提升 5.6% |
| v10.36.0 | feat | OpenCode 集成 |
| v10.36.1 | fix | **SQLite-vec 并发 segfault 修复**（生产关键） |
| v10.36.2-4 | fix | Windows 修复 |

---

## 九、生产部署要点

### 9.1 K8s 部署关键配置

```yaml
env:
  MCP_MODE: "streamable-http"
  MCP_SSE_HOST: "0.0.0.0"          # 必须！否则只监听 127.0.0.1
  MCP_SSE_PORT: "8000"
  MCP_MEMORY_STORAGE_BACKEND: "sqlite_vec"
  MCP_MEMORY_SQLITE_PATH: "/app/data/memory.db"
  MCP_ALLOW_ANONYMOUS_ACCESS: "true"  # Ingress 层做认证
```

### 9.2 常见踩坑

| 问题 | 原因 | 解决 |
|------|------|------|
| K8s 探针全部失败 | 默认绑定 127.0.0.1 | `MCP_SSE_HOST=0.0.0.0` |
| 首次启动超时 | 下载 embedding 模型 90MB | Dockerfile 预装模型 |
| containerd 不可达 | 无 `/.dockerenv` 文件 | monkey-patch `is_docker_environment()` |
| 并发 segfault | SQLite-vec 线程安全 | 升级到 v10.36.1+ |
| SQLite 单写限制 | 不支持多副本写入 | replicas: 1 |

### 9.3 自定义镜像构建

```dockerfile
FROM doobidoo/mcp-memory-service:latest
ENV HF_ENDPOINT=https://hf-mirror.com
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('all-MiniLM-L6-v2')"
ENV HF_ENDPOINT=
RUN mkdir -p /app/data/backups
```

---

## 十、总结

### 核心优势

1. **零外部依赖**：Embedding 纯本地 ONNX 推理，不调任何 API，零运行成本
2. **MCP 原生**：20+ AI 工具直接连接，不需要适配层
3. **数据自主**：SQLite 单文件，备份就是拷贝文件
4. **功能完整**：语义搜索 + 知识图谱 + 自动整合 + 质量评分 + 文档摄入
5. **类型体系**：12 类 70+ 子类型的记忆分类本体，支持自定义扩展
6. **轻量部署**：单容器 ~800MB，K8s 一键部署

### 局限性

1. **记忆采集非全自动**：依赖 AI 通过 prompt 引导主动写入（vs claude-mem 的全自动 Hook）
2. **SQLite 单写**：不支持多副本写入，水平扩展受限
3. **暴力搜索**：sqlite-vec 无 ANN 索引，百万级数据性能下降（ANN 索引开发中）
4. **streamable-http 工具精简**：远程部署模式只有 6 个工具，缺少 harvest/consolidate/update 等高级功能

### 展望

- sqlite-vec ANN 索引（DiskANN）：百万级数据性能突破
- Memory Evolution：非破坏性版本更新、陈旧度评分
- Cloudflare Hybrid：本地 + 云端混合，团队协作
- 更多 AI 客户端集成（ChatGPT Developer Mode 已支持）
