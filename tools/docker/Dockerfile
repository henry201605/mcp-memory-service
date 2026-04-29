## Slim (CPU-only) Docker image optimized for sqlite-vec + ONNX
## Consolidated as the primary Dockerfile to avoid confusion.
## Use TCR mirror for faster pulls in China; override with --build-arg BASE_IMAGE=python:3.12-slim for overseas builds.
ARG BASE_IMAGE=unipus.tencentcloudcr.com/unipus/mcp/python-torch-cpu:3.12-slim
FROM ${BASE_IMAGE}

# Build arguments for conditional features
ARG SKIP_MODEL_DOWNLOAD=false
ARG INSTALL_EXTRA="[sqlite]"

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    MCP_MEMORY_STORAGE_BACKEND=sqlite_vec \
    MCP_MEMORY_SQLITE_PATH=/app/sqlite_db \
    MCP_MEMORY_BACKUPS_PATH=/app/backups \
    PYTHONPATH=/app/src \
    DOCKER_CONTAINER=1 \
    CHROMA_TELEMETRY_IMPL=none \
    ANONYMIZED_TELEMETRY=false \
    HF_HUB_DISABLE_TELEMETRY=1

# Set the working directory
WORKDIR /app

# Minimal system packages with security updates
# Use Tencent Cloud mirror for faster apt downloads in China
# hadolint ignore=DL3008
RUN sed -i 's|deb.debian.org|mirrors.cloud.tencent.com|g' /etc/apt/sources.list.d/debian.sources && \
    apt-get update && \
    apt-get install -y --no-install-recommends \
    curl \
    bash \
    && apt-get upgrade -y \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

# ---------------------------------------------------------------------------
# Layer-cache optimization: install dependencies BEFORE copying source code.
#
# pyproject.toml / uv.lock change rarely; src/ changes on every commit.
# By installing deps first with a minimal skeleton src/, the expensive
# pip-install layer stays cached across pure code changes.
# ---------------------------------------------------------------------------

# Copy dependency declarations + installer
COPY pyproject.toml .
COPY uv.lock .
COPY README.md .
COPY scripts/installation/install_uv.py .

RUN python install_uv.py && \
    mkdir -p /app/sqlite_db /app/backups

# Create a minimal package skeleton so `pip install -e .` can resolve the
# project metadata and install all dependencies. The real src/ is copied
# later and overwrites this skeleton — editable installs use path references,
# so Python picks up the new code automatically.
RUN mkdir -p src/mcp_memory_service && \
    echo '__version__ = "0.0.0"' > src/mcp_memory_service/__init__.py

# Install the package with UV (configurable dependency group)
# All packages downloaded from Tencent Cloud PyPI mirror.
# torch is pre-installed in the base image (TCR python-torch-cpu) to avoid
# downloading from external PyTorch CDN during build.
ARG PIP_INDEX_URL=https://mirrors.cloud.tencent.com/pypi/simple/
ARG PIP_TRUSTED_HOST=mirrors.cloud.tencent.com
RUN echo "Installing core dependencies..." && \
    python -m uv pip install -e . \
        --index-url ${PIP_INDEX_URL} && \
    if [ -n "$INSTALL_EXTRA" ] && [ "$INSTALL_EXTRA" != "[]" ]; then \
        echo "Installing optional dependencies: ${INSTALL_EXTRA}..." && \
        python -m uv pip install -e ".${INSTALL_EXTRA}" \
            --index-url ${PIP_INDEX_URL}; \
    fi

# Conditionally pre-download ONNX models for lightweight embedding
# (only depends on installed packages, not project source code)
RUN if [ "$SKIP_MODEL_DOWNLOAD" != "true" ]; then \
        echo "Pre-downloading ONNX embedding models..." && \
        python -c "import onnxruntime as ort; print('ONNX runtime available for lightweight embeddings'); print('ONNX models will be downloaded at runtime as needed')" \
            || echo "ONNX check failed, continuing..."; \
    else \
        echo "Skipping model download (SKIP_MODEL_DOWNLOAD=true)"; \
    fi

# ---------------------------------------------------------------------------
# From here on, layers change on every code commit — but they are all cheap
# file copies, no network or compilation involved.
# ---------------------------------------------------------------------------

# Copy source code (overwrites the skeleton created above)
COPY src/ /app/src/
COPY run_server.py ./

# Copy utility scripts
COPY scripts/utils/uv_wrapper.py ./uv_wrapper.py
COPY scripts/utils/memory_wrapper_uv.py ./memory_wrapper_uv.py

# Copy Docker entrypoint scripts
COPY tools/docker/docker-entrypoint.sh /usr/local/bin/
COPY tools/docker/docker-entrypoint-persistent.sh /usr/local/bin/
COPY tools/docker/docker-entrypoint-unified.sh /usr/local/bin/

# Configure stdio for MCP communication and make entrypoints executable
RUN chmod a+rw /dev/stdin /dev/stdout /dev/stderr && \
    chmod +x /usr/local/bin/docker-entrypoint.sh && \
    chmod +x /usr/local/bin/docker-entrypoint-persistent.sh && \
    chmod +x /usr/local/bin/docker-entrypoint-unified.sh

# Add volume mount points for data persistence
VOLUME ["/app/sqlite_db", "/app/backups"]

# Expose ports: 8000 for HTTP API, 8765 for SSE/Streamable HTTP transport
EXPOSE 8000 8765

# Use the unified entrypoint script by default
# Can be overridden with docker-entrypoint.sh for backward compatibility
ENTRYPOINT ["/usr/local/bin/docker-entrypoint-unified.sh"]
