#!/usr/bin/env bash
# Обновление приложения: бэкап → загрузка новой версии → сборка →
# проверка здоровья → откат, если что-то пошло не так.
set -euo pipefail

cd "$(dirname "$0")"
COMPOSE="docker compose -f docker-compose.prod.yml"
STAMP=$(date +%Y%m%d-%H%M%S)

echo "▶ Бэкап базы перед обновлением…"
mkdir -p backups
$COMPOSE exec -T db pg_dump -U sendbot sendbot | gzip > "backups/predeploy-$STAMP.sql.gz" || \
  echo "  (база ещё не запущена — пропускаю бэкап)"

PREV=$(git rev-parse HEAD 2>/dev/null || echo "")
echo "▶ Забираю новую версию из репозитория…"
git fetch --all --prune
git reset --hard origin/main

echo "▶ Собираю и запускаю…"
$COMPOSE up -d --build

echo "▶ Проверяю, что приложение живо…"
OK=0
for i in $(seq 1 30); do
  if curl -fsS http://localhost:8000/api/health >/dev/null 2>&1 || \
     $COMPOSE exec -T app python -c "import urllib.request;urllib.request.urlopen('http://localhost:8000/api/health')" >/dev/null 2>&1; then
    OK=1; break
  fi
  sleep 3
done

if [ "$OK" != "1" ]; then
  echo "✖ Приложение не отвечает — откатываюсь на предыдущую версию"
  if [ -n "$PREV" ]; then
    git reset --hard "$PREV"
    $COMPOSE up -d --build
    echo "  откат выполнен (версия $PREV)"
  fi
  exit 1
fi

echo "▶ Убираю мусор старых сборок…"
docker image prune -f >/dev/null 2>&1 || true

echo "✔ Готово. Версия: $(git rev-parse --short HEAD) — $(git log -1 --pretty=%s)"
