"""Criação e envio de alertas. Idempotente (dedupe_key), com backoff, e sem nunca candidatar-se."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

import psycopg

from .alerts import AlertSendError, render_message
from .config import Config
from .db import jsonb, run
from .logs import redact

log = logging.getLogger("radar.notify")
_FIELD_ORDER = "CASE field WHEN 'value' THEN 0 WHEN 'deadline' THEN 1 WHEN 'status' THEN 2 ELSE 3 END"


def enabled_channels(cfg: Config) -> list[str]:
    return [n for n in ("telegram", "smtp") if getattr(cfg.alerts.channels, n).enabled]


def opportunity_view(conn: psycopg.Connection, opp_id: Any) -> dict | None:
    o = run(conn, "SELECT * FROM opportunities WHERE id=%s", (opp_id,)).fetchone()
    if o is None:
        return None
    ev = run(conn, f"SELECT field, quote FROM evidence_spans WHERE opportunity_id=%s AND version_id IS NOT DISTINCT FROM %s AND verified "
                   f"ORDER BY {_FIELD_ORDER}, created_at LIMIT 5", (opp_id, o["current_version_id"])).fetchall()
    return {**o, "evidence": [{"field": e["field"].replace("requirement:", ""), "quote": e["quote"]} for e in ev]}


def alert_history(conn: psycopg.Connection, opp_id: Any, deadline_key: str) -> tuple[bool, bool]:
    """(já alertada como new/changed?, já alertada como urgente para ESTE prazo?)"""
    r = run(conn,
            "SELECT bool_or(kind IN ('new','changed')) AS ever, bool_or(kind='urgent' AND dedupe_key LIKE %s) AS urgent "
            "FROM alerts WHERE opportunity_id=%s", (f"{opp_id}:urgent:{deadline_key}:%", opp_id)).fetchone()
    return bool(r["ever"]), bool(r["urgent"])


def create_alerts(conn: psycopg.Connection, cfg: Config, opp_id: Any, version_id: Any, kind: str, *, reason: str,
                  deadline_key: str = "none", now: datetime) -> int:
    """Uma linha por canal habilitado; sem credenciais ela espera (pending) e sai quando o canal for configurado."""
    n = 0
    for channel in enabled_channels(cfg):
        key = f"{opp_id}:urgent:{deadline_key}:{channel}" if kind == "urgent" else f"{opp_id}:{kind}:{version_id}:{channel}"
        n += run(conn,
                 "INSERT INTO alerts (opportunity_id, version_id, kind, channel, dedupe_key, payload, created_at) VALUES (%s,%s,%s,%s,%s,%s,%s) "
                 "ON CONFLICT (dedupe_key) DO NOTHING",
                 (opp_id, version_id, kind, channel, key, jsonb({"reason": reason}), now)).rowcount
    return n


def dispatch_alerts(conn: psycopg.Connection, cfg: Config, senders: list, now: datetime, limit: int = 200) -> dict[str, int]:
    by_name = {s.name: s for s in senders}
    out = {"sent": 0, "failed_attempts": 0, "waiting": 0, "suppressed": 0}
    rows = run(conn, "SELECT * FROM alerts WHERE status='pending' AND (next_attempt_at IS NULL OR next_attempt_at <= %s) "
                     "ORDER BY created_at LIMIT %s", (now, limit)).fetchall()
    for a in rows:
        opp = opportunity_view(conn, a["opportunity_id"])
        if opp is None or opp["status"] == "closed" or (opp["deadline_at"] is not None and opp["deadline_at"] < now):
            with conn.transaction():
                run(conn, "UPDATE alerts SET status='suppressed', suppression_reason=%s WHERE id=%s",
                    ("oportunidade encerrada antes do envio", a["id"]))
            out["suppressed"] += 1
            continue
        sender = by_name.get(a["channel"])
        if sender is None:
            with conn.transaction():
                run(conn, "UPDATE alerts SET last_error=%s WHERE id=%s", ("canal sem credenciais configuradas", a["id"]))
            out["waiting"] += 1
            continue
        try:
            sender.send(render_message(opp, a["kind"]))
        except AlertSendError as exc:
            attempts = a["attempts"] + 1
            dead = attempts >= cfg.queue.max_attempts
            delay = min(cfg.queue.backoff_base_seconds * 2 ** (attempts - 1), cfg.queue.backoff_max_seconds)
            with conn.transaction():
                run(conn, "UPDATE alerts SET attempts=%s, last_error=%s, status=%s, next_attempt_at=%s WHERE id=%s",
                    (attempts, redact(str(exc))[:300], "failed" if dead else "pending", None if dead else now + timedelta(seconds=delay), a["id"]))
            out["failed_attempts"] += 1
            log.warning("falha ao enviar alerta", extra={"channel": a["channel"], "attempts": attempts})
            continue
        with conn.transaction():
            run(conn, "UPDATE alerts SET status='sent', sent_at=%s, attempts=attempts+1, last_error=NULL WHERE id=%s", (now, a["id"]))
        out["sent"] += 1
    return out
