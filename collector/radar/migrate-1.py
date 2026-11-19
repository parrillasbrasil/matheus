"""Runner mínimo de migrations SQL (ordenadas por nome, com checksum e trava de concorrência)."""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import psycopg

ADVISORY_LOCK = 727274


class MigrationError(Exception):
    pass


def _checksum(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def migrate(dsn: str, migrations_dir: str | Path) -> list[str]:
    """Aplica migrations pendentes e devolve os nomes aplicados. Lança MigrationError se uma já aplicada foi alterada."""
    files = sorted(Path(migrations_dir).glob("*.sql"))
    if not files:
        raise MigrationError(f"nenhuma migration em {migrations_dir}")
    applied_now: list[str] = []
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute("SELECT pg_advisory_lock(%s)", (ADVISORY_LOCK,))
        try:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations ("
                " version text PRIMARY KEY, checksum text NOT NULL, applied_at timestamptz NOT NULL DEFAULT now())"
            )
            done = {r[0]: r[1] for r in conn.execute("SELECT version, checksum FROM schema_migrations")}
            for f in files:
                digest = _checksum(f)
                if f.name in done:
                    if done[f.name] != digest:
                        raise MigrationError(f"migration {f.name} foi alterada depois de aplicada; crie uma nova migration")
                    continue
                with conn.transaction():
                    conn.execute(f.read_text(encoding="utf-8"))
                    conn.execute("INSERT INTO schema_migrations (version, checksum) VALUES (%s, %s)", (f.name, digest))
                applied_now.append(f.name)
        finally:
            conn.execute("SELECT pg_advisory_unlock(%s)", (ADVISORY_LOCK,))
    return applied_now


def main(argv: list[str] | None = None) -> int:
    import os

    argv = argv if argv is not None else sys.argv[1:]
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("DATABASE_URL não definido", file=sys.stderr)
        return 2
    directory = argv[0] if argv else os.environ.get("RADAR_MIGRATIONS_DIR", "db/migrations")
    applied = migrate(dsn, directory)
    print("migrations aplicadas:", applied or "nenhuma (já atualizado)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
