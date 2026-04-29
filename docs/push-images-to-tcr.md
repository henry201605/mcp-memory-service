# 推送外网镜像到腾讯云 TCR

在 K8s 节点终端上依次执行以下两步：

## 第一步：生成脚本

```bash
cat << 'EOF' > /tmp/push-images-to-tcr.sh
#!/bin/bash
set -e

TCR="unipus.tencentcloudcr.com/unipus/mcp"
# 腾讯云 TKE 内置的 Docker Hub 镜像加速
MIRROR="mirror.ccs.tencentyun.com"

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
echo "  使用加速器: ${MIRROR}"
echo "============================================"
echo ""

# Docker Hub 官方镜像（library/xxx）通过腾讯云加速拉取
echo "[1/5] python:3.12-slim（Dockerfile 基础镜像）"
pull_tag_push "${MIRROR}/library/python:3.12-slim" "${TCR}/python:3.12-slim"

echo "[2/5] busybox:1.36（initContainer 等待脚本）"
pull_tag_push "${MIRROR}/library/busybox:1.36" "${TCR}/busybox:1.36"

# Docker Hub 第三方镜像通过腾讯云加速拉取
echo "[3/5] minio（Milvus 对象存储）"
pull_tag_push "${MIRROR}/minio/minio:RELEASE.2024-11-07T00-52-20Z" "${TCR}/minio:RELEASE.2024-11-07T00-52-20Z"

echo "[4/5] milvus:v2.5.6（向量数据库）"
pull_tag_push "${MIRROR}/milvusdb/milvus:v2.5.6" "${TCR}/milvus:v2.5.6"

# quay.io 镜像 — 腾讯云加速不支持 quay.io，尝试直连
echo "[5/5] etcd:v3.5.16（Milvus 元数据存储）"
# 优先尝试 Docker Hub 上的 etcd 镜像，失败则直连 quay.io
pull_tag_push "${MIRROR}/bitnami/etcd:3.5.16" "${TCR}/etcd:v3.5.16" \
  || pull_tag_push "quay.io/coreos/etcd:v3.5.16" "${TCR}/etcd:v3.5.16"

echo "============================================"
echo "  全部完成！"
echo "============================================"
EOF
```

## 第二步：执行脚本

```bash
bash /tmp/push-images-to-tcr.sh
```

## 清理

```bash
rm -f /tmp/push-images-to-tcr.sh
```

## 备注

- 使用 `mirror.ccs.tencentyun.com` 作为 Docker Hub 加速器（TKE 节点内置）
- 如果加速器也不通，可以尝试换成 `hub-mirror.c.163.com` 或配置 daemon.json 代理
- etcd 原始源是 quay.io，腾讯云加速不覆盖，脚本会先尝试 Docker Hub 上的 bitnami/etcd，失败再直连 quay.io
