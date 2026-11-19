"""Fila de jobs no PostgreSQL: SKIP LOCKED, backoff exponencial com teto, DLQ (status 'dead') e recuperação de travados."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

import psycopg

from .config import QueueCfg
from .db import jsonb, run
from .logs import redact


@dataclass(frozen=True)
class Job:
    id: int
    kind: str
    payload: dict
    attempts: int
    max_attempts: int


def enqueue(conn: psycopg.Connection, kind: str, payload: dict | None = None, *, now: datetime,
            run_at: datetime | None = None, dedupe_key: str | None = None, max_attempts: int = 5) -> int | None:
    """Enfileira. Devolve None se já existe job queued/running com o mesmo dedupe_key."""
    with conn.transaction():
        row = run(
            conn,
            "INSERT INTO jobs (kind, payload, run_at, max_attempts, dedupe_key, created_at) VALUES (%s,%s,%s,%s,%s,%s) "
            "ON CONFLICT (dedupe_key) WHERE dedupe_key IS NOT NULL AND status IN ('queued','running') DO NOTHING RETURNING id",
            (kind, jsonb(payload or {}), run_at or now, max_attempts, dedupe_key, now),
        ).fetchone()
    return row["id"] if row else None


def claim(conn: psycopg.Connection, worker: str, now: datetime, kinds: list[str] | None = None) -> Job | None:
    with conn.transaction():
        row = run(
            conn,
            "WITH next AS (SELECT id FROM jobs WHERE status='queued' AND run_at <= %(now)s "
            "              AND (%(kinds)s::text[] IS NULL OR kind = ANY(%(kinds)s)) "
            "              ORDER BY run_at, id FOR UPDATE SKIP LOCKED LIMIT 1) "
            "UPDATE jobs j SET status='running', attempts = j.attempts + 1, locked_at=%(now)s, locked_by=%(w)s "
            "FROM next WHERE j.id = next.id RETURNING j.id, j.kind, j.payload, j.attempts, j.max_attempts",
            {"now": now, "kinds": kinds, "w": worker},
        ).fetchone()
    return Job(row["id"], row["kind"], row["payload"], row["attempts"], row["max_attempts"]) if row else None


def complete(conn: psycopg.Connection, job_id: int, now: datetime) -> None:
    with conn.transaction():
        run(conn, "UPDATE jobs SET status='done', finished_at=%s, locked_at=NULL, locked_by=NULL, last_error=NULL WHERE id=%s", (now, job_id))


def fail(conn: psycopg.Connection, job: Job, error: str, qcfg: QueueCfg, now: datetime) -> str:
    """Reagenda com backoff exponencial (com teto) ou manda para a DLQ. Devolve 'retry' ou 'dead'."""
    message = redact(error)[:500]
    with conn.transaction():
        if job.attempts >= job.max_attempts:
            run(conn, "UPDATE jobs SET status='dead', finished_at=%s, locked_at=NULL, locked_by=NULL, last_error=%s WHERE id=%s",
                (now, message, job.id))
            return "dead"
        delay = min(qcfg.backoff_base_seconds * 2 ** (job.attempts - 1), qcfg.backoff_max_seconds)
        run(conn, "UPDATE jobs SET status='queued', run_at=%s, locked_at=NULL, locked_by=NULL, last_error=%s WHERE id=%s",
            (now + timedelta(seconds=delay), message, job.id))
        return "retry"


def requeue_dead(conn: psycopg.Connection, job_id: int, now: datetime) -> bool:
    try:
        with conn.transaction():
            cur = run(conn, "UPDATE jobs SET status='queued', attempts=0, run_at=%s, last_error=NULL, finished_at=NULL "
                            "WHERE id=%s AND status='dead'", (now, job_id))
    except psycopg.errors.UniqueViolation:   # já existe outro job queued/running com o mesmo dedupe_key
        return False
    return cur.rowcount == 1


def release_stale(conn: psycopg.Connection, now: datetime, stale_after_s: int) -> int:
    """Jobs 'running' há muito tempo (worker caiu): voltam à fila; se já gastaram as tentativas, vão para a DLQ."""
    cutoff = now - timedelta(seconds=stale_after_s)
    with conn.transaction():
        dead = run(conn, "UPDATE jobs SET status='dead', finished_at=%s, locked_at=NULL, locked_by=NULL, "
                         "last_error='worker travou/caiu durante o processamento' "
                         "WHERE status='running' AND locked_at < %s AND attempts >= max_attempts", (now, cutoff)).rowcount
        back = run(conn, "UPDATE jobs SET status='queued', locked_at=NULL, locked_by=NULL "
                         "WHERE status='running' AND locked_at < %s", (cutoff,)).rowcount
    return dead + back


def stats(conn: psycopg.Connection) -> dict[str, int]:
    out = {"queued": 0, "running": 0, "done": 0, "dead": 0}
    for r in run(conn, "SELECT status, count(*) AS n FROM jobs GROUP BY status").fetchall():
        out[r["status"]] = r["n"]
    return out
