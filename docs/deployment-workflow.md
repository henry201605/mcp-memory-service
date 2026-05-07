# mcp-memory-service 部署流程

从本地打包 → 上传服务器 → 构建镜像 → 部署到 K8s 的完整流程。

## 前置条件

- 服务器：K8s 节点已登录 TCR（`unipus.tencentcloudcr.com`）
- 服务器：已推送过基础镜像到 TCR（`python-torch-cpu:3.12-slim`、`etcd`、`minio`、`milvus`、`busybox`、`embedding-model`）
- 服务器：Dockerfile 已就位在 `/tmp/scripts/memory/milvus/Dockerfile`
- 本地：项目根目录为 `mcp-memory-service/`

## 第一步：本地打包

在项目根目录执行：

```bash
tar czf /tmp/mcp-memory-service.tar.gz \
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
  --exclude='.kiro' \
  --exclude='backups' \
  --exclude='.coverage' \
  --exclude='data' \
  .
```

压缩包大约 2-5MB。

## 第二步：上传到 K8s 节点

scp 直连 K8s 节点不通（密钥认证限制），需要通过跳板机中转：

```bash
# 本地 → 跳板机
scp /tmp/mcp-memory-service.tar.gz root@<跳板机IP>:/tmp/

# 跳板机 → K8s 节点
ssh root@<跳板机IP>
scp /tmp/mcp-memory-service.tar.gz root@10.60.0.12:/tmp/
```

或者通过腾讯云控制台的 WebShell 上传（文件上传功能）。

## 第三步：在 K8s 节点解压

```bash
# 清理旧目录
rm -rf /tmp/scripts/memory/file-milvus

# 解压到指定目录
mkdir -p /tmp/scripts/memory/file-milvus
tar xzf /tmp/mcp-memory-service.tar.gz -C /tmp/scripts/memory/file-milvus

# 清理压缩包
rm -f /tmp/mcp-memory-service.tar.gz
```

## 第四步：构建镜像

```bash
docker build -f /tmp/scripts/memory/milvus/Dockerfile \
  --build-arg INSTALL_EXTRA="[milvus]" \
  -t unipus.tencentcloudcr.com/unipus/mcp/memory-mcp:milvus-cpu \
  /tmp/scripts/memory/file-milvus
```

说明：
- Dockerfile 在 `/tmp/scripts/memory/milvus/Dockerfile`（代码之外，重用）
- 代码目录 `/tmp/scripts/memory/file-milvus` 作为 build context
- 构建走腾讯云内网源（apt、PyPI），torch 预装在基础镜像中，零外网请求
- 代码改动时 Docker 层缓存生效，几十秒完成（依赖层不重装）

首次构建或强制全量重建：

```bash
docker build -f /tmp/scripts/memory/milvus/Dockerfile \
  --build-arg INSTALL_EXTRA="[milvus]" \
  --no-cache \
  -t unipus.tencentcloudcr.com/unipus/mcp/memory-mcp:milvus-cpu \
  /tmp/scripts/memory/file-milvus
```

## 第五步：推送镜像到 TCR

```bash
docker push unipus.tencentcloudcr.com/unipus/mcp/memory-mcp:milvus-cpu
```

## 第六步：滚动更新 K8s Deployment

```bash
# 触发滚动更新
kubectl -n mcp-system rollout restart deploy/memory-mcp-milvus

# 等待完成（模型加载需 30-120 秒）
kubectl -n mcp-system rollout status deploy/memory-mcp-milvus
```

实时观察 Pod 状态：

```bash
kubectl -n mcp-system get pods -l app=memory-mcp-milvus -w
```

## 第七步：验证

```bash
# 查看日志
kubectl -n mcp-system logs deploy/memory-mcp-milvus -c memory --tail=20

# 检查环境变量
kubectl exec -n mcp-system deploy/memory-mcp-milvus -- env | grep -i -E "model|embedding|backend|milvus"
```

服务地址：`http://semgrep-mcp.unipus.cn/mcp`

Kiro/Claude 客户端配置：

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

## 一键执行脚本（本地）

保存为 `scripts/deployment/deploy-to-k8s.sh`：

```bash
#!/bin/bash
set -e

JUMP_HOST="root@<跳板机IP>"
K8S_NODE="root@10.60.0.12"
TAR_FILE="/tmp/mcp-memory-service.tar.gz"

echo "[1/3] 本地打包..."
tar czf ${TAR_FILE} \
  --exclude='.git' --exclude='.venv' --exclude='__pycache__' \
  --exclude='node_modules' --exclude='archive' --exclude='.gitnexus' \
  --exclude='.kiro' --exclude='*.sqlite' --exclude='backups' --exclude='data' \
  --exclude='.DS_Store' \
  .

echo "[2/3] 上传到跳板机..."
scp ${TAR_FILE} ${JUMP_HOST}:/tmp/

echo "[3/3] 跳板机中转到 K8s 节点并构建..."
ssh ${JUMP_HOST} << REMOTE_EOF
scp /tmp/mcp-memory-service.tar.gz ${K8S_NODE}:/tmp/
ssh ${K8S_NODE} << NODE_EOF
rm -rf /tmp/scripts/memory/file-milvus
mkdir -p /tmp/scripts/memory/file-milvus
tar xzf /tmp/mcp-memory-service.tar.gz -C /tmp/scripts/memory/file-milvus
rm -f /tmp/mcp-memory-service.tar.gz
docker build -f /tmp/scripts/memory/milvus/Dockerfile \
  --build-arg INSTALL_EXTRA="[milvus]" \
  -t unipus.tencentcloudcr.com/unipus/mcp/memory-mcp:milvus-cpu \
  /tmp/scripts/memory/file-milvus
docker push unipus.tencentcloudcr.com/unipus/mcp/memory-mcp:milvus-cpu
kubectl -n mcp-system rollout restart deploy/memory-mcp-milvus
kubectl -n mcp-system rollout status deploy/memory-mcp-milvus
NODE_EOF
REMOTE_EOF

echo "✅ 部署完成"
rm -f ${TAR_FILE}
```

使用前替换 `<跳板机IP>` 为真实 IP。

## 文件布局总览

本地：
```
mcp-memory-service/
├── tools/docker/Dockerfile         # 原始 Dockerfile（供参考）
├── src/                            # 源码
├── pyproject.toml                  # 依赖声明
└── ...
```

K8s 节点：
```
/tmp/scripts/memory/
├── milvus/
│   └── Dockerfile                  # 定制 Dockerfile（腾讯云内网源，不随代码更新）
└── file-milvus/                    # 解压的项目代码（每次部署重新解压）
    ├── src/
    ├── pyproject.toml
    └── ...
```

## 踩坑记录

- **scp 直连失败**：K8s 节点只允许密钥登录，不接受密码 → 通过跳板机中转
- **构建卡在 Step 14**：默认下载 CUDA 版 PyTorch（~2GB） → 用预装 CPU-only torch 的基础镜像
- **apt-get 卡住**：Debian 官方源外网慢 → sed 换腾讯云镜像
- **pip 卡住**：PyPI 官方源外网慢 → 加 `--index-url` 指向腾讯云 PyPI
