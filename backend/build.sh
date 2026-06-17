#!/usr/bin/env bash
# Вспомогательный скрипт сборки для деплоя Saya KDS.
# Повторяет buildCommand из render.yaml. render.yaml НЕ зависит от этого файла
# (там build-команда прописана напрямую), но скрипт удобно запускать вручную
# или подключить в render.yaml как: buildCommand: ./backend/build.sh
#
# Запускать из КОРНЯ репозитория:
#   bash backend/build.sh
set -euo pipefail

# Корень репозитория = на уровень выше папки этого скрипта (backend/).
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

echo "==> Сборка фронтенда (Vite)"
cd "$ROOT/frontend"
npm ci
npm run build           # создаёт frontend/dist

echo "==> Установка бэкенда (Python)"
cd "$ROOT/backend"
pip install -e .

echo "==> Готово. Запуск: cd backend && uvicorn app.main:app --host 0.0.0.0 --port \$PORT"
