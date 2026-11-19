"""Sincroniza config.yaml -> banco (fontes e perfil do candidato). O config é a fonte única de regras."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Mapping

import psycopg

from .config import Config
from .db import jsonb, run


def _hash(obj: object) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()


def sync_sources(conn: psycopg.Connection, cfg: Config, env: Mapping[str, str], now: datetime) -> None:
    in_test = env.get("RADAR_ENV") == "test"
    ids = [s.id for s in cfg.sources]
    with conn.transaction():
        for s in cfg.sources:
            enabled = s.enabled and (s.origin != "fixture" or in_test)   # fixture NUNCA roda fora de teste
            run(conn,
                "INSERT INTO sources (id,name,kind,role,origin,enabled,url,country,language,interval_minutes,rate_limit,tos_status,tos_note,"
                " tos_reviewed_on,tos_evidence_url,options,next_run_at,config_hash,created_at,updated_at) "
                "VALUES (%(id)s,%(name)s,%(kind)s,%(role)s,%(origin)s,%(enabled)s,%(url)s,%(country)s,%(language)s,%(interval)s,%(rate)s,%(tos)s,"
                " %(note)s,%(reviewed)s,%(evidence)s,%(options)s,%(now)s,%(hash)s,%(now)s,%(now)s) "
                "ON CONFLICT (id) DO UPDATE SET name=EXCLUDED.name, kind=EXCLUDED.kind, role=EXCLUDED.role, origin=EXCLUDED.origin, "
                " enabled=EXCLUDED.enabled, url=EXCLUDED.url, country=EXCLUDED.country, language=EXCLUDED.language, "
                " interval_minutes=EXCLUDED.interval_minutes, rate_limit=EXCLUDED.rate_limit, tos_status=EXCLUDED.tos_status, "
                " tos_note=EXCLUDED.tos_note, tos_reviewed_on=EXCLUDED.tos_reviewed_on, tos_evidence_url=EXCLUDED.tos_evidence_url, "
                " options=EXCLUDED.options, config_hash=EXCLUDED.config_hash, updated_at=EXCLUDED.updated_at",
                {"id": s.id, "name": s.name, "kind": s.kind, "role": s.role, "origin": s.origin, "enabled": enabled, "url": s.url,
                 "country": s.country, "language": s.language, "interval": s.interval_minutes, "rate": jsonb(s.rate_limit.model_dump()),
                 "tos": s.tos.status, "note": s.tos.note, "reviewed": s.tos.reviewed_on, "evidence": s.tos.evidence_url,
                 "options": jsonb(s.options), "now": now, "hash": _hash(s.model_dump(mode="json"))})
        run(conn, "UPDATE sources SET enabled=false, updated_at=%s WHERE enabled AND id <> ALL(%s)", (now, ids))
        profile = cfg.profile.model_dump(mode="json")
        run(conn,
            "INSERT INTO candidate_profiles (label, profile, config_hash, created_at, updated_at) VALUES (%s,%s,%s,%s,%s) "
            "ON CONFLICT (label) DO UPDATE SET profile=EXCLUDED.profile, config_hash=EXCLUDED.config_hash, updated_at=EXCLUDED.updated_at",
            (cfg.profile.label, jsonb(profile), _hash(profile), now, now))
