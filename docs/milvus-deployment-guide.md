# mcp-memory-service Milvus 版 K8s 部署指南

> 从本地开发到 Docker 镜像构建，再到 K8s 集群部署的完整流程。
> 基于实际部署经验整理，包含所有踩坑记录。

## 1. 架构概览

```
┌─────────────────────────────────────────────────────────┐
│                    mcp-system 命名空间                     │
│                                                         │
│  ┌──────────────┐    ┌──────────────┐                   │
│  │ memory-mcp   │    │ memory-mcp   │                   │
│  │ (SQLite版)   │    │ -milvus      │ ◄── 本文档部署目标  │
│  │ :30527       │    │ :30529       │                   │
│  └──────────────┘    └──────┬───────┘                   │
│                             │                           │
│                    ┌────────▼────────┐                   │
│                    │ Milvus          │                   │
│                    │ Standalone      │                   │
│                    │ :19530          │                   │
│                    └───┬────────┬────┘                   │
│                        │        │                       │
│                 ┌──────▼──┐ ┌───▼────┐                  │
│                 │  etcd   │ │ MinIO  │                  │
│                 │  :2379  │ │ :9000  │                  │
│                 └─────────┘ └────────┘                  │
└─────────────────────────────────────────────────────────┘
```

关键配置：
- Embedding 模型：`BAAI/bge-small-zh-v1.5`（512 维，95MB，中文 C-MTEB small 级别最强）
- 存储后端：Milvus Standalone v2.5.6
- 传输协议：Streamable HTTP（MCP 标准协议）
- 检索策略：BM25 + 向量混合搜索（RRFRanker 融合排序）

## 2. Embedding 模型选型

| 模型 | 维度 | 大小 | 中文测试正确率 | 结论 |
|------|------|------|---------------|------|
| all-MiniLM-L6-v2 | 384 | 90MB | 40%（纯靠英文 token 匹配） | ❌ 中文语义理解为零 |
| BAAI/bge-small-zh-v1.5 | 512 | 95MB | 配合 BM25 效果好 | ✅ 最终选择 |
| 768 维模型 | 768 | ~400MB | 收益递减 | ❌ 模型大 4 倍不值 |

选择理由：
1. 中文 C-MTEB small 级别最强
2. 模型极小（95MB），K8s 冷启动友好
3. CPU 推理最快（~5-8ms）
4. 配合 Milvus BM25 混合检索（RRFRanker）补精确关键词短板
5. 384 维中文太弱（测试 40% 正确率），768 维收益递减但模型大 4 倍不值

## 3. 本地打包 → K8s 构建镜像（无本地 Docker 环境）

本地没有 Docker 环境时，需要先打压缩包，传到 K8s 节点上构建镜像。

### 3.1 本地打压缩包

```bash
# 在项目根目录执行，排除不需要的文件
tar czf mcp-memory-service.tar.gz \
  --exclude='.git' \
  --exclude='.venv' \
  --exclude='venv' \
  --exclude='__pycache__' \
  --exclude='*.pyc' \
  --exclude='.pytest_cache' \
  --exclude='node_modules' \
  --exclude='.DS_Store' \
  --exclude='*.sqlite' \
  --exclude='*.sqlite-*' \
  --exclude='chroma_db' \
  --exclude='.mcp_memory_sqlite' \
  --exclude='.mcp_memory_chroma' \
  --exclude='archive' \
  --exclude='.gitnexus' \
  --exclude='.benchmarks' \
  --exclude='.idea' \
  --exclude='.vscode' \
  --exclude='backups' \
  --exclude='.coverage' \
  --exclude='data' \
  .
```

压缩包大约 2-5MB（不含模型和依赖）。

### 3.2 传到 K8s 节点

```bash
# scp 到 K8s 可以执行 docker build 的节点
scp mcp-memory-service.tar.gz root@<K8S_NODE_IP>:/tmp/

# SSH 到节点
ssh root@<K8S_NODE_IP>

# 解压
mkdir -p /tmp/mcp-memory-build && cd /tmp/mcp-memory-build
tar xzf /tmp/mcp-memory-service.tar.gz
```

### 3.3 在 K8s 节点上构建应用镜像

```bash
cd /tmp/mcp-memory-build

# 构建 Milvus 版镜像
docker build -f tools/docker/Dockerfile \
  --build-arg INSTALL_EXTRA="[milvus]" \
  -t unipus.tencentcloudcr.com/unipus/mcp/memory-mcp:milvus-cpu \
  .

# 推送到 TCR
docker push unipus.tencentcloudcr.com/unipus/mcp/memory-mcp:milvus-cpu
```

注意事项：
- `INSTALL_EXTRA="[milvus]"` 会安装 pymilvus + milvus-lite（~52MB），连接远程 Milvus 其实只需要 pymilvus，后续可优化为 `[milvus-remote]`
- Dockerfile 已优化层缓存：依赖安装在 `COPY src/` 之前，改代码不会触发依赖重装
- 如果未优化层缓存，改任何源码都会触发依赖重新安装（~30-50 分钟）
- 构建完成后清理：`rm -rf /tmp/mcp-memory-build /tmp/mcp-memory-service.tar.gz`

### 3.4 构建模型镜像（只需一次）

单独构建一个只包含模型文件的镜像，通过 initContainer 拷贝到共享 volume：

```dockerfile
# Dockerfile.model（在 K8s 节点上创建）
FROM python:3.12-slim
RUN pip install huggingface_hub
RUN python -c "from huggingface_hub import snapshot_download; snapshot_download('BAAI/bge-small-zh-v1.5')"
RUN mkdir -p /models && cp -r /root/.cache/huggingface /models/
```

```bash
docker build -f Dockerfile.model \
  -t unipus.tencentcloudcr.com/unipus/mcp/embedding-model:bge-zh-v1.5 \
  .
docker push unipus.tencentcloudcr.com/unipus/mcp/embedding-model:bge-zh-v1.5
```

模型镜像只需构建一次，除非换模型。

### 3.5 快速更新流程（只改了代码）

日常改代码后的快速更新流程：

```bash
# === 本地 ===
# 1. 打包（只需要源码相关文件）
tar czf mcp-memory-service.tar.gz \
  --exclude='.git' --exclude='.venv' --exclude='__pycache__' \
  --exclude='node_modules' --exclude='archive' --exclude='.gitnexus' \
  --exclude='*.sqlite' --exclude='backups' --exclude='data' \
  .

# 2. 传到节点
scp mcp-memory-service.tar.gz root@<K8S_NODE_IP>:/tmp/

# === K8s 节点 ===
# 3. 解压 + 构建 + 推送
cd /tmp && rm -rf mcp-memory-build && mkdir mcp-memory-build && cd mcp-memory-build
tar xzf /tmp/mcp-memory-service.tar.gz

docker build -f tools/docker/Dockerfile \
  --build-arg INSTALL_EXTRA="[milvus]" \
  -t unipus.tencentcloudcr.com/unipus/mcp/memory-mcp:milvus-cpu \
  .
docker push unipus.tencentcloudcr.com/unipus/mcp/memory-mcp:milvus-cpu

# 4. 滚动更新 Pod（拉取新镜像）
kubectl -n mcp-system rollout restart deploy/memory-mcp-milvus

# 5. 等待就绪
kubectl -n mcp-system rollout status deploy/memory-mcp-milvus

# 6. 清理
rm -rf /tmp/mcp-memory-build /tmp/mcp-memory-service.tar.gz
```

由于 Dockerfile 层缓存优化，只改源码时构建很快（几十秒），依赖层会命中缓存。

### 3.6 模型缓存路径踩坑

milvus.py 的 `_initialize_embedding_model` 查找缓存时用 `models--sentence-transformers--{name}` 路径，但 HuggingFace 下载的实际路径是 `models--{name}`（无 sentence-transformers 前缀）。

解决方案：在 initContainer 中建软链接：
```bash
cd /model-cache/hub && \
ln -sf models--BAAI--bge-small-zh-v1.5 models--sentence-transformers--BAAI--bge-small-zh-v1.5
```

## 4. Milvus 集群部署

### 4.1 部署命令

```bash
kubectl apply -f deploy/k8s/milvus-standalone.yaml
```

包含 4 个组件：
- **etcd**：元数据存储（PVC 2Gi）
- **MinIO**：对象存储（PVC 20Gi）
- **Milvus Standalone**：向量数据库（v2.5.6）
- **Attu**：可视化管理工具（NodePort 30531）

### 4.2 启动顺序

必须保证 etcd → MinIO → Milvus 的启动顺序，YAML 里用 initContainer wait 实现：
```yaml
initContainers:
- name: wait-etcd
  command: ['sh', '-c', 'until nc -z milvus-etcd... 2379; do sleep 3; done']
- name: wait-minio
  command: ['sh', '-c', 'until nc -z milvus-minio... 9000; do sleep 3; done']
```

### 4.3 etcd 踩坑

etcd 必须显式设置以下参数，否则 Pod IP 和 localhost 不匹配导致 CrashLoopBackOff：
```yaml
command:
- etcd
- --name=default
- --initial-advertise-peer-urls=http://0.0.0.0:2380
- --initial-cluster=default=http://0.0.0.0:2380
```

如果 etcd PVC 有脏数据，需要先 `kubectl delete pvc milvus-etcd-data` 再重建。

### 4.4 Milvus Service

默认 ClusterIP，集群外访问需改 NodePort：
```yaml
spec:
  type: NodePort
  ports:
  - name: grpc
    port: 19530
    nodePort: 30530
```

### 4.5 安全注意

Milvus 默认不开启认证，公网暴露时需要配置安全组限制访问 IP。

## 5. 应用部署

### 5.1 部署命令

```bash
# 先部署 Milvus（如果还没部署）
kubectl apply -f deploy/k8s/milvus-standalone.yaml

# 部署应用
kubectl apply -f deploy/k8s/memory-mcp-milvus.yaml
```

### 5.2 关键环境变量

| 变量 | 值 | 说明 |
|------|-----|------|
| `MCP_MODE` | `streamable-http` | MCP 传输协议 |
| `MCP_SSE_HOST` | `0.0.0.0` | ⚠️ 必须设置，否则默认 127.0.0.1 导致探针失败 |
| `MCP_SSE_PORT` | `8765` | HTTP 服务端口 |
| `MCP_MEMORY_STORAGE_BACKEND` | `milvus` | 存储后端 |
| `MCP_MILVUS_URI` | `http://milvus.mcp-system.svc.cluster.local:19530` | Milvus 集群内地址 |
| `MCP_MILVUS_COLLECTION_NAME` | `mcp_memory` | Collection 名称 |
| `MCP_EMBEDDING_MODEL` | `BAAI/bge-small-zh-v1.5` | Embedding 模型 |
| `HF_HOME` | `/model-cache` | 模型缓存路径 |
| `HF_HUB_OFFLINE` | `1` | 禁止联网下载，强制本地缓存 |

### 5.3 initContainer 模型预加载

```yaml
initContainers:
- name: load-model
  image: unipus.tencentcloudcr.com/unipus/mcp/embedding-model:bge-zh-v1.5
  command: ['sh', '-c', 'cp -r /models/* /model-cache/ && cd /model-cache/hub && ln -sf models--BAAI--bge-small-zh-v1.5 models--sentence-transformers--BAAI--bge-small-zh-v1.5']
  volumeMounts:
  - name: model-cache
    mountPath: /model-cache
```

### 5.4 探针配置

```yaml
# 启动探针：模型加载需要 30-120 秒
startupProbe:
  tcpSocket:
    port: 8765
  initialDelaySeconds: 15
  periodSeconds: 10
  failureThreshold: 60  # 最多等 10 分钟

# 存活探针
livenessProbe:
  tcpSocket:
    port: 8765
  initialDelaySeconds: 120
  periodSeconds: 30
```

注意：用 TCP Socket 探针而非 HTTP，因为 MCP 服务没有 `/health` 端点。

### 5.5 Ingress 配置

当前复用 `semgrep-mcp.unipus.cn` 域名测试：
```yaml
spec:
  rules:
  - host: semgrep-mcp.unipus.cn
    http:
      paths:
      - path: /
        pathType: Prefix
        backend:
          service:
            name: memory-mcp-milvus
            port:
              number: 9528
```

## 6. 验证

```bash
# 检查 Pod 状态
kubectl -n mcp-system get pods -l app=memory-mcp-milvus

# 查看日志
kubectl -n mcp-system logs deploy/memory-mcp-milvus -c memory --tail=50

# 查看环境变量确认配置
kubectl exec -n mcp-system deploy/memory-mcp-milvus -- env | grep -i -E "model|embedding|backend|milvus"
```

IDE 配置（Kiro）：
```json
{
  "mcpServers": {
    "memory": {
      "type": "streamable-http",
      "url": "http://semgrep-mcp.unipus.cn/mcp"
    }
  }
}
```

## 7. Collection Schema

mcp_memory collection 共 12 字段：

| 字段 | 类型 | 说明 |
|------|------|------|
| id | VarChar(128) PK | content_hash |
| vector | FloatVector(512) | bge-small-zh-v1.5 生成的 dense 向量 |
| content | VarChar(65535) | 记忆内容（enable_analyzer=True） |
| content_lower | VarChar(65535) | 小写内容（精确匹配用） |
| tags | VarChar(8192) | 标签 |
| memory_type | VarChar(128) | 记忆类型 |
| metadata | VarChar(65535) | 元数据 JSON |
| created_at | Double | 创建时间戳 |
| updated_at | Double | 更新时间戳 |
| created_at_iso | VarChar(64) | ISO 格式创建时间 |
| updated_at_iso | VarChar(64) | ISO 格式更新时间 |
| sparse_vector | SparseFloatVector | BM25 Function 自动生成 |

2 个索引（vector + sparse_vector），1 个 BM25 Function。

## 8. 踩坑汇总

### 8.1 K8s 部署踩坑

| 问题 | 原因 | 解决方案 |
|------|------|---------|
| 探针全部失败 | MCP_SSE_HOST 默认 127.0.0.1 | 设置为 `0.0.0.0` |
| 首次启动超时 | 需下载 embedding 模型 ~90MB | Dockerfile 预装或 initContainer 预加载 |
| containerd 环境检测失败 | 无 `/.dockerenv` 文件 | monkey-patch `is_docker_environment()` |
| SQLite 并发写入 | SQLite 单写 | replicas 只能设 1（Milvus 版无此限制） |
| etcd CrashLoopBackOff | Pod IP 和 localhost 不匹配 | 显式设置 `--initial-advertise-peer-urls=http://0.0.0.0:2380` |
| etcd 启动失败 | PVC 有脏数据 | `kubectl delete pvc` 再重建 |

### 8.2 Milvus 踩坑

| 问题 | 原因 | 解决方案 |
|------|------|---------|
| BM25 创建失败 | content 字段未设 `enable_analyzer=True` | schema.add_field 时加上 |
| 语义去重报错 `multiple anns_fields` | collection 有 vector + sparse_vector 两个向量字段，search() 未指定 anns_field | 显式传入 `anns_field="vector"` |
| Zilliz Cloud 纯 scalar collection 创建失败 | 云端要求至少一个向量字段 | 添加 `_dummy_vec` (dim=1) |
| edge ID max_length 不够 | SHA256 64+1+64=129 > 128 | 改用 `sha256(f"{source}:{target}")` 固定 64 字符 |
| 模型缓存路径不匹配 | HF 下载路径 vs sentence-transformers 查找路径不同 | initContainer 建软链接 |

### 8.3 Zilliz Cloud 兼容性

Milvus Lite 不会暴露以下问题，只有远程 Milvus / Zilliz Cloud 才会报错：
- 纯 scalar collection 创建失败
- edge ID max_length 限制
- BM25 enable_analyzer 要求

## 9. 双 MCP Server 架构

项目有两套并行的 MCP Server 实现：

| | mcp_server.py (FastMCP) | server_impl.py (低级 SDK) |
|---|---|---|
| 入口 | `mcp-memory-server` | `memory server` |
| 传输 | streamable-http | stdio / SSE / streamable-http |
| 用途 | K8s 远程部署 | 本地客户端（Claude Desktop 等） |
| 工具数 | 8 个 | 15~16 个 |
| 工具名风格 | `store_memory` / `retrieve_memory` | `memory_store` / `memory_search` |

K8s 部署用的是 mcp_server.py 的 FastMCP 版本。两套 server 共享底层 MemoryService 和 MemoryStorage。

## 10. 资源清单

| 组件 | 镜像 | CPU 请求/限制 | 内存 请求/限制 |
|------|------|-------------|--------------|
| memory-mcp-milvus | memory-mcp:milvus-cpu | 200m / 2 | 512Mi / 4Gi |
| milvus-standalone | milvus:v2.5.6 | 500m / 2 | 1Gi / 4Gi |
| milvus-etcd | etcd:v3.5.16 | 100m / 500m | 256Mi / 512Mi |
| milvus-minio | minio:RELEASE.2024-11-07 | 100m / 1 | 256Mi / 1Gi |
| milvus-attu | attu:latest | 100m / 500m | 128Mi / 512Mi |
| embedding-model | embedding-model:bge-zh-v1.5 | initContainer | initContainer |
