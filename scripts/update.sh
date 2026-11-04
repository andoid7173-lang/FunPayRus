#!/usr/bin/env bash
set -Eeuo pipefail

if [[ "$EUID" -ne 0 ]]; then
  echo 'Запустите: sudo bash scripts/update.sh' >&2
  exit 1
fi
cd /opt/funpayrus
if [[ -n "$(git status --porcelain)" ]]; then
  echo '❌ Есть локальные изменения. Обновление прервано.' >&2
  exit 1
fi
if [[ "$(git branch --show-current)" != "main" ]]; then
  echo '❌ Обновление через этот скрипт разрешено только для main.' >&2
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
if ! git merge-base --is-ancestor "$CURRENT" "$LATEST"; then
  echo '❌ Невозможно безопасное fast-forward обновление.' >&2
  exit 1
fi
printf 'Текущий коммит: %s\nНовый коммит: %s\n' "$CURRENT" "$LATEST"
read -r -p 'Применить обновление? (yes/no): ' ANSWER </dev/tty
if [[ "$ANSWER" != 'yes' ]]; then
  echo 'Отменено, ничего не изменено.'
  exit 0
fi

umask 077
BACKUP_ROOT=/var/backups/funpayrus
install -d -m 0700 -o root -g root "$BACKUP_ROOT"
BACKUP="$(mktemp -d "$BACKUP_ROOT/preupdate-XXXXXXXX")"
printf '%s\n' "$CURRENT" > "$BACKUP/old-commit.txt"
backup_paths=()
[[ -d /etc/funpayrus ]] && backup_paths+=(etc/funpayrus)
[[ -d /var/lib/funpayrus ]] && backup_paths+=(var/lib/funpayrus)
if (( ${#backup_paths[@]} )); then
  tar -C / -czf "$BACKUP/config-and-user-data.tar.gz" -- "${backup_paths[@]}"
fi
echo "Резервная копия: $BACKUP"

rollback() {
  echo '❌ Обновление не прошло проверку; откатываем только Git-код...' >&2
  if [[ "$(git rev-parse HEAD)" == "$LATEST" ]]; then
    # Checkout started clean, all user data lives outside Git.
    git reset --hard "$CURRENT"
  fi
  if systemctl restart funpayrus && systemctl is-active --quiet funpayrus; then
    echo '✅ Предыдущий код восстановлен, служба работает.' >&2
  else
    echo '⚠️ Предыдущая служба не стартовала; нужна ручная диагностика.' >&2
  fi
  echo "Резервная копия: $BACKUP" >&2
  exit 1
}

if ! git merge --ff-only "$LATEST"; then
  echo '❌ Ошибка Git. Проверьте состояние дерева вручную.' >&2
  exit 1
fi
if ! python3 -m unittest discover -s tests -q || ! python3 -m compileall -q bot plugins || ! bash -n install.sh scripts/*.sh; then
  rollback
fi
if ! systemctl restart funpayrus; then
  rollback
fi
sleep 4
if ! systemctl is-active --quiet funpayrus; then
  rollback
fi
echo '✅ Обновление установлено и прошло основные проверки.'
echo 'Для обновления ограничений systemd после проверки используйте установщик вручную.'
