---
marp: true
theme: default
paginate: true
backgroundColor: #1a1a2e
color: #eaeaea
style: |
  section {
    font-family: 'PingFang SC', 'Microsoft YaHei', sans-serif;
  }
  h1 { color: #00d4ff; }
  h2 { color: #00d4ff; }
  h3 { color: #7dd3fc; }
  strong { color: #fbbf24; }
  code { background: #2d2d44; color: #7dd3fc; padding: 2px 6px; border-radius: 4px; }
  table { font-size: 0.75em; }
  th { background: #2d2d44; color: #00d4ff; }
  td { background: #16213e; }
  blockquote { border-left: 4px solid #00d4ff; background: #16213e; padding: 10px 20px; }
---

# MCP Memory Service
## 为 AI 助手构建持久化记忆

<br>

**让 AI 不再每次从零开始**

<br>

分享人：___________
日期：2026.04.21

---

# 目录

1. 问题：AI 的"失忆症"
2. 方案选型：三大记忆方案对比
3. MCP Memory Service 架构解析
4. 核心能力深度剖析
5. K8s 生产部署实践
6. 实际效果与 Benchmark
7. 最佳实践与踩坑经验
8. 总结与展望

---

# 01 问题：AI 的"失忆症"

---

## 每次对话都是一张白纸

```
Day 1: "项目用的 ES 8.x，索引策略是按月分片..."
       → AI 理解了，给出了精准建议

Day 2: "继续昨天的 ES 优化"
       → AI: "请问你用的什么版本？索引策略是什么？"
```

<br>

### 痛点

- 🔄 **重复解释**：每次新会话都要重新介绍项目背景
- 💸 **Token 浪费**：复制粘贴历史对话消耗大量 context window
- 🧠 **知识流失**：踩坑经验、技术决策没有沉淀
- 🔀 **多工具割裂**：Kiro 里的对话，Cursor 里看不到

---

## 我们需要什么？

<br>

> 一个**跨会话、跨工具、可搜索**的 AI 记忆层

<br>

| 需求 | 说明 |
|------|------|
| 持久化 | 记忆不随会话结束而消失 |
| 语义搜索 | 不需要精确关键词，"数据库配置"能找到"Redis URL 设置" |
| 多工具共享 | Kiro、Cursor、Claude Code 共享同一份记忆 |
| 自主可控 | 数据在自己的服务器，不上传第三方 |
| 零外部依赖 | 不需要调 OpenAI/Claude API 来做记忆管理 |

---

# 02 方案选型

---

## 三大方案对比

| 维度 | mcp-memory-service | claude-mem | Mem0 |
|------|:---:|:---:|:---:|
| 定位 | 通用 AI 记忆服务 | Claude Code 专属插件 | Agent 框架记忆层 |
| 记忆采集 | prompt 驱动 | 全自动 Hook 捕获 | LLM 自动提取 |
| 存储 | SQLite-vec 单文件 | SQLite + ChromaDB | 向量库+图库+KV |
| 需要 LLM API | ❌ 纯本地 | ✅ Claude API | ✅ OpenAI 等 |
| 客户端兼容 | 20+ 工具 | 仅 Claude Code | 主要面向 Agent 框架 |
| 跨机器共享 | ✅ 远程部署 | ❌ 本地 | ✅ |
| 部署复杂度 | 低（单容器） | 低（PM2 本地） | 高（多组件） |
| 运行成本 | 零 | 有 API 费用 | 有 API 费用 |
| Stars | ~1.2K | ~46K | ~47K |

---

## 为什么选 mcp-memory-service？

<br>

✅ **零外部依赖** — Embedding 纯本地 ONNX 推理，不调任何 API

✅ **MCP 原生** — Kiro / Cursor / Claude Code / VS Code 直接连

✅ **远程部署** — K8s 一个容器，多人多工具共享同一服务

✅ **数据自主** — SQLite 单文件，备份就是拷贝文件

✅ **轻量** — 镜像 ~800MB（去掉 ChromaDB 后缩小 68%）

---

# 03 架构解析

---

## 整体架构

```
  Kiro / Cursor / Claude Code / VS Code ...
          │
          │  MCP 协议 (streamable-http)
          ▼
  ┌─────────────────────────────────┐
  │     mcp-memory-service          │
  │                                 │
  │  ┌───────────┐  ┌───────────┐  │
  │  │  FastMCP   │  │  REST API │  │
  │  │  Server    │  │  + Web UI │  │
  │  └─────┬─────┘  └─────┬─────┘  │
  │        └───────┬───────┘        │
  │                ▼                │
  │  ┌─────────────────────────┐    │
  │  │    Memory Service       │    │
  │  │  (存储/检索/整合/图谱)   │    │
  │  └────────────┬────────────┘    │
  │               ▼                 │
  │  ┌──────────┐ ┌──────────────┐  │
  │  │ ONNX     │ │  SQLite-vec  │  │
  │  │ Embedding│ │  + FTS5      │  │
  │  │ (384d)   │ │  + 知识图谱   │  │
  │  └──────────┘ └──────────────┘  │
  └─────────────────────────────────┘
```

---

## 两种传输模式

<br>

| | stdio 模式 | streamable-http 模式 |
|---|---|---|
| 入口文件 | `server_impl.py` | `mcp_server.py` (FastMCP) |
| 工具数量 | 15+ 个 | 6 个（精简） |
| 适用场景 | Claude Desktop 本地 | K8s 远程部署 |
| 会话管理 | 进程级 | HTTP 会话 |
| 我们的选择 | | ✅ |

<br>

> streamable-http 模式下的 6 个工具：
> `store_memory` · `retrieve_memory` · `search_by_tag` · `list_memories` · `delete_memory` · `check_database_health`

---

## 为什么用 SQLite-vec 替代 ChromaDB？

<br>

| | ChromaDB | SQLite-vec |
|---|---|---|
| 镜像大小 | ~2.5 GB | ~800 MB |
| 构建时间 | 10-15 min | 2-3 min |
| 依赖 | PyTorch + sentence-transformers | ONNX Runtime（轻量） |
| 数据格式 | 专有目录结构 | 单个 .db 文件 |
| 备份方式 | 需要导出工具 | `cp memory.db backup.db` |
| 搜索方式 | 内置 HNSW 索引 | 暴力扫描（10 万条 < 100ms） |
| 运维成本 | 中 | 几乎为零 |

<br>

> 代码中 ChromaDB 后端仍保留，但 `pyproject.toml` 已移除依赖，Docker 镜像不再包含。

---

# 04 核心能力

---

## 4.1 语义搜索

<br>

**Embedding 模型**：`all-MiniLM-L6-v2`（ONNX，384 维向量）

```
存储: "项目使用 Redis 6.x，连接池大小 20，超时 3s"
      ↓ Embedding
      [0.023, -0.156, 0.089, ..., 0.042]  (384d)

搜索: "数据库连接配置"
      ↓ Embedding → 余弦相似度计算
      → 命中！相似度 0.87
```

<br>

- 不需要精确关键词匹配
- 支持中英文混合
- 搜索延迟：1 万条 < 10ms，10 万条 < 100ms

---

## 4.2 记忆类型体系

<br>

**12 个 Base Type + 70+ 子类型**，定义在 `models/ontology.py`

<br>

| Base Type | 子类型示例 | 适用场景 |
|-----------|-----------|---------|
| `observation` | note, reference, document | 一般性记录 |
| `decision` | architecture, tool_choice, approach | 技术决策 |
| `learning` | insight, best_practice, gotcha | 踩坑经验 |
| `error` | bug, failure, exception, timeout | 故障排查 |
| `pattern` | recurring_issue, design_pattern | 重复模式 |

<br>

> 无效值自动降级为 `observation`，不会报错

---

## 4.3 知识图谱

<br>

记忆之间自动建立关联关系：

```
[ES 索引按月分片] ──causes──→ [查询跨月数据时性能下降]
                                      │
[改用 alias 聚合查询] ──fixes──→──────┘
                         │
[最佳实践：用 alias] ──supports──→ [改用 alias 聚合查询]
```

<br>

**6 种关系类型**：
- `causes` / `fixes` — 因果与修复
- `supports` / `contradicts` — 支持与矛盾
- `follows` / `related` — 时序与关联

---

## 4.4 自动整合（Consolidation）

<br>

### Decay 衰减机制
- 长期未访问的记忆权重逐渐降低
- 按 memory_type 设置不同保留周期

### 压缩合并
- 相似度高的记忆自动合并
- 去除重复内容，保留最新版本

### 冲突检测（v10.30.0+）
- 发现矛盾记忆时标记冲突
- 支持手动或自动解决

---

# 05 K8s 生产部署

---

## 部署架构

```
                    Ingress (traefik)
                    memory-mcp.unipus.cn
                          │
                          ▼
                ┌─────────────────┐
                │   Service       │
                │   :9527 → :8000 │
                └────────┬────────┘
                         │
                ┌────────▼────────┐
                │   Deployment    │
                │   memory-mcp    │
                │   replicas: 1   │  ← SQLite 单写，不能多副本
                │                 │
                │   image:        │
                │   memory-mcp:v4 │  ← 预装 embedding 模型
                └────────┬────────┘
                         │
                ┌────────▼────────┐
                │      PVC        │
                │   5Gi RWO       │  ← /app/data/memory.db
                └─────────────────┘
```

---

## 自定义镜像

```dockerfile
# Dockerfile.memory-mcp
FROM doobidoo/mcp-memory-service:latest   # 基础镜像 v10.33.0

# 通过国内镜像预下载 embedding 模型（~90MB）
ENV HF_ENDPOINT=https://hf-mirror.com
RUN python -c "from sentence_transformers import SentenceTransformer; \
               SentenceTransformer('all-MiniLM-L6-v2')"
ENV HF_ENDPOINT=

RUN mkdir -p /app/data/backups
```

<br>

**为什么要预装模型？**
- 首次启动需下载 ~90MB 模型
- K8s Pod 重启时网络不稳定可能下载失败
- 预装后启动时间从 2min+ 降到 ~15s

---

## 关键配置

```yaml
env:
  - name: MCP_MODE
    value: "streamable-http"        # 传输模式
  - name: MCP_SSE_HOST
    value: "0.0.0.0"               # 必须！否则只监听 127.0.0.1
  - name: MCP_SSE_PORT
    value: "8000"
  - name: MCP_MEMORY_STORAGE_BACKEND
    value: "sqlite_vec"            # 存储后端
  - name: MCP_MEMORY_SQLITE_PATH
    value: "/app/data/memory.db"   # 数据库路径（挂载 PVC）
  - name: MCP_ALLOW_ANONYMOUS_ACCESS
    value: "true"                  # 跳过 OAuth，Ingress 层做认证
```

<br>

> ⚠️ `MCP_SSE_HOST=0.0.0.0` 是最常见的踩坑点，不设的话 K8s 探针全部失败

---

## 探针配置

```yaml
startupProbe:          # 首次启动加载模型较慢
  tcpSocket:
    port: 8000
  initialDelaySeconds: 15
  periodSeconds: 10
  failureThreshold: 60   # 最多等 10 分钟

livenessProbe:
  tcpSocket:
    port: 8000
  initialDelaySeconds: 120
  periodSeconds: 30

readinessProbe:
  tcpSocket:
    port: 8000
  initialDelaySeconds: 60
  periodSeconds: 10
  failureThreshold: 6
```

> 用 TCP Socket 而非 HTTP，因为服务没有 `/health` 端点

---

# 06 效果与 Benchmark

---

## LongMemEval 基准测试

<br>

| 指标 | 分数 |
|------|------|
| R@5（turn-level） | 80.4% |
| R@5（session-level，v10.35.0+） | **86.0%** |
| R@10 | 90.4% |
| NDCG@10 | 82.2% |
| MRR | 89.1% |

<br>

> **R@5 = Recall at 5**：返回 5 条最相关记忆，正确答案在其中的概率

<br>

### 性能数据（SQLite-vec，384d 向量）

| 数据量 | 搜索延迟 | 向量文件大小 |
|--------|---------|-------------|
| 1 万条 | < 10ms | ~15 MB |
| 10 万条 | 50-100ms | ~150 MB |

---

# 07 最佳实践

---

## Steering 规范（给 AI 的行为指南）

```markdown
# Memory 管理规范

## 存储规则
- 只存有长期价值的知识和决策，不要什么都存
- 每次 store_memory 必须带项目标签 tags: ["proj:crsdp"]
- memory_type 常用值：
  - observation：一般性记录
  - decision：技术决策和选型
  - learning：踩坑经验
  - error：故障排查
- 单条记忆控制在 500 字以内
- 不要存储代码片段，只存知识和决策

## 检索规则
- 遇到需要项目上下文时，用 retrieve_memory 语义检索
- 找到相关记忆就直接用，避免重复读代码
```

---

## 标签策略

<br>

```
proj:crsdp          ← 项目标签（必带）
├── 预警             ← 业务域标签
├── 架构
├── ES
├── 部署
└── 故障
```

<br>

### 为什么需要项目标签？

- 同一个 memory 服务可能被多个项目共享
- `search_by_tag(tags=["proj:crsdp"])` 精确过滤本项目记忆
- 避免跨项目记忆污染

---

## 踩坑记录

<br>

| 问题 | 原因 | 解决 |
|------|------|------|
| K8s 探针全部失败 | 默认绑定 127.0.0.1 | 设置 `MCP_SSE_HOST=0.0.0.0` |
| 首次启动超时被杀 | 下载 embedding 模型 90MB | Dockerfile 预装模型 |
| containerd 环境服务不可达 | 无 `/.dockerenv` 文件 | monkey-patch `is_docker_environment()` |
| store_memory 100% 失败 | 27b-io fork 的 prompt_name bug | 切换到 doobidoo 主线 |
| 并发访问 segfault | SQLite-vec 线程安全问题 | 升级到 v10.36.1+ |

---

# 08 总结与展望

---

## 总结

<br>

### MCP Memory Service 的核心价值

🧠 **让 AI 拥有长期记忆** — 跨会话保持项目上下文

🔍 **语义搜索** — 不需要精确关键词，按意思找

🌐 **多工具共享** — 一个服务，Kiro / Cursor / Claude 都能用

🏠 **数据自主** — 单文件数据库，完全在自己的服务器

💰 **零成本** — 不调任何外部 API，纯本地计算

---

## 展望

<br>

### 值得关注的演进方向

- **v10.35.0** `memory_store_session` — 会话级记忆存储，R@5 提升 5.6%
- **v10.36.1** SQLite-vec 并发修复 — 生产稳定性提升
- **sqlite-vec ANN 索引**（开发中）— 百万级数据性能突破
- **Cloudflare Hybrid 模式** — 本地 + 云端混合，团队协作
- **Memory Evolution** — 非破坏性版本更新、陈旧度评分、冲突检测

<br>

> 社区活跃：1537 tests，每周发版，Apache 2.0

---

# Q & A

<br>
<br>

### 相关资源

- GitHub: `github.com/doobidoo/mcp-memory-service`
- 文档: `github.com/doobidoo/mcp-memory-service/wiki`
- K8s 部署: `mcp-all-in-one.yaml`

<br>

**谢谢！**
