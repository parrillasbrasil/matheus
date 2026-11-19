"""Pipeline de uma fonte: coletar -> detectar mudança -> extrair -> verificar evidência -> elegibilidade -> auditar -> pontuar -> persistir -> alertar.

Regras que este módulo garante:
- nenhuma chamada de rede/IA acontece dentro de transação de banco; cada escrita é uma transação curta;
- falha em UM item nunca derruba a execução, e falha de coleta/IA nunca apaga dado existente;
- conteúdo idêntico (mesmo hash normalizado) não é reprocessado, nem pago de novo;
- a IA só extrai fatos: elegibilidade e pontuação são determinísticas; a auditoria (Claude) só pode tornar o resultado MAIS conservador.
"""
from __future__ import annotations

import dataclasses
import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable, Mapping

import httpx
import psycopg

from .alerts import AlertInput, decide_alert
from .config import Config, SourceCfg
from .connectors import ConnectorOptionsError, RawItem, build_connector
from .db import jsonb, run
from .deadlines import effective_status, resolve_deadline
from .eligibility import Check, evaluate
from .evidence import Verified, verify_extraction
from .http import BlockedByRobots, FetchError, PoliteClient, RequestBudgetExceeded
from .llm.base import LlmError, LlmNotConfigured
from .llm.budget import cache_get, cache_key, cache_put, can_spend, record_usage
from .llm.prompts import AUDIT_PROMPT_VERSION, EXTRACT_PROMPT_VERSION, build_claims
from .logs import redact
from .notify import alert_history, create_alerts
from .official import is_official_url
from .safety import looks_like_prompt_injection
from .schema import Extraction, MoneyValue, llm_json_schema
from .scoring import score_opportunity
from .textutil import content_hash, relevance_hits, select_passages

log = logging.getLogger("radar.pipeline")
_MAX_STORED_CHARS = 500_000


@dataclass
class Deps:
    extractor: Any = None            # OpenAIExtractor (ou equivalente): .model() / .extract(url, title, text)
    auditor: Any = None              # ClaudeAuditor (ou equivalente): .model() / .audit(url, text, claims)
    senders: list = field(default_factory=list)
    transport: httpx.BaseTransport | None = None
    sleep: Callable[[float], None] = time.sleep
    env: Mapping[str, str] = field(default_factory=lambda: os.environ)

    def with_transport(self, transport: httpx.BaseTransport) -> "Deps":
        return dataclasses.replace(self, transport=transport)


@dataclass
class RunResult:
    run_id: str
    status: str
    stats: dict


def _bump(stats: dict, key: str, n: int = 1) -> None:
    stats[key] = stats.get(key, 0) + n


def _schema_hash() -> str:
    return content_hash(json.dumps(llm_json_schema(), sort_keys=True))


# --------------------------------------------------------------------------------------------------
def run_source(conn: psycopg.Connection, cfg: Config, source_id: str, *, trigger: str = "schedule", now: datetime, deps: Deps) -> RunResult:
    src = cfg.source(source_id)
    row = run(conn, "SELECT enabled, tos_status, consecutive_failures, interval_minutes FROM sources WHERE id=%s", (source_id,)).fetchone()
    if row is None:
        raise KeyError(f"fonte {source_id} não está no banco (rode `radar sync`)")
    stats: dict = {"items_seen": 0, "items_new": 0, "items_changed": 0, "items_unchanged": 0, "items_failed": 0}

    def record(status: str, error: str | None = None, requests: int = 0) -> RunResult:
        rid = run(conn,
                  "INSERT INTO source_runs (source_id, trigger, status, started_at, finished_at, items_seen, items_new, items_changed, "
                  " items_unchanged, items_failed, requests_made, error, stats) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
                  (source_id, trigger, status, now, now, stats["items_seen"], stats["items_new"], stats["items_changed"], stats["items_unchanged"],
                   stats["items_failed"], requests, redact(error)[:500] if error else None, jsonb(stats))).fetchone()["id"]
        return RunResult(str(rid), status, stats)

    if not row["enabled"]:
        return record("skipped", "fonte desabilitada")
    if row["tos_status"] != "allowed":
        return record("blocked_by_tos", f"ToS da fonte: {row['tos_status']} (só 'allowed' executa)")

    client = PoliteClient(src.id, src.rate_limit, transport=deps.transport, sleep=deps.sleep)
    status, error, fetch_stats = "success", None, {}
    try:
        connector = build_connector(src, cfg)
        for item in connector.fetch(client, now, fetch_stats):
            _bump(stats, "items_seen")
            try:
                process_item(conn, cfg, src, item, deps, now, stats)
            except Exception as exc:  # isolamento por item: um item ruim não derruba a fonte
                _bump(stats, "items_failed")
                log.error("falha ao processar item", extra={"source": src.id, "url": item.url, "error": redact(f"{exc.__class__.__name__}: {exc}")})
    except BlockedByRobots as exc:
        status, error = "blocked_by_robots", str(exc)
    except (FetchError, RequestBudgetExceeded, ConnectorOptionsError) as exc:
        status, error = ("partial" if stats["items_seen"] else "failed"), f"{exc.__class__.__name__}: {exc}"
    finally:
        client.close()
    if status == "success" and stats["items_failed"]:
        status = "partial"
    stats.update({k: v for k, v in fetch_stats.items()})

    result = record(status, error, client.requests_made)
    _reschedule(conn, src, row, status, now)
    return result


def _reschedule(conn: psycopg.Connection, src: SourceCfg, row: dict, status: str, now: datetime) -> None:
    interval = timedelta(minutes=row["interval_minutes"])
    if status in ("success", "partial"):
        fails, nxt = 0, now + interval
    else:
        fails = row["consecutive_failures"] + 1
        nxt = now + interval * min(2 ** fails, 8)       # backoff para fontes que falham ou bloqueiam
    with conn.transaction():
        run(conn, "UPDATE sources SET last_run_at=%s, next_run_at=%s, consecutive_failures=%s, updated_at=%s WHERE id=%s",
            (now, nxt, fails, now, src.id))


# --------------------------------------------------------------------------------------------------
def _set_doc(conn: psycopg.Connection, doc_id: Any, processing: str, *, status: str | None = None, metadata: dict | None = None) -> None:
    with conn.transaction():
        run(conn, "UPDATE documents SET processing_status=%s, status=coalesce(%s, status), "
                  "raw_metadata = raw_metadata || %s::jsonb WHERE id=%s", (processing, status, jsonb(metadata or {}), doc_id))


def process_item(conn: psycopg.Connection, cfg: Config, src: SourceCfg, item: RawItem, deps: Deps, now: datetime, stats: dict) -> None:
    chash = content_hash(item.text, cfg.change_detection.volatile_patterns)
    existing = run(conn, "SELECT id, content_hash, processing_status FROM documents WHERE source_id=%s AND canonical_url=%s",
                   (src.id, item.url)).fetchone()
    if existing and existing["content_hash"] == chash and existing["processing_status"] in ("processed", "skipped"):
        with conn.transaction():
            run(conn, "UPDATE documents SET last_checked_at=%s, fetched_at=%s WHERE id=%s", (now, now, existing["id"]))
        _bump(stats, "items_unchanged")
        return

    _bump(stats, "items_new" if existing is None else ("items_changed" if existing["content_hash"] != chash else "items_retried"))
    with conn.transaction():
        doc_id = run(
            conn,
            "INSERT INTO documents (source_id, canonical_url, external_id, content_type, title, text, text_chars, content_hash, status, "
            " processing_status, origin, raw_metadata, first_seen_at, fetched_at, last_checked_at) "
            "VALUES (%(s)s,%(u)s,%(e)s,%(ct)s,%(t)s,%(x)s,%(n)s,%(h)s,%(st)s,'pending',%(o)s,%(m)s,%(now)s,%(now)s,%(now)s) "
            "ON CONFLICT (source_id, canonical_url) DO UPDATE SET external_id=EXCLUDED.external_id, content_type=EXCLUDED.content_type, "
            " title=EXCLUDED.title, text=EXCLUDED.text, text_chars=EXCLUDED.text_chars, content_hash=EXCLUDED.content_hash, "
            " status=EXCLUDED.status, processing_status='pending', origin=EXCLUDED.origin, raw_metadata=EXCLUDED.raw_metadata, "
            " fetched_at=EXCLUDED.fetched_at, last_checked_at=EXCLUDED.last_checked_at RETURNING id",
            {"s": src.id, "u": item.url, "e": item.external_id, "ct": item.content_type, "t": item.title, "x": item.text[:_MAX_STORED_CHARS],
             "n": len(item.text), "h": chash, "st": "needs_ocr" if item.needs_ocr else "ok", "o": src.origin,
             "m": jsonb(item.metadata), "now": now}).fetchone()["id"]

    if item.needs_ocr:
        _bump(stats, "needs_ocr")
        return _set_doc(conn, doc_id, "skipped")
    if src.role == "program_rules":
        return _set_doc(conn, doc_id, "skipped", metadata={"role": "program_rules"})
    if item.structured is None:
        hits = looks_like_prompt_injection(item.text)
        if hits:
            _bump(stats, "suspicious_documents")
            return _set_doc(conn, doc_id, "skipped", metadata={"prompt_injection_suspected": True, "patterns": hits})
        keywords = [k for ks in cfg.relevance.keywords.values() for k in ks]
        if relevance_hits(item.text, keywords) < cfg.relevance.min_hits:
            _bump(stats, "irrelevant")
            return _set_doc(conn, doc_id, "skipped", status="irrelevant")

    if item.structured is not None:
        ex, origin, truncated = item.structured, "deterministic_feed", False
    else:
        ex, origin, truncated = _extract_with_llm(conn, cfg, item, deps, doc_id, now, stats)
        if ex is None:
            return
    if truncated:
        with conn.transaction():
            run(conn, "UPDATE documents SET truncated=true WHERE id=%s", (doc_id,))

    _persist(conn, cfg, src, item, ex, origin, doc_id, chash, deps, now, stats)


# --------------------------------------------------------------------------------------------------
def _extract_with_llm(conn, cfg: Config, item: RawItem, deps: Deps, doc_id: Any, now: datetime, stats: dict
                      ) -> tuple[Extraction | None, str, bool]:
    if deps.extractor is None:
        _bump(stats, "pending_llm")
        _set_doc(conn, doc_id, "pending_llm")
        return None, "llm", False
    try:
        model = deps.extractor.model()
    except LlmNotConfigured:
        _bump(stats, "pending_llm")
        _set_doc(conn, doc_id, "pending_llm")
        return None, "llm", False

    keywords = [k for ks in cfg.relevance.keywords.values() for k in ks]
    passages = select_passages(item.text, keywords, cfg.llm.budget.max_input_chars_per_document)
    key = cache_key("openai", model, EXTRACT_PROMPT_VERSION, content_hash(passages.text), _schema_hash())
    cached = cache_get(conn, key)
    if cached is not None:
        record_usage(conn, None, cfg, "extraction", now=now, cached=True, provider="openai", model=model, document_id=doc_id)
        _bump(stats, "llm_cache_hits")
        return Extraction.model_validate(cached), "llm", passages.truncated

    estimate = len(passages.text) // 3 + cfg.llm.openai.max_output_tokens
    allowed, why = can_spend(conn, cfg, now, estimate)
    if not allowed:
        record_usage(conn, None, cfg, "extraction", now=now, status="budget_blocked", error=why, provider="openai", model=model, document_id=doc_id)
        _bump(stats, "pending_llm")
        _set_doc(conn, doc_id, "pending_llm")
        return None, "llm", False
    try:
        res = deps.extractor.extract(item.url, item.title, passages.text)
    except LlmNotConfigured:
        _bump(stats, "pending_llm")
        _set_doc(conn, doc_id, "pending_llm")
        return None, "llm", False
    except LlmError as exc:
        record_usage(conn, None, cfg, "extraction", now=now, status="error", error=str(exc), provider="openai", model=model, document_id=doc_id)
        _set_doc(conn, doc_id, "failed")
        _bump(stats, "items_failed")
        return None, "llm", False
    record_usage(conn, res.call, cfg, "extraction", now=now, document_id=doc_id)
    cache_put(conn, key, "openai", model, res.extraction.model_dump(mode="json"), res.call.input_tokens, res.call.output_tokens)
    return res.extraction, "llm", passages.truncated


@dataclass
class _Assessment:
    resolved: Any
    status: str
    elig: Any
    score: Any


def _assess(cfg: Config, ex: Extraction, v: Verified, item: RawItem, src: SourceCfg, official: bool, now: datetime,
            *, status_verified: bool, deadline_verified: bool) -> _Assessment:
    resolved = resolve_deadline(cfg, ex.deadline, ex.country or src.country, deadline_verified, now)
    status = effective_status(ex.status, resolved, status_verified, now)
    elig = evaluate(cfg, ex, source_url=item.url, is_official=official, now=now, text=item.text)
    score = score_opportunity(cfg, ex, elig, resolved, v.ratio, now)
    return _Assessment(resolved, status, elig, score)


def _run_audit(conn, cfg: Config, item: RawItem, ex: Extraction, deps: Deps, doc_id: Any, now: datetime) -> tuple[str, set[str]]:
    """Devolve (audit_status, ids de claims contestados). Falha de auditoria nunca bloqueia o pipeline."""
    if deps.auditor is None:
        return "unavailable", set()
    claims = build_claims(ex)
    if not claims:
        return "not_requested", set()
    try:
        model = deps.auditor.model()
    except LlmNotConfigured:
        return "unavailable", set()
    keywords = [k for ks in cfg.relevance.keywords.values() for k in ks]
    text = select_passages(item.text, keywords, cfg.llm.budget.max_input_chars_per_document).text
    allowed, why = can_spend(conn, cfg, now, len(text) // 3 + cfg.llm.anthropic.max_output_tokens)
    if not allowed:
        record_usage(conn, None, cfg, "audit", now=now, status="budget_blocked", error=why, provider="anthropic", model=model, document_id=doc_id)
        return "unavailable", set()
    try:
        res = deps.auditor.audit(item.url, text, claims)
    except LlmError as exc:
        record_usage(conn, None, cfg, "audit", now=now, status="error", error=str(exc), provider="anthropic", model=model, document_id=doc_id)
        return "unavailable", set()
    record_usage(conn, res.call, cfg, "audit", now=now, document_id=doc_id)
    bad = {x.claim_id for x in res.verdicts if x.verdict != "supported"}
    if bad or res.overall == "disputed":
        return "disputed", bad
    return ("confirmed" if res.overall == "confirmed" else "unavailable"), set()


def _norm(v: Any) -> str:
    from decimal import Decimal

    def conv(o: Any) -> Any:
        if isinstance(o, Decimal):
            return float(o)
        if isinstance(o, datetime):
            return o.isoformat()
        return str(o)

    return json.dumps(v, sort_keys=True, default=conv)


_TRACKED = ["title_original", "value_amount", "value_currency", "value_period", "deadline_at", "status", "eligibility_status",
            "relocation_condition", "benefit_types", "requirements", "obligations", "not_covered_expenses"]


def _persist(conn, cfg: Config, src: SourceCfg, item: RawItem, ex: Extraction, origin: str, doc_id: Any, chash: str,
             deps: Deps, now: datetime, stats: dict) -> None:
    key = f"{src.id}|{item.external_id or item.url}"
    prev = run(conn, "SELECT id, content_hash FROM opportunities WHERE opportunity_key=%s", (key,)).fetchone()

    if not ex.is_opportunity:
        if prev is not None:
            _no_longer_opportunity(conn, prev["id"], chash, now)
        return _set_doc(conn, doc_id, "processed", metadata={"is_opportunity": False})

    official = is_official_url(cfg, item.url)
    v = verify_extraction(ex, item.text)
    ex, status_ok, deadline_ok = v.extraction, v.status_verified, v.deadline_quote_verified
    change = "new" if prev is None else ("changed" if prev["content_hash"] != chash else "unchanged")
    a = _assess(cfg, ex, v, item, src, official, now, status_verified=status_ok, deadline_verified=deadline_ok)

    # auditoria independente: score alto, mudança ou conflito de evidência (só para extrações feitas por IA)
    audit_status = "not_requested"
    ac = cfg.llm.audit
    wanted = origin == "llm" and (a.score.total >= ac.score_threshold or (ac.on_change and change == "changed") or (ac.on_conflict and bool(v.failures)))
    if wanted:
        audit_status, bad = _run_audit(conn, cfg, item, ex, deps, doc_id, now)
        if bad:
            upd: dict = {}
            if "value" in bad:
                upd["value"] = MoneyValue(amount=None, currency=None, gross_net="unknown", period="unknown", quote=None)
            if "deadline" in bad:
                upd["deadline"] = ex.deadline.model_copy(update={"iso": None, "timezone": None, "quote": None})
                deadline_ok = False
            if "status" in bad:
                status_ok = False
            upd["requirements"] = [r.model_copy(update={"verified": False}) if r.id in bad else r for r in ex.requirements]
            ex = ex.model_copy(update=upd)
            a = _assess(cfg, ex, v, item, src, official, now, status_verified=status_ok, deadline_verified=deadline_ok)
        if audit_status == "disputed" and a.elig.status == "eligible":
            a.elig.status = "needs_confirmation"
            a.elig.checks.append(Check("independent-audit", "independent_audit", "unknown",
                                       "A auditoria independente contestou parte da evidência extraída; confirme na fonte oficial.",
                                       source_url=item.url, origin="deterministic"))
            a.elig.summary_pt = "Precisa de confirmação: a auditoria independente contestou parte da evidência. " + a.elig.summary_pt
            a.elig.next_steps.append("Conferir na fonte oficial os pontos contestados pela auditoria antes de qualquer ação.")

    verified_evidence = sum(1 for s in v.spans if s.verified)
    val = ex.value
    cols = {
        "opportunity_key": key, "source_id": src.id, "document_id": doc_id, "canonical_url": item.url, "is_official": official,
        "data_origin": src.origin, "title_original": ex.title_original or item.title, "title_pt": ex.title_pt, "issuer": ex.issuer,
        "country": ex.country or src.country, "city_or_region": ex.city_or_region, "benefit_types": list(ex.benefit_types),
        "category": ex.category, "aid_type": ex.aid_type, "benefit_summary_pt": ex.benefit_summary_pt,
        "value_amount": val.amount, "value_currency": val.currency, "value_gross_net": val.gross_net, "value_period": val.period,
        "deadline_at": a.resolved.utc, "deadline_tz": a.resolved.tz, "deadline_tz_assumed": a.resolved.tz_assumed,
        "deadline_confirmed": a.resolved.confirmed, "status": a.status, "eligibility_status": a.elig.status,
        "relocation_condition": a.elig.relocation_condition, "eligibility_summary": a.elig.summary_pt,
        "requirements": jsonb([r.model_dump(mode="json") for r in ex.requirements]),
        "obligations": jsonb([o.model_dump(mode="json") for o in ex.obligations]),
        "not_covered_expenses": jsonb([o.model_dump(mode="json") for o in ex.not_covered_expenses]),
        "extraction": jsonb(ex.model_dump(mode="json")), "extraction_origin": origin, "audit_status": audit_status,
        "last_verified_at": now, "content_hash": chash, "current_score": a.score.total, "updated_at": now,
    }
    names = list(cols)
    update_set = ", ".join(f"{n}=EXCLUDED.{n}" for n in names if n != "opportunity_key")

    with conn.transaction():
        old = run(conn, "SELECT * FROM opportunities WHERE opportunity_key=%s FOR UPDATE", (key,)).fetchone()
        opp_id = run(conn,
                     f"INSERT INTO opportunities ({', '.join(names)}, first_seen_at) VALUES ({', '.join('%(' + n + ')s' for n in names)}, %(updated_at)s) "
                     f"ON CONFLICT (opportunity_key) DO UPDATE SET {update_set} RETURNING id", cols).fetchone()["id"]

        fields_changed: dict[str, dict] = {}
        if old is not None:
            for name in _TRACKED:
                before = old[name]
                after = cols[name].obj if hasattr(cols[name], "obj") else cols[name]
                if _norm(before) != _norm(after):
                    fields_changed[name] = {"from": json.loads(_norm(before)), "to": json.loads(_norm(after))}
        meaningful = old is None or bool(fields_changed)

        version_id = None
        if old is None or old["content_hash"] != chash:
            version_no = (run(conn, "SELECT coalesce(max(version_no),0)+1 AS n FROM opportunity_versions WHERE opportunity_id=%s", (opp_id,)).fetchone()["n"])
            snapshot = {"title_original": cols["title_original"], "status": a.status, "eligibility": a.elig.to_dict(),
                        "score": {"total": a.score.total, "components": a.score.components, "explanation": a.score.explanation},
                        "extraction": ex.model_dump(mode="json"), "audit_status": audit_status}
            summary = {"created": True} if old is None else {"fields": fields_changed}
            version_id = run(conn,
                             "INSERT INTO opportunity_versions (opportunity_id, version_no, content_hash, snapshot, text_excerpt, change_summary, created_at) "
                             "VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id",
                             (opp_id, version_no, chash, jsonb(snapshot), item.text[:2000], jsonb(summary), now)).fetchone()["id"]
            run(conn, "UPDATE opportunities SET current_version_id=%s WHERE id=%s", (version_id, opp_id))
            for s in v.spans:
                run(conn, "INSERT INTO evidence_spans (opportunity_id, version_id, document_id, field, quote, start_char, end_char, verified, source_url, created_at) "
                          "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)", (opp_id, version_id, doc_id, s.field, s.quote, s.start, s.end, s.verified, item.url, now))
            for c in a.elig.checks:
                run(conn, "INSERT INTO eligibility_checks (opportunity_id, version_id, requirement_id, kind, result, reason_pt, quote, source_url, origin, "
                          "checked_by, fixable_by_relocation, created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (opp_id, version_id, c.requirement_id, c.kind, c.result, c.reason_pt, c.quote, c.source_url, c.origin,
                     "claude_audit" if c.requirement_id == "independent-audit" else "deterministic", c.fixable_by_relocation, now))
            run(conn, "INSERT INTO scores (opportunity_id, version_id, total, components, explanation, created_at) VALUES (%s,%s,%s,%s,%s,%s)",
                (opp_id, version_id, a.score.total, jsonb(a.score.components), jsonb(a.score.explanation), now))
        else:
            version_id = run(conn, "SELECT current_version_id FROM opportunities WHERE id=%s", (opp_id,)).fetchone()["current_version_id"]

        deadline_key = a.resolved.utc.date().isoformat() if a.resolved.utc else "none"
        ever, urgent_sent = alert_history(conn, opp_id, deadline_key)
        decision = decide_alert(
            cfg,
            AlertInput(status=a.status, eligibility_status=a.elig.status, score=a.score.total, is_official=official,
                       verified_evidence=verified_evidence, deadline=a.resolved),
            change=change, meaningful_change=meaningful, ever_alerted=ever, urgent_sent=urgent_sent, now=now)
        if decision.send and decision.kind:
            _bump(stats, "alerts_created", create_alerts(conn, cfg, opp_id, version_id, decision.kind, reason=decision.reason,
                                                         deadline_key=deadline_key, now=now))
        else:
            log.info("alerta suprimido", extra={"url": item.url, "reason": decision.reason})
        run(conn, "UPDATE documents SET processing_status='processed' WHERE id=%s", (doc_id,))


def _no_longer_opportunity(conn, opp_id: Any, chash: str, now: datetime) -> None:
    """O documento deixou de descrever uma oferta: registra versão e marca status desconhecido (nunca apaga)."""
    with conn.transaction():
        run(conn, "UPDATE opportunities SET status='unknown', content_hash=%s, last_verified_at=%s, updated_at=%s WHERE id=%s", (chash, now, now, opp_id))
        n = run(conn, "SELECT coalesce(max(version_no),0)+1 AS n FROM opportunity_versions WHERE opportunity_id=%s", (opp_id,)).fetchone()["n"]
        vid = run(conn, "INSERT INTO opportunity_versions (opportunity_id, version_no, content_hash, snapshot, change_summary, created_at) "
                        "VALUES (%s,%s,%s,%s,%s,%s) RETURNING id",
                  (opp_id, n, chash, jsonb({"status": "unknown"}), jsonb({"no_longer_opportunity": True}), now)).fetchone()["id"]
        run(conn, "UPDATE opportunities SET current_version_id=%s WHERE id=%s", (vid, opp_id))
