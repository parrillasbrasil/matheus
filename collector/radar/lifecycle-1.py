"""Tarefas dependentes do TEMPO (rodam a cada tick, mesmo sem a fonte ter mudado)."""
from __future__ import annotations

from datetime import datetime, timedelta

import psycopg

from .alerts import AlertInput, decide_alert
from .config import Config
from .db import audit, run
from .deadlines import ResolvedDeadline
from .notify import alert_history, create_alerts


def refresh_opportunities(conn: psycopg.Connection, cfg: Config, now: datetime) -> dict[str, int]:
    """1) fecha oportunidades cujo prazo passou; 2) dispara o alerta URGENTE (uma vez por prazo) quando o prazo entra na janela."""
    out = {"closed": 0, "urgent": 0}
    with conn.transaction():
        rows = run(conn, "UPDATE opportunities SET status='closed', updated_at=%s WHERE status <> 'closed' AND deadline_at IS NOT NULL "
                         "AND deadline_at < %s RETURNING id, canonical_url, deadline_at", (now, now)).fetchall()
        for r in rows:
            audit(conn, "worker", "opportunity_closed_by_deadline", "opportunity", str(r["id"]),
                  {"url": r["canonical_url"], "deadline_at": r["deadline_at"].isoformat()})
        out["closed"] = len(rows)

    window = now + timedelta(days=cfg.alerts.urgent.within_days)
    candidates = run(conn, "SELECT * FROM opportunities WHERE status='open' AND deadline_at > %s AND deadline_at <= %s", (now, window)).fetchall()
    for o in candidates:
        evidence = run(conn, "SELECT count(*) AS n FROM evidence_spans WHERE opportunity_id=%s AND version_id IS NOT DISTINCT FROM %s AND verified",
                       (o["id"], o["current_version_id"])).fetchone()["n"]
        deadline_key = o["deadline_at"].date().isoformat()
        ever, urgent_sent = alert_history(conn, o["id"], deadline_key)
        decision = decide_alert(
            cfg,
            AlertInput(status=o["status"], eligibility_status=o["eligibility_status"], score=float(o["current_score"] or 0),
                       is_official=o["is_official"], verified_evidence=evidence,
                       deadline=ResolvedDeadline(o["deadline_at"], o["deadline_tz"], o["deadline_tz_assumed"], o["deadline_confirmed"])),
            change="unchanged", meaningful_change=False, ever_alerted=ever, urgent_sent=urgent_sent, now=now)
        if decision.send and decision.kind == "urgent":
            with conn.transaction():
                out["urgent"] += 1 if create_alerts(conn, cfg, o["id"], o["current_version_id"], "urgent", reason=decision.reason,
                                                    deadline_key=deadline_key, now=now) else 0
    return out
