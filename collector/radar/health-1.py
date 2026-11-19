"""Saúde do worker para o healthcheck do Docker: o loop grava um heartbeat a cada tick."""
from __future__ import annotations

from datetime import datetime

import psycopg


def worker_health(conn: psycopg.Connection, now: datetime, *, max_age_s: int = 900) -> tuple[bool, str]:
    row = conn.execute("SELECT last_seen FROM service_heartbeats WHERE service = 'worker'").fetchone()
    if row is None:
        return False, "worker nunca registrou heartbeat"
    age = int((now - row["last_seen"]).total_seconds())
    if age > max_age_s:
        return False, f"último heartbeat há {age}s (limite {max_age_s}s)"
    return True, f"ok: último heartbeat há {max(age, 0)}s"
