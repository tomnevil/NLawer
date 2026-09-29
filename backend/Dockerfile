# 律小智 NLawer 后端镜像（Python 3.11 + asyncpg + pgvector）
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# 依赖单独成层：代码变更时可复用安装缓存
COPY requirements.txt requirements-postgres.txt ./
RUN pip install --upgrade pip \
    && pip install -r requirements.txt -r requirements-postgres.txt

COPY . .

# 本地存储目录（证据 / 文书 / 开庭材料包）
RUN mkdir -p /app/storage/local

# 以非 root 用户运行，降低容器逃逸风险
RUN useradd --create-home --shell /bin/bash nlawer \
    && chown -R nlawer:nlawer /app
USER nlawer

EXPOSE 8000

# 启动时由 lifespan 调用 init_db()：
# ENVIRONMENT=production 会执行 `alembic upgrade head`，其余环境走 create_all
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
