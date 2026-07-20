# ================================================================
# 多阶段构建：生产级 Docker 镜像
# 最终镜像约 500MB（含 Python + 依赖 + ONNX Runtime）
# ================================================================

# --- 第一阶段：构建（安装依赖） ---
FROM python:3.12-slim AS builder

# 安装 uv（生产构建优先走国内 PyPI 镜像，避免 ghcr.io 拉取不稳定）
RUN pip install --no-cache-dir -i https://mirrors.aliyun.com/pypi/simple --trusted-host mirrors.aliyun.com uv

WORKDIR /app

# 先拷贝依赖文件（利用 Docker 缓存层）
COPY pyproject.toml uv.lock README.md ./

# 安装依赖（不装 dev 依赖）
RUN uv sync --frozen --no-dev --no-editable --index-url https://mirrors.aliyun.com/pypi/simple

# --- 第二阶段：运行（精简镜像） ---
FROM python:3.12-slim AS runtime

WORKDIR /app

# 从 builder 阶段拷贝虚拟环境
COPY --from=builder /app/.venv /app/.venv

# 拷贝应用代码
COPY app/ ./app/

# 拷贝版本化 DDL，应用启动时执行尚未应用的迁移
COPY db/ ./db/

# 将版本化评测题库打入运行镜像，生产环境可直接执行回归评测。
COPY docs/evaluation/ ./docs/evaluation/

# 设置环境变量
ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONUNBUFFERED=1
ENV APP_ENV=prod
ENV LOG_CONSOLE=true

# 创建非 root 用户运行（安全）
RUN useradd -m appuser && chown -R appuser:appuser /app
USER appuser

# 创建日志和数据目录
RUN mkdir -p /app/logs /app/data

# 暴露端口
EXPOSE 8000

# 健康检查
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import httpx; httpx.get('http://localhost:8000/api/v1/health/ping')" || exit 1

# 启动命令（单 worker，避免本地 Qdrant 文件锁冲突和内存状态分裂）
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
