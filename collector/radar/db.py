"""Acesso ao PostgreSQL: conexão com linhas em dict, execução curta e trilha de auditoria."""
from __future__ import annotations

from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb


def connect(dsn: str, *, autocommit: bool = False) -> psycopg.Connection:
    return psycopg.connect(dsn, row_factory=dict_row, autocommit=autocommit)


def run(conn: psycopg.Connection, sql: str, params: Any = None) -> psycopg.Cursor:
    """Executa e devolve o cursor com linhas em dict (independe do row_factory da conexão)."""
    cur = conn.cursor(row_factory=dict_row)
    cur.execute(sql, params)
    return cur


def jsonb(value: Any) -> Jsonb:
    return Jsonb(value, dumps=lambda o: __import__("json").dumps(o, ensure_ascii=False, default=str))


def audit(conn: psycopg.Connection, actor: str, event: str, entity_type: str | None = None,
          entity_id: str | None = None, details: dict | None = None) -> None:
    run(conn, "INSERT INTO audit_events (actor, event, entity_type, entity_id, details) VALUES (%s,%s,%s,%s,%s)",
        (actor, event, entity_type, entity_id, jsonb(details or {})))
