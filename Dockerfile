# ── Этап 1: сборка фронтенда (Node) ─────────────────────────────────────────
FROM node:20-slim AS frontend
WORKDIR /fe
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# ── Этап 2: backend (Python) + готовый фронт ────────────────────────────────
FROM python:3.12-slim
WORKDIR /app/backend
COPY backend/ /app/backend/
RUN pip install --no-cache-dir -e .
# main.py ожидает SPA в ../../frontend/dist относительно app/
COPY --from=frontend /fe/dist /app/frontend/dist
EXPOSE 10000
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-10000}"]
