# Memory MCP 工具完整指南（server_impl.py 15 个工具）

> 当前部署：server_impl.py streamable-http 模式，Milvus + bge-small-zh-v1.5 + BM25 混合检索

---

## 一、核心读写（4 个）

### 1. memory_store — 存储记忆

最常用的工具，将一条信息存入记忆库。

**参数：**
- `content`（必填）：记忆内容文本
- `metadata`（可选）：元数据对象
  - `metadata.tags`：标签，支持字符串 `"tag1,tag2"` 或数组 `["tag1", "tag2"]`
  - `metadata.type`：记忆类型，如 decision/learning/error/pattern/observation
- `conversation_id`（可选）：对话标识符。传了之后会跳过语义去重，允许同一对话中存储多条主题相似的记忆（精确哈希去重仍然生效）

**存储流程：**
1. 计算内容的 SHA256 哈希，检查是否已存在（精确去重）
2. 如果没传 conversation_id，做语义去重：在 24 小时内搜索相似度 > 0.85 的记忆，有则拒绝
3. 生成 embedding 向量
4. 写入 Milvus（向量 + 元数据 + BM25 稀疏向量）
5. 内容超长时自动按段落/句子边界分片，50 字符重叠

**调用示例：**
```json
{
  "content": "Redis 集群脑裂修复：配置 min-replicas-to-write=1 防止孤立主节点接受写入",
  "metadata": {
    "tags": "project:crsdp,redis,踩坑",
    "type": "error"
  }
}
```

---

### 2. memory_store_session — 存储完整对话

将一段多轮对话作为一个整体存储，而不是逐条拆开。

**参数：**
- `turns`（必填）：对话轮次数组，每个元素 `{role: "user"/"assistant", content: "..."}`，至少 1 轮
- `session_id`（可选）：会话标识符，不传则自动生成 UUID
- `tags`（可选）：额外标签，字符串或数组。系统会自动加 `session:<id>` 标签
- `metadata`（可选）：额外元数据

**适用场景：**
- 需要保留完整对话上下文的场景（比如一次技术讨论的完整过程）
- 比逐条 memory_store 更好，检索时能拿到完整对话而不是碎片

**调用示例：**
```json
{
  "turns": [
    {"role": "user", "content": "Redis 超时应该设多少？"},
    {"role": "assistant", "content": "建议 10s，原因是..."}
  ],
  "tags": "project:crsdp,redis,配置"
}
```

---

### 3. memory_search — 统一搜索（主力工具）

最强大的搜索工具，统一了语义搜索、精确匹配、标签过滤、时间过滤等多种能力。

**参数：**
- `query`（语义/精确模式必填，纯时间搜索可选）：搜索关键词
- `mode`（可选，默认 semantic）：
  - `semantic`：向量语义搜索（Milvus 后端自动走向量+BM25 混合检索）
  - `exact`：精确文本匹配，内容中包含查询字符串
  - `hybrid`：语义相似度 + 质量评分加权
- `tags`（可选）：按标签过滤，字符串或数组，返回包含任一标签的记忆
- `time_expr`（可选）：自然语言时间表达，如 "yesterday"、"last week"、"3 days ago"、"last month"
- `after` / `before`（可选）：ISO 日期范围，格式 YYYY-MM-DD
- `quality_boost`（可选，0-1）：质量加权。0 = 纯语义排序，0.3 = 70% 语义 + 30% 质量，1.0 = 纯质量排序
- `limit`（可选，默认 10，最大 100）：返回数量上限
- `include_debug`（可选）：返回调试信息（耗时、embedding 详情、过滤细节）
- `max_response_chars`（可选）：限制返回总字符数，防止上下文溢出。建议 30000-50000

**调用示例：**
```json
// 语义搜索
{"query": "缓存集群数据不一致"}

// 按标签过滤
{"query": "部署", "tags": ["project:mcp-memory-service"]}

// 时间回溯
{"time_expr": "last week", "limit": 20}

// 组合：某项目上周的决策
{"query": "技术选型", "tags": ["project:crsdp"], "time_expr": "last week", "mode": "hybrid", "quality_boost": 0.3}
```

---

### 4. memory_list — 分页浏览

不做语义搜索，按时间倒序列出记忆，支持分页和过滤。

**参数：**
- `page`（可选，默认 1）：页码，从 1 开始
- `page_size`（可选，默认 20，最大 100）：每页数量
- `tags`（可选）：标签数组过滤，返回包含任一标签的记忆
- `memory_type`（可选）：按类型过滤，如 "decision"、"error"

**返回：**
- `memories`：当前页的记忆数组
- `total`：匹配的总记忆数
- `page` / `page_size` / `total_pages` / `has_more`：分页信息

**调用示例：**
```json
// 浏览所有记忆
{}

// 第 2 页，每页 50 条
{"page": 2, "page_size": 50}

// 只看决策类型
{"memory_type": "decision", "page_size": 10}

// 只看某项目
{"tags": ["project:mcp-memory-service"]}
```

---

## 二、删除与清理（2 个）

### 5. memory_delete — 灵活删除

支持单条删除和批量删除，有预览功能。

**参数（组合使用，AND 逻辑）：**
- `content_hash`（可选）：按哈希删除单条。传了这个参数会忽略其他过滤条件
- `tags`（可选）：按标签过滤，字符串或数组
- `tag_match`（可选，默认 "any"）："any" = 匹配任一标签，"all" = 匹配所有标签
- `before` / `after`（可选）：ISO 日期范围
- `dry_run`（可选，默认 false）：设为 true 只预览要删什么，不实际执行

**安全机制：**
- 不传任何过滤条件会报错，防止误删全部
- dry_run 预览模式
- 返回 deleted_hashes 审计记录

**调用示例：**
```json
// 删除单条
{"content_hash": "abc123def456"}

// 预览：删除所有带 "temporary" 标签的
{"tags": ["temporary"], "dry_run": true}

// 删除 2024 年之前的旧记忆
{"before": "2024-01-01"}

// 删除某项目的所有 draft 标签记忆
{"tags": ["project:crsdp", "draft"], "tag_match": "all"}
```

---

### 6. memory_cleanup — 去重清理

自动查找并删除重复记忆。无参数，直接调用。

**注意：** Milvus 后端用 content_hash 做主键，天然不会有重复，所以这个工具在 Milvus 上基本是空操作，返回 "No duplicate memories found"。SQLite-vec 后端可能有用。

---

## 三、元数据与质量（4 个）

### 7. memory_update — 更新元数据

修改记忆的标签、类型等元数据，不需要删了重建。保留原始内容、embedding 和创建时间。

**参数：**
- `content_hash`（必填）：要更新的记忆哈希
- `updates`（必填）：要更新的字段
  - `tags`：替换标签（字符串或数组）
  - `memory_type`：更新类型
  - `metadata`：自定义元数据，会与现有 metadata 合并
- `preserve_timestamps`（可选，默认 true）：是否保留原始创建时间

**调用示例：**
```json
{
  "content_hash": "abc123...",
  "updates": {
    "tags": ["project:crsdp", "重要", "架构"],
    "memory_type": "decision"
  }
}
```

---

### 8. memory_quality — 质量管理

对记忆进行质量评分和分析。

**参数：**
- `action`（必填）：
  - `rate`：给记忆打分。需要 `content_hash` 和 `rating`（"-1" 踩 / "0" 中性 / "1" 赞），可选 `feedback` 文字
  - `get`：查看某条记忆的质量指标。需要 `content_hash`
  - `analyze`：分析所有记忆的质量分布。可选 `min_quality` / `max_quality` 阈值过滤

**调用示例：**
```json
// 给记忆点赞
{"action": "rate", "content_hash": "abc123", "rating": "1", "feedback": "非常有用"}

// 查看质量
{"action": "get", "content_hash": "abc123"}

// 分析整体质量分布
{"action": "analyze"}
```

---

### 9. memory_health — 健康检查

检查数据库连接状态和服务统计信息。无参数。

**返回：**
- 存储后端类型（milvus / sqlite_vec / hybrid）
- 总记忆数、唯一标签数
- 数据库连接状态
- Embedding 模型信息（名称、维度）
- 本周新增记忆数

---

### 10. memory_stats — 缓存统计

查看 MCP server 的缓存性能指标。无参数。

**返回：**
- 总调用次数、缓存命中率
- 存储缓存和服务缓存的命中/未命中/大小
- 初始化耗时统计（平均/最小/最大）
- 后端配置信息

---

## 四、高级功能（5 个）

### 11. memory_ingest — 文档批量导入

将文件或目录批量导入到记忆库，自动分块。

**参数：**
- `file_path`（单文件模式）：文件路径，支持 PDF/TXT/MD/JSON
- `directory_path`（目录模式）：目录路径
- `recursive`（可选，默认 true）：是否递归子目录
- `file_extensions`（可选，默认 ["pdf","txt","md","json"]）：要处理的文件类型
- `chunk_size`（可选，默认 1000）：分块大小（字符数）
- `chunk_overlap`（可选，默认 200）：块之间的重叠字符数
- `tags`（可选）：给所有导入的记忆统一打标签
- `memory_type`（可选，默认 "document"）：记忆类型
- `max_files`（可选，默认 100）：目录模式最多处理的文件数

**调用示例：**
```json
// 导入单个文档
{"file_path": "/path/to/document.pdf", "tags": ["documentation"]}

// 批量导入目录
{"directory_path": "/path/to/docs", "file_extensions": ["md", "txt"], "tags": ["project-docs"]}
```

---

### 12. memory_harvest — 从会话提取学习

从 Claude Code 的会话记录中自动提取有价值的信息。

**参数：**
- `sessions`（可选，默认 1）：扫描最近几个会话
- `session_ids`（可选）：指定具体的会话 ID
- `types`（可选）：过滤提取类型，可选 decision / bug / convention / learning / context
- `min_confidence`（可选，默认 0.6）：最低置信度阈值
- `dry_run`（可选，默认 true）：预览模式，不实际存储
- `use_llm`（可选，默认 false）：用 Groq LLM 做二次验证（需要 GROQ_API_KEY）
- `project_path`（可选）：覆盖 Claude Code 项目目录路径

**注意：** 目前只支持 Claude Code 的 JSONL 会话格式，不支持 Kiro/Cursor/Codex。

**调用示例：**
```json
// 预览最近 1 个会话的提取结果
{"sessions": 1, "dry_run": true}

// 提取最近 3 个会话的决策和踩坑，实际存储
{"sessions": 3, "types": ["decision", "bug"], "dry_run": false}
```

---

### 13. memory_graph — 知识图谱操作

探索记忆之间的关联关系。

**参数：**
- `action`（必填）：
  - `connected`：从某条记忆出发，BFS 遍历找关联记忆
    - `hash`（必填）：起始记忆哈希
    - `max_hops`（可选，默认 2）：最大遍历深度
  - `path`：找两条记忆之间的最短路径
    - `hash1`、`hash2`（必填）：起止记忆哈希
    - `max_depth`（可选，默认 5）：最大路径长度
  - `subgraph`：提取某条记忆周围的子图（节点+边）
    - `hash`（必填）：中心记忆哈希
    - `radius`（可选，默认 2）：子图半径

**调用示例：**
```json
// 找关联记忆
{"action": "connected", "hash": "abc123", "max_hops": 2}

// 找两条记忆之间的路径
{"action": "path", "hash1": "abc123", "hash2": "def456"}

// 提取子图用于可视化
{"action": "subgraph", "hash": "abc123", "radius": 3}
```

---

### 14. memory_conflicts — 冲突检测

列出所有未解决的记忆冲突。无参数。

通过相似度分析自动检测内容矛盾的记忆对。比如同一个配置项记了两个不同的值，或者同一个技术方案有两个互相矛盾的结论。

**返回：** 冲突记忆对列表，每对包含两条记忆的内容、哈希和相似度分数。

---

### 15. memory_resolve — 解决冲突

解决一个记忆冲突，选择正确的那条保留。

**参数：**
- `winner_hash`（必填）：正确记忆的哈希
- `loser_hash`（必填）：错误记忆的哈希

**效果：** 保留 winner，删除 loser，记录解决结果。

**调用示例：**
```json
{
  "winner_hash": "abc123...",
  "loser_hash": "def456..."
}
```

---

## 工具能力矩阵

| 场景 | 推荐工具 |
|------|---------|
| 存一条经验/决策 | `memory_store` |
| 存一段完整对话 | `memory_store_session` |
| 搜索相关记忆 | `memory_search`（语义模式） |
| 按标签筛选 | `memory_search`（带 tags 参数） |
| 查最近的记忆 | `memory_search`（带 time_expr） |
| 浏览所有记忆 | `memory_list` |
| 删除过时记忆 | `memory_delete` |
| 批量清理某标签 | `memory_delete`（带 tags + dry_run） |
| 改标签/类型 | `memory_update` |
| 导入文档 | `memory_ingest` |
| 检查服务状态 | `memory_health` |
| 找关联记忆 | `memory_graph` |
| 检测矛盾 | `memory_conflicts` → `memory_resolve` |
