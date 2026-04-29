# 构建 PyTorch CPU-only 基础镜像

在 K8s 节点上执行，构建预装 CPU-only torch 的 Python 基础镜像并推送到 TCR。
此镜像作为 mcp-memory-service Dockerfile 的 `FROM` 基础镜像，避免每次构建都从外网下载 torch。

## 第一步：生成 Dockerfile

```bash
cat << 'EOF' > /tmp/Dockerfile.torch-cpu
FROM unipus.tencentcloudcr.com/unipus/mcp/python:3.12-slim
RUN pip install uv && \
    python -m uv pip install torch --index-url https://download.pytorch.org/whl/cpu
EOF
```

## 第二步：构建并推送

```bash
docker build -f /tmp/Dockerfile.torch-cpu -t unipus.tencentcloudcr.com/unipus/mcp/python-torch-cpu:3.12-slim /tmp
docker push unipus.tencentcloudcr.com/unipus/mcp/python-torch-cpu:3.12-slim
```

## 第三步：清理

```bash
rm -f /tmp/Dockerfile.torch-cpu
```

## 说明

- 这个镜像只需要构建一次，除非要升级 torch 版本
- CPU-only torch 约 200MB，比 CUDA 版 2GB+ 小很多
- 构建完成后，mcp-memory-service 的 Dockerfile `FROM` 直接用这个镜像，后续构建零外网请求
