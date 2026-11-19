"""Controle de custo/tokens (teto diário), cache de respostas e registro de uso por chamada."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import psycopg

from ..config import Config
from ..db import jsonb, run
from ..logs import redact
from .base import LlmCall


def cost_usd(cfg: Config, model: str, input_tokens: int, output_tokens: int) -> float | None:
    """Custo em US$; None quando o modelo não tem preço em config.llm.pricing_usd_per_mtok (custo DESCONHECIDO)."""
    price = cfg.llm.pricing_usd_per_mtok.get(model)
    if price is None:
        return None
    return (input_tokens * price.input + output_tokens * price.output) / 1_000_000


def cache_key(provider: str, model: str, prompt_version: str, content_hash: str, schema_hash: str = "") -> str:
    return hashlib.sha256("|".join([provider, model, prompt_version, content_hash, schema_hash]).encode()).hexdigest()


def cache_get(conn: psycopg.Connection, key: str) -> dict | None:
    row = run(conn, "SELECT response FROM llm_cache WHERE cache_key=%s", (key,)).fetchone()
    return row["response"] if row else None


def cache_put(conn: psycopg.Connection, key: str, provider: str, model: str, response: dict, input_tokens: int, output_tokens: int) -> None:
    with conn.transaction():
        run(conn, "INSERT INTO llm_cache (cache_key, provider, model, response, input_tokens, output_tokens) VALUES (%s,%s,%s,%s,%s,%s) "
                  "ON CONFLICT (cache_key) DO NOTHING", (key, provider, model, jsonb(response), input_tokens, output_tokens))


def record_usage(conn: psycopg.Connection, call: LlmCall | None, cfg: Config, purpose: str, *, now: datetime, cached: bool = False,
                 status: str = "ok", error: str | None = None, provider: str | None = None, model: str | None = None,
                 document_id: str | None = None, opportunity_id: str | None = None) -> None:
    """Chamadas em cache ou com erro não gastam tokens contabilizados; custo sem preço configurado fica 'desconhecido'."""
    provider = call.provider if call else provider
    model = call.model if call else model
    spent = call is not None and status == "ok" and not cached
    tin, tout = (call.input_tokens, call.output_tokens) if spent else (0, 0)
    cost = cost_usd(cfg, model or "", tin, tout) if spent else (0.0 if cached else None)
    with conn.transaction():
        run(conn, "INSERT INTO agent_usage (at, provider, model, purpose, document_id, opportunity_id, input_tokens, output_tokens, "
                  "cost_usd, cost_known, cached, status, error) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (now, provider, model, purpose, document_id, opportunity_id, tin, tout, cost, cost is not None, cached, status,
             redact(error)[:300] if error else None))


@dataclass(frozen=True)
class Usage:
    tokens: int
    cost_usd: float
    unknown_cost_calls: int


def usage_today(conn: psycopg.Connection, now: datetime) -> Usage:
    n = now.astimezone(timezone.utc)
    start = datetime(n.year, n.month, n.day, tzinfo=timezone.utc)
    r = run(conn, "SELECT coalesce(sum(input_tokens + output_tokens),0)::bigint AS tokens, "
                  "coalesce(sum(cost_usd) FILTER (WHERE cost_known),0)::float AS cost, "
                  "count(*) FILTER (WHERE status='ok' AND NOT cached AND NOT cost_known) AS unknown "
                  "FROM agent_usage WHERE at >= %s AND at < %s", (start, start + timedelta(days=1))).fetchone()
    return Usage(int(r["tokens"]), float(r["cost"]), int(r["unknown"]))


def can_spend(conn: psycopg.Connection, cfg: Config, now: datetime, estimated_tokens: int = 0) -> tuple[bool, str]:
    b, u = cfg.llm.budget, usage_today(conn, now)
    if u.tokens + estimated_tokens > b.daily_token_cap:
        return False, f"teto diário de tokens atingido ({u.tokens} usados + {estimated_tokens} estimados > {b.daily_token_cap})"
    if u.cost_usd >= b.daily_usd_cap:
        return False, f"teto diário de US$ {b.daily_usd_cap:.2f} atingido (gasto conhecido US$ {u.cost_usd:.2f})"
    return True, ""
