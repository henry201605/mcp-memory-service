# Memory MCP 镜像构建指南

## 概述

Memory MCP 的 Docker 镜像由两部分组成：
1. **应用镜像**：Python 代码 + 依赖库 + 运行环境
2. **Embedding 模型**：all-MiniLM-L6-v2（~90MB），用于把文本转成 384 维向量

模型不在基础镜像里，默认首次启动时在线下载。国内环境下载不稳定，所以我们把模型预打包成独立镜像，构建时通过多阶段构建 COPY 进去，实现完全离线构建。

## 镜像架构

```
┌─────────────────────────────────────────────────┐
│  memory-mcp:v5（最终镜像）                        │
│                                                 │
│  ┌───────────────────────────────────────────┐  │
│  │  应用层                                    │  │
│  │  - Python 3.11 + 依赖库                    │  │
│  │  - mcp-memory-service v10.40.1 源码        │  │
│  │  - docker-entrypoint-unified.sh            │  │
│  └───────────────────────────────────────────┘  │
│  ┌───────────────────────────────────────────┐  │
│  │  模型层（COPY --from=model）               │  │
│  │  - all-MiniLM-L6-v2（~90MB）              │  │
│  │  - 来自 embedding-model:v1 镜像            │  │
│  └───────────────────────────────────────────┘  │
└─────────────────────────────────────────────────┘
```

## 前置条件

- 构建机器：`share-test-tke-mng`（有 Docker，无 Python，无法直连 Docker Hub）
- 可用镜像源：腾讯云镜像 `mirror.ccs.tencentyun.com`、腾讯云私有仓库 `unipus.tencentcloudcr.com`
- GitHub Container Registry：`ghcr.io`（可访问）

## 构建流程

### 阶段一：构建 Embedding 模型镜像（一次性）

模型镜像只需要构建一次，后续所有版本的 memory-mcp 镜像都复用它。

#### 1.1 下载模型文件

构建机器上没有 Python，借用本地已有的 `ghcr.io/doobidoo/mcp-memory-service:latest` 镜像来下载模型：

```bash
# 创建工作目录
mkdir -p /tmp/scripts/docker/memory-mcp/models_cache
cd /tmp/scripts/docker/memory-mcp

# 用已有镜像下载模型到宿主机
# --entrypoint python 跳过镜像默认的服务启动脚本
# -v 挂载宿主机目录，模型下载后保存在宿主机上
# HF_ENDPOINT 使用国内 HuggingFace 镜像加速
docker run --rm \
  -v $(pwd)/models_cache:/models \
  -e HF_ENDPOINT=https://hf-mirror.com \
  --entrypoint python \
  ghcr.io/doobidoo/mcp-memory-service:latest \
  -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('all-MiniLM-L6-v2', cache_folder='/models')"
```

输出 `Loading weights: 100%` 表示下载成功。`UNEXPECTED` 警告可忽略。

#### 1.2 打包模型镜像

```bash
cd /tmp/scripts/docker/memory-mcp/models_cache

# 写 Dockerfile（用本地已有的 nginx:alpine 作为基础镜像，仅当文件容器用）
cat > Dockerfile.model <<'EOF'
FROM mirror.ccs.tencentyun.com/library/nginx:alpine
COPY . /models
EOF

# 构建并推送
docker build -f Dockerfile.model -t unipus.tencentcloudcr.com/unipus/mcp/embedding-model:v1 .
docker push unipus.tencentcloudcr.com/unipus/mcp/embedding-model:v1
```

#### 1.3 验证

```bash
# 确认镜像已推送
docker images | grep embedding-model
# 预期输出：
# unipus.tencentcloudcr.com/unipus/mcp/embedding-model   v1   xxx   ~150MB
```

### 阶段二：构建 Memory MCP 应用镜像

每次代码更新后执行此阶段。

#### 2.1 准备源码

```bash
# 在构建机器上拉取最新代码
cd /tmp/scripts/docker/memory-mcp
git clone https://github.com/henry201605/mcp-memory-service.git source
# 或者从本地 Mac scp 过去
# scp -r /path/to/mcp-memory-service root@share-test-tke-mng:/tmp/scripts/docker/memory-mcp/source
```

#### 2.2 编写 Dockerfile

```bash
cd /tmp/scripts/docker/memory-mcp/source

cat > Dockerfile.v5 <<'EOF'
# ============================================================
# Memory MCP 自定义镜像（v5）
# 基于本地源码构建，预装 embedding 模型
# 
# 构建命令：
#   docker build -f Dockerfile.v5 -t unipus.tencentcloudcr.com/unipus/mcp/memory-mcp:v5 .
#   docker push unipus.tencentcloudcr.com/unipus/mcp/memory-mcp:v5
# ============================================================

# ---------- 阶段 1：从模型镜像获取预下载的 embedding 模型 ----------
FROM unipus.tencentcloudcr.com/unipus/mcp/embedding-model:v1 AS model

# ---------- 阶段 2：构建应用镜像 ----------
FROM mirror.ccs.tencentyun.com/library/python:3.11-slim

# 环境变量
ENV PYTHONUNBUFFERED=1 \
    MCP_MEMORY_STORAGE_BACKEND=sqlite_vec \
    MCP_MEMORY_SQLITE_PATH=/app/sqlite_db \
    MCP_MEMORY_BACKUPS_PATH=/app/backups \
    PYTHONPATH=/app/src \
    DOCKER_CONTAINER=1 \
    CHROMA_TELEMETRY_IMPL=none \
    ANONYMIZED_TELEMETRY=false \
    HF_HUB_DISABLE_TELEMETRY=1

WORKDIR /app

# 安装系统依赖
RUN apt-get update && \
    apt-get install -y --no-install-recommends curl bash && \
    apt-get upgrade -y && \
    rm -rf /var/lib/apt/lists/*

# 复制依赖文件，安装 uv 包管理器
COPY pyproject.toml uv.lock README.md ./
COPY scripts/installation/install_uv.py ./
RUN python install_uv.py && \
    mkdir -p /app/sqlite_db /app/backups

# 复制源码
COPY src/ /app/src/
COPY run_server.py ./
COPY scripts/utils/uv_wrapper.py ./
COPY scripts/utils/memory_wrapper_uv.py ./

# 复制入口脚本
COPY tools/docker/docker-entrypoint.sh /usr/local/bin/
COPY tools/docker/docker-entrypoint-persistent.sh /usr/local/bin/
COPY tools/docker/docker-entrypoint-unified.sh /usr/local/bin/

# 安装 Python 依赖（CPU-only PyTorch 节省空间）
RUN python -m uv pip install torch --index-url https://download.pytorch.org/whl/cpu && \
    python -m uv pip install -e . && \
    python -m uv pip install -e ".[sqlite]"

# 从模型镜像拷贝预下载的 embedding 模型（不需要在线下载）
COPY --from=model /models /root/.cache/torch/sentence_transformers/

# 设置权限
RUN chmod a+rw /dev/stdin /dev/stdout /dev/stderr && \
    chmod +x /usr/local/bin/docker-entrypoint.sh && \
    chmod +x /usr/local/bin/docker-entrypoint-persistent.sh && \
    chmod +x /usr/local/bin/docker-entrypoint-unified.sh

# 数据持久化挂载点
VOLUME ["/app/sqlite_db", "/app/backups"]

# 端口：8000 HTTP API，8765 SSE/Streamable HTTP
EXPOSE 8000 8765

# 统一入口脚本
ENTRYPOINT ["/usr/local/bin/docker-entrypoint-unified.sh"]
EOF
```

#### 2.3 构建并推送

```bash
# 构建（在源码目录下执行）
docker build -f Dockerfile.v5 -t unipus.tencentcloudcr.com/unipus/mcp/memory-mcp:v5 .

# 推送到腾讯云仓库
docker push unipus.tencentcloudcr.com/unipus/mcp/memory-mcp:v5
```

#### 2.4 验证镜像

```bash
# 查看版本
docker run --rm --entrypoint python \
  unipus.tencentcloudcr.com/unipus/mcp/memory-mcp:v5 \
  -c "from mcp_memory_service._version import __version__; print(__version__)"
# 预期输出：10.40.1

# 验证模型可用
docker run --rm --entrypoint python \
  unipus.tencentcloudcr.com/unipus/mcp/memory-mcp:v5 \
  -c "from sentence_transformers import SentenceTransformer; m = SentenceTransformer('all-MiniLM-L6-v2'); print(f'dim={m.get_sentence_embedding_dimension()}')"
# 预期输出：dim=384
```

### 阶段三：更新 K8s 部署

#### 3.1 更新 mcp-all-in-one.yaml

```yaml
# 修改 memory-mcp Deployment 的镜像 tag
containers:
  - name: memory
    image: unipus.tencentcloudcr.com/unipus/mcp/memory-mcp:v5   # v4 → v5
```

#### 3.2 滚动更新

```bash
# 方式 1：apply 整个文件
kubectl apply -f mcp-all-in-one.yaml

# 方式 2：只更新镜像
kubectl -n mcp-system set image deploy/memory-mcp \
  memory=unipus.tencentcloudcr.com/unipus/mcp/memory-mcp:v5

# 查看更新状态
kubectl -n mcp-system rollout status deploy/memory-mcp
```

## 版本对照

| 镜像 Tag | 基础镜像 | 应用版本 | 模型 | 说明 |
|----------|---------|---------|------|------|
| v4 | ghcr.io/doobidoo:latest (v10.33.0) | v10.33.0 | 在线下载 | 旧版，基于预构建镜像 |
| with-model | ghcr.io/doobidoo:latest (v10.33.0) | v10.33.0 | 预装 | v4 + 预装模型 |
| v5 | python:3.11-slim（源码构建） | v10.40.1 | 预装（多阶段） | 最新，108 个 commit 优化 |

## v4 → v5 关键升级

| 变更 | 影响 |
|------|------|
| SQLite-vec 并发 segfault 修复 | 生产稳定性 |
| Milvus 存储后端 | 百万级数据可选 |
| harvest HTTP 端点 | streamable-http 模式也能用 harvest |
| memory_store_session | session-level 存储，R@5 +5.6% |
| 安全补丁（CVE-2026-4539, CVE-2026-39892） | 依赖安全 |

## 后续升级流程

代码更新后只需要重复阶段二：

```bash
cd /tmp/scripts/docker/memory-mcp/source
git pull
docker build -f Dockerfile.v5 -t unipus.tencentcloudcr.com/unipus/mcp/memory-mcp:v6 .
docker push unipus.tencentcloudcr.com/unipus/mcp/memory-mcp:v6
kubectl -n mcp-system set image deploy/memory-mcp \
  memory=unipus.tencentcloudcr.com/unipus/mcp/memory-mcp:v6
```

模型镜像 `embedding-model:v1` 不需要重新构建（除非换模型）。
