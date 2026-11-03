#!/usr/bin/env bash
set -Eeuo pipefail
if [[ "$EUID" -ne 0 ]]; then
  echo 'Запустите: sudo bash scripts/update.sh' >&2
  exit 1
fi
cd /opt/funpayrus
if [[ -n "$(git status --porcelain)" ]]; then
  echo '❌ Есть локальные изменения. Обновление прервано, файлы не затронуты.' >&2
  exit 1
fi
printf 'Проверяем обновления FunPay Rus...\n'
git fetch origin main
CURRENT="$(git rev-parse HEAD)"
LATEST="$(git rev-parse origin/main)"
if [[ "$CURRENT" == "$LATEST" ]]; then
  echo '✅ Уже последняя версия.'
  exit 0
fi
printf 'Текущая версия Git: %s\nНовая версия Git: %s\n' "$CURRENT" "$LATEST"
read -r -p 'Установить обновление из GitHub? (yes/no): ' ANSWER </dev/tty
if [[ "$ANSWER" != 'yes' ]]; then
  echo 'Отменено, ничего не изменено.'
  exit 0
fi
# Secrets live outside the repo and will not be overwritten by git pull.
git pull --ff-only origin main
systemctl restart funpayrus
sleep 3
if systemctl is-active --quiet funpayrus; then
  echo '✅ Приложение перезапущено с новой версией.'
else
  echo '❌ Сервис не запустился. Требуется ручная проверка; старый коммит:' "$CURRENT" >&2
  exit 1
fi
