#!/usr/bin/env bash
# Backup lógico do PostgreSQL (pg_dump -Fc) com retenção.
#   DATABASE_URL  ou as variáveis padrão do libpq (PGHOST, PGUSER, PGPASSWORD, PGDATABASE)
#   BACKUP_DIR              pasta de destino (padrão /backups)
#   RETENTION_DAYS          quantos dias de backup manter (padrão 14)
#   BACKUP_INTERVAL_SECONDS se definido, repete em loop (usado pelo serviço `backup` do docker-compose)
# Restaurar:  pg_restore --clean --if-exists --no-owner -d "$DATABASE_URL" /backups/radar-AAAAMMDD-HHMMSS.dump
set -euo pipefail

BACKUP_DIR="${BACKUP_DIR:-/backups}"
RETENTION_DAYS="${RETENTION_DAYS:-14}"

# Cada passo checa o próprio erro: dentro de `backup_once || ...` o `set -e` não vale, então não dá para confiar nele.
backup_once() {
  mkdir -p "$BACKUP_DIR" || return 1
  local stamp final partial
  stamp="$(date -u +%Y%m%d-%H%M%S)"
  final="$BACKUP_DIR/radar-$stamp.dump"
  partial="$final.partial"
  if [ -n "${DATABASE_URL:-}" ]; then
    pg_dump --format=custom --no-owner --file="$partial" "$DATABASE_URL" || { rm -f "$partial"; return 1; }
  else
    pg_dump --format=custom --no-owner --file="$partial" || { rm -f "$partial"; return 1; }
  fi
  # só vale como backup se o arquivo for legível
  pg_restore --list "$partial" > /dev/null || { rm -f "$partial"; return 1; }
  mv "$partial" "$final" || { rm -f "$partial"; return 1; }
  find "$BACKUP_DIR" -maxdepth 1 -type f -name 'radar-*.dump' -mtime "+$RETENTION_DAYS" -delete || return 1
  echo "backup ok: $final"
}

if [ -n "${BACKUP_INTERVAL_SECONDS:-}" ]; then
  while true; do
    backup_once || echo "backup FALHOU em $(date -u +%FT%TZ)" >&2
    sleep "$BACKUP_INTERVAL_SECONDS"
  done
else
  backup_once
fi
