FROM python:3.12-slim

ARG UV_VERSION=0.11.16

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

WORKDIR /app

RUN pip install --no-cache-dir "uv==${UV_VERSION}"

COPY pyproject.toml uv.lock ./
COPY app/ ./app/
COPY bin/ ./bin/
COPY migrations/ ./migrations/
COPY alembic.ini ./

RUN uv sync --frozen --no-dev

ENV DATABASE_PATH=/data/food.db
RUN mkdir -p /data

CMD ["uv", "run", "--no-sync", "python", "bin/run.py"]
