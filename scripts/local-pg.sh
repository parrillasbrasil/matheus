#!/usr/bin/env bash
# PostgreSQL local descartável para testes de integração (sandbox/dev sem Docker).
# Uso:  scripts/local-pg.sh start | stop | url
# Em CI/Docker use o serviço `db-test` do docker-compose.test.yml.
set -euo pipefail

PGBIN="${PGBIN:-$(ls -d /usr/lib/postgresql/*/bin | sort -V | tail -1)}"
ROOT="${RADAR_LOCAL_PG_DIR:-/tmp/radar-pg}"
PORT="${RADAR_LOCAL_PG_PORT:-54329}"
RUN_AS=()
if [ "$(id -u)" = "0" ]; then RUN_AS=(runuser -u postgres --); fi

case "${1:-url}" in
  start)
    mkdir -p "$ROOT"; [ "$(id -u)" = "0" ] && chown postgres:postgres "$ROOT"
    if [ ! -d "$ROOT/data" ]; then
      "${RUN_AS[@]}" "$PGBIN/initdb" -D "$ROOT/data" -A trust -U postgres -E UTF8 --locale=C.UTF-8 >/dev/null
    fi
    if ! "${RUN_AS[@]}" "$PGBIN/pg_ctl" -D "$ROOT/data" status >/dev/null 2>&1; then
      "${RUN_AS[@]}" "$PGBIN/pg_ctl" -D "$ROOT/data" -l "$ROOT/pg.log" -w \
        -o "-p $PORT -k $ROOT -c listen_addresses=127.0.0.1 -c fsync=off -c synchronous_commit=off" start >/dev/null
    fi
    echo "postgresql://postgres@127.0.0.1:$PORT/postgres"
    ;;
  stop)
    "${RUN_AS[@]}" "$PGBIN/pg_ctl" -D "$ROOT/data" -m fast stop || true
    ;;
  url)
    echo "postgresql://postgres@127.0.0.1:$PORT/postgres"
    ;;
esac
