#!/usr/bin/env bash
# Обновление приложения: бэкап → загрузка новой версии → сборка →
# проверка здоровья → откат, если что-то пошло не так.
set -euo pipefail

cd "$(dirname "$0")"
COMPOSE="docker compose -f docker-compose.prod.yml"
STAMP=$(date +%Y%m%d-%H%M%S)
DUMP="backups/predeploy-$STAMP.sql.gz"

# Бэкап снимаем контейнером backup: он ходит на базу по PGHOST из .env и
# работает и с внешним сервером БД. Раньше здесь дёргался контейнер db,
# который на проде вообще не запускается (спрятан за профилем local-db) —
# бэкап молча пропускался при каждом деплое.
echo "▶ Бэкап базы перед обновлением…"
mkdir -p backups
if $COMPOSE exec -T backup sh -c 'pg_dump -h "$PGHOST" -U sendbot sendbot | gzip' > "$DUMP" 2>/dev/null \
   && [ -s "$DUMP" ]; then
  echo "  сохранён: $DUMP ($(du -h "$DUMP" | cut -f1))"
else
  rm -f "$DUMP"
  echo "  ✖ БЭКАП НЕ СНЯЛСЯ. Прерываю: обновляться без бэкапа опасно."
  echo "    Проверьте: $COMPOSE ps backup"
  exit 1
fi

PREV=$(git rev-parse HEAD 2>/dev/null || echo "")
echo "▶ Забираю новую версию из репозитория…"
git fetch --all --prune
git reset --hard origin/main

echo "▶ Собираю и запускаю…"
$COMPOSE up -d --build

# Проверяем не «отвечает ли HTTP», а «работает ли приложение»: живая база и
# все включённые боты действительно поллятся. Именно этого не хватало —
# деплой отчитывался об успехе, когда боты лежали.
echo "▶ Проверяю здоровье (база + боты)…"
health() {
  $COMPOSE exec -T app python -c "
import urllib.request
print(urllib.request.urlopen('http://localhost:8000/api/health', timeout=5).read().decode())
" 2>/dev/null || true
}

OK=0
LAST=""
for _ in $(seq 1 40); do
  LAST=$(health)
  case "$LAST" in
    *'"status":"ok"'*|*'"status": "ok"'*) OK=1; break ;;
  esac
  sleep 3
done

if [ "$OK" != "1" ]; then
  echo "✖ Приложение нездорово после обновления."
  [ -n "$LAST" ] && echo "  health: $LAST"
  echo "  Последние строки лога:"
  $COMPOSE logs --tail=40 app 2>&1 | sed 's/^/    /' || true
  # Лог сохраняем на диск: после отката контейнер пересоберётся и всё пропадёт
  $COMPOSE logs --tail=300 app > "backups/failed-deploy-$STAMP.log" 2>&1 || true
  echo "  Полный лог: backups/failed-deploy-$STAMP.log"
  if [ -n "$PREV" ]; then
    echo "▶ Откатываюсь на предыдущую версию…"
    git reset --hard "$PREV"
    $COMPOSE up -d --build
    echo "  откат выполнен (версия $PREV)"
  fi
  exit 1
fi

echo "▶ Убираю мусор старых сборок…"
docker image prune -f >/dev/null 2>&1 || true

echo "✔ Готово. Версия: $(git rev-parse --short HEAD) — $(git log -1 --pretty=%s)"
echo "  health: $LAST"
