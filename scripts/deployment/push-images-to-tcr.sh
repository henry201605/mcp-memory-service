#!/bin/bash
# =============================================================================
# 将所有外网镜像拉取并推送到腾讯云 TCR
# 直接在 K8s 节点上执行：bash push-images-to-tcr.sh
# =============================================================================

set -e

TCR="unipus.tencentcloudcr.com/unipus/mcp"

pull_tag_push() {
    local src=$1
    local dst=$2
    echo "  拉取: ${src}"
    docker pull "${src}"
    echo "  打标: ${dst}"
    docker tag "${src}" "${dst}"
    echo "  推送: ${dst}"
    docker push "${dst}"
    docker rmi "${src}" 2>/dev/null || true
    echo "  ✅ 完成"
    echo ""
}

echo "============================================"
echo "  推送镜像到 TCR: ${TCR}"
echo "============================================"
echo ""

# 1. Dockerfile 基础镜像
echo "[1/5] python:3.12-slim（Dockerfile 基础镜像）"
pull_tag_push "python:3.12-slim" "${TCR}/python:3.12-slim"

# 2. etcd
echo "[2/5] etcd:v3.5.16（Milvus 元数据存储）"
pull_tag_push "quay.io/coreos/etcd:v3.5.16" "${TCR}/etcd:v3.5.16"

# 3. minio
echo "[3/5] minio（Milvus 对象存储）"
pull_tag_push "minio/minio:RELEASE.2024-11-07T00-52-20Z" "${TCR}/minio:RELEASE.2024-11-07T00-52-20Z"

# 4. milvus
echo "[4/5] milvus:v2.5.6（向量数据库）"
pull_tag_push "milvusdb/milvus:v2.5.6" "${TCR}/milvus:v2.5.6"

# 5. busybox
echo "[5/5] busybox:1.36（initContainer 等待脚本）"
pull_tag_push "busybox:1.36" "${TCR}/busybox:1.36"

echo "============================================"
echo "  全部完成！"
echo "============================================"
