"""Worker 24/7: agenda fontes vencidas, executa jobs da fila, fecha prazos vencidos, dispara alertas e marca heartbeat."""
from __future__ import annotations

import logging
import os
import threading
from datetime import datetime, timezone
from typing import Mapping

import psycopg

from .alerts import channels_from_env
from .config import Config
from .db import audit, connect, jsonb, run
from .jobqueue import Job, claim, complete, enqueue, fail, release_stale
from .lifecycle import refresh_opportunities
from .llm import ClaudeAuditor, OpenAIExtractor
from .notify import dispatch_alerts
from .pipeline import Deps, run_source

log = logging.getLogger("radar.worker")
STALE_AFTER_S = 1800


def deps_from_env(cfg: Config, env: Mapping[str, str] | None = None) -> Deps:
    env = os.environ if env is None else env
    return Deps(extractor=OpenAIExtractor(cfg, env), auditor=ClaudeAuditor(cfg, env), senders=channels_from_env(cfg, env), env=env)


def schedule_due_sources(conn: psycopg.Connection, cfg: Config, now: datetime) -> int:
    """Enfileira um scan por fonte habilitada, com ToS 'allowed' e vencida. dedupe_key impede fila duplicada."""
    rows = run(conn, "SELECT id FROM sources WHERE enabled AND tos_status='allowed' AND (next_run_at IS NULL OR next_run_at <= %s) ORDER BY next_run_at NULLS FIRST", (now,)).fetchall()
    n = 0
    for r in rows:
        if enqueue(conn, "scan_source", {"source_id": r["id"], "trigger": "schedule"}, now=now, dedupe_key=f"scan:{r['id']}",
                   max_attempts=cfg.queue.max_attempts) is not None:
            n += 1
    return n


def handle_job(conn: psycopg.Connection, cfg: Config, deps: Deps, job: Job, now: datetime) -> None:
    if job.kind == "scan_source":
        run_source(conn, cfg, job.payload["source_id"], trigger=job.payload.get("trigger", "schedule"), now=now, deps=deps)
    else:
        raise ValueError(f"tipo de job desconhecido: {job.kind}")


def tick(conn: psycopg.Connection, cfg: Config, deps: Deps, now: datetime, *, worker: str = "worker", max_jobs: int = 10) -> dict:
    out = {"scheduled": 0, "jobs_done": 0, "jobs_failed": 0, "closed": 0, "urgent": 0, "alerts_sent": 0, "alerts_waiting": 0}
    release_stale(conn, now, STALE_AFTER_S)
    out["scheduled"] = schedule_due_sources(conn, cfg, now)
    for _ in range(max_jobs):
        job = claim(conn, worker, now)
        if job is None:
            break
        try:
            handle_job(conn, cfg, deps, job, now)
        except Exception as exc:  # bug/queda inesperada: backoff e, esgotadas as tentativas, DLQ
            outcome = fail(conn, job, f"{exc.__class__.__name__}: {exc}", cfg.queue, now)
            out["jobs_failed"] += 1
            log.error("job falhou", extra={"job": job.id, "kind": job.kind, "outcome": outcome})
            if outcome == "dead":
                audit(conn, worker, "job_dead", "job", str(job.id), {"kind": job.kind})
        else:
            complete(conn, job.id, now)
            out["jobs_done"] += 1
    r = refresh_opportunities(conn, cfg, now)
    out["closed"], out["urgent"] = r["closed"], r["urgent"]
    d = dispatch_alerts(conn, cfg, deps.senders, now)
    out["alerts_sent"], out["alerts_waiting"] = d["sent"], d["waiting"]
    with conn.transaction():
        run(conn, "INSERT INTO service_heartbeats (service, last_seen, info) VALUES ('worker', %s, %s) "
                  "ON CONFLICT (service) DO UPDATE SET last_seen=EXCLUDED.last_seen, info=EXCLUDED.info",
            (now, jsonb({"worker": worker, **out})))
    return out


def run_forever(dsn: str, cfg: Config, deps: Deps, *, worker: str = "worker", stop: threading.Event | None = None) -> None:
    """Loop do serviço. Erros de banco/rede não matam o processo: reconecta e segue."""
    stop = stop or threading.Event()
    conn: psycopg.Connection | None = None
    while not stop.is_set():
        try:
            if conn is None or conn.closed:
                conn = connect(dsn, autocommit=True)
            result = tick(conn, cfg, deps, datetime.now(timezone.utc), worker=worker)
            if any(result[k] for k in ("scheduled", "jobs_done", "jobs_failed", "alerts_sent")):
                log.info("tick", extra=result)
        except Exception as exc:  # noqa: BLE001
            log.error("tick falhou; tentando de novo", extra={"error": f"{exc.__class__.__name__}"})
            try:
                if conn is not None:
                    conn.close()
            finally:
                conn = None
        stop.wait(cfg.scheduler.tick_seconds)
    if conn is not None:
        conn.close()
