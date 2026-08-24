FROM node:26-slim AS web-build

WORKDIR /web
COPY web/package*.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

FROM python:3.14-slim

ARG UV_VERSION=0.11.16

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

WORKDIR /app

RUN pip install --no-cache-dir "uv==${UV_VERSION}"

COPY pyproject.toml uv.lock README.md ./
COPY app/ ./app/
COPY bin/ ./bin/
COPY migrations/ ./migrations/
COPY alembic.ini ./
COPY --from=web-build /web/dist ./web/dist

RUN uv sync --frozen --no-dev

ENV DATABASE_PATH=/data/food.db
RUN mkdir -p /data

CMD ["uv", "run", "--no-sync", "python", "bin/run.py"]
