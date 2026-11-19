"""Critérios de aceite da especificação, de ponta a ponta, contra PostgreSQL real.
Extratores/auditores são FALSOS e as páginas são fixtures sintéticas: nada aqui é pesquisa ao vivo."""
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from radar.config import RateLimit, SourceCfg, Tos
from radar.llm.budget import usage_today
from radar.pipeline import run_source
from tests.integration.pipeline_kit import (
    ENV, ESC, EXPIRED, INJECTION, NOW, RENT, FakeAuditor, FakeExtractor, count, deps, edit, esc_placement,
    expired_bando, rent_bonus, setup, use_tmp_repo,
)

pytestmark = pytest.mark.integration


def opp(conn):
    return conn.execute("select * from opportunities").fetchone()


def scan(conn, cfg, sid, ext, aud=None, now=NOW):
    return run_source(conn, cfg, sid, trigger="manual", now=now, deps=deps(ext, aud))


@pytest.fixture(autouse=True)
def one_channel(cfg):
    cfg.alerts.channels.smtp.enabled = False  # 1 linha de alerta por decisão (telegram)


# ---- trilha completa ------------------------------------------------------------------------
def test_first_scan_persists_the_whole_audit_trail(aconn, cfg):
    setup(aconn, cfg, RENT)
    res = scan(aconn, cfg, RENT, FakeExtractor(rent_bonus))
    assert res.status == "success" and res.stats["items_new"] == 1
    o = opp(aconn)
    assert o["data_origin"] == "fixture" and o["is_official"] and o["extraction_origin"] == "llm"
    assert o["eligibility_status"] == "eligible" and o["relocation_condition"] == "after_award" and o["status"] == "open"
    assert float(o["value_amount"]) == 3000 and o["value_currency"] == "EUR"
    assert o["deadline_at"] == datetime(2027, 3, 31, 21, 59, 59, tzinfo=timezone.utc)
    assert o["deadline_tz"] == "Europe/Rome" and o["deadline_tz_assumed"] and not o["deadline_confirmed"]
    assert o["last_verified_at"] == NOW and o["current_version_id"] and o["current_score"] is not None
    assert count(aconn, "opportunity_versions") == 1 and count(aconn, "scores") == 1 and count(aconn, "eligibility_checks") >= 3
    fields = {r["field"] for r in aconn.execute("select field from evidence_spans where verified").fetchall()}
    assert {"value", "deadline", "status", "requirement:r-nat", "requirement:r-age"} <= fields
    assert aconn.execute("select origin from documents").fetchone()["origin"] == "fixture"
    assert aconn.execute("select status, items_seen, items_new, requests_made from source_runs").fetchone()["items_new"] == 1


def test_identical_source_does_not_duplicate_anything_nor_pay_the_llm_twice(aconn, cfg):
    setup(aconn, cfg, RENT)
    ext = FakeExtractor(rent_bonus)
    scan(aconn, cfg, RENT, ext)
    second = scan(aconn, cfg, RENT, ext, now=NOW + timedelta(hours=1))
    assert second.stats["items_unchanged"] == 1 and len(ext.calls) == 1
    assert (count(aconn, "documents"), count(aconn, "opportunities"), count(aconn, "opportunity_versions"), count(aconn, "alerts")) == (1, 1, 1, 1)


def test_volatile_page_noise_is_not_a_change(aconn, cfg, tmp_path, monkeypatch):
    html = use_tmp_repo(tmp_path, monkeypatch) / "rent_bonus_it.html"
    edit(html, "</main>", "<p>Ultima modifica: 10/11/2026</p></main>")
    setup(aconn, cfg, RENT)
    ext = FakeExtractor(rent_bonus)
    scan(aconn, cfg, RENT, ext)
    edit(html, "Ultima modifica: 10/11/2026", "Ultima modifica: 12/11/2026")
    assert scan(aconn, cfg, RENT, ext, now=NOW + timedelta(hours=1)).stats["items_unchanged"] == 1
    assert len(ext.calls) == 1 and count(aconn, "opportunity_versions") == 1


def test_changed_source_adds_a_version_and_realerts(aconn, cfg, tmp_path, monkeypatch):
    html = use_tmp_repo(tmp_path, monkeypatch) / "rent_bonus_it.html"
    setup(aconn, cfg, RENT)
    ext = FakeExtractor(rent_bonus)
    scan(aconn, cfg, RENT, ext)
    edit(html, "3.000,00", "4.000,00")
    res = scan(aconn, cfg, RENT, ext, now=NOW + timedelta(hours=1))
    assert res.stats["items_changed"] == 1 and count(aconn, "opportunities") == 1 and count(aconn, "documents") == 1
    o = opp(aconn)
    assert float(o["value_amount"]) == 4000
    versions = aconn.execute("select version_no, change_summary from opportunity_versions order by version_no").fetchall()
    assert [v["version_no"] for v in versions] == [1, 2] and "value_amount" in versions[1]["change_summary"]["fields"]
    assert sorted(r["kind"] for r in aconn.execute("select kind from alerts").fetchall()) == ["changed", "new"]


# ---- elegibilidade / alertas ----------------------------------------------------------------------
def test_expired_opportunity_is_stored_closed_and_never_fires_an_open_application_alert(aconn, cfg):
    setup(aconn, cfg, EXPIRED)
    scan(aconn, cfg, EXPIRED, FakeExtractor(expired_bando))
    assert opp(aconn)["status"] == "closed" and count(aconn, "alerts") == 0  # o texto diz "aperto", mas o prazo já passou


def test_italian_citizenship_with_brazilian_residence_is_not_eligible_for_esc(aconn, cfg):
    setup(aconn, cfg, ESC)
    scan(aconn, cfg, ESC, FakeExtractor(esc_placement))
    o = opp(aconn)
    assert o["eligibility_status"] == "ineligible" and o["relocation_condition"] == "before_apply"
    assert count(aconn, "alerts") == 0
    assert aconn.execute("select count(*) n from eligibility_checks where origin='program_rule'").fetchone()["n"] >= 1


def test_eligible_open_opportunity_creates_an_alert_labelled_as_test_data(aconn, cfg):
    setup(aconn, cfg, RENT)
    scan(aconn, cfg, RENT, FakeExtractor(rent_bonus))
    a = aconn.execute("select * from alerts").fetchone()
    assert a["kind"] == "new" and a["channel"] == "telegram" and a["status"] == "pending"


# ---- falhas e economia ---------------------------------------------------------------------------------
def test_without_llm_keys_the_document_waits_and_is_processed_once_keys_exist(aconn, cfg):
    setup(aconn, cfg, RENT)
    res = scan(aconn, cfg, RENT, FakeExtractor(rent_bonus, configured=False))
    assert res.status == "success" and res.stats["pending_llm"] == 1
    assert aconn.execute("select processing_status from documents").fetchone()["processing_status"] == "pending_llm"
    assert count(aconn, "opportunities") == 0 and count(aconn, "alerts") == 0
    scan(aconn, cfg, RENT, FakeExtractor(rent_bonus), now=NOW + timedelta(hours=1))
    assert opp(aconn)["eligibility_status"] == "eligible"
    assert aconn.execute("select processing_status from documents").fetchone()["processing_status"] == "processed"


def test_llm_failure_keeps_existing_data_and_does_not_stop_the_run(aconn, cfg, tmp_path, monkeypatch):
    html = use_tmp_repo(tmp_path, monkeypatch) / "rent_bonus_it.html"
    setup(aconn, cfg, RENT)
    scan(aconn, cfg, RENT, FakeExtractor(rent_bonus))
    edit(html, "3.000,00", "4.000,00")
    res = scan(aconn, cfg, RENT, FakeExtractor(rent_bonus, fail=True), now=NOW + timedelta(hours=1))
    assert res.status == "partial" and res.stats["items_failed"] == 1
    assert float(opp(aconn)["value_amount"]) == 3000 and count(aconn, "opportunity_versions") == 1  # nada foi apagado
    assert aconn.execute("select processing_status from documents").fetchone()["processing_status"] == "failed"
    assert aconn.execute("select status from agent_usage where status='error'").fetchone() is not None
    scan(aconn, cfg, RENT, FakeExtractor(rent_bonus), now=NOW + timedelta(hours=2))  # reprocessa na próxima execução
    assert float(opp(aconn)["value_amount"]) == 4000


def test_budget_cap_defers_the_document_instead_of_spending(aconn, cfg):
    cfg.llm.budget.daily_token_cap = 10
    setup(aconn, cfg, RENT)
    ext = FakeExtractor(rent_bonus)
    assert scan(aconn, cfg, RENT, ext).stats["pending_llm"] == 1 and ext.calls == []
    assert aconn.execute("select status from agent_usage").fetchone()["status"] == "budget_blocked"


def test_identical_content_on_another_url_is_served_from_the_cache(aconn, cfg):
    twin = cfg.source(RENT).model_copy(update={"id": "test-fixture-rent-bonus-gemea", "url": RENT_TWIN})
    cfg.sources.append(twin)
    setup(aconn, cfg, RENT, twin.id)
    ext = FakeExtractor(rent_bonus)
    scan(aconn, cfg, RENT, ext)
    scan(aconn, cfg, twin.id, ext)
    assert len(ext.calls) == 1 and count(aconn, "opportunities") == 2
    assert count(aconn, "agent_usage", "cached") == 1 and usage_today(aconn, NOW).tokens == 150


RENT_TWIN = "https://www.comune.esempio-di-prova.it/__fixture__/bonus-affitto-gemea"


def test_irrelevant_page_never_reaches_the_llm(aconn, cfg, tmp_path, monkeypatch):
    html = use_tmp_repo(tmp_path, monkeypatch) / "rent_bonus_it.html"
    html.write_text("<html><title>Orari</title><body><main><p>Orari di apertura dello sportello anagrafe: lunedì e giovedì.</p></main></body></html>", encoding="utf-8")
    setup(aconn, cfg, RENT)
    ext = FakeExtractor(rent_bonus)
    scan(aconn, cfg, RENT, ext)
    d = aconn.execute("select status, processing_status from documents").fetchone()
    assert ext.calls == [] and (d["status"], d["processing_status"]) == ("irrelevant", "skipped") and count(aconn, "opportunities") == 0


def test_prompt_injection_page_is_not_sent_to_the_llm_and_creates_nothing(aconn, cfg):
    setup(aconn, cfg, INJECTION)
    ext = FakeExtractor(rent_bonus)
    res = scan(aconn, cfg, INJECTION, ext)
    d = aconn.execute("select processing_status, raw_metadata from documents").fetchone()
    assert ext.calls == [] and res.stats["suspicious_documents"] == 1 and d["processing_status"] == "skipped"
    assert d["raw_metadata"]["prompt_injection_suspected"] and count(aconn, "opportunities") == 0 and count(aconn, "alerts") == 0


# ---- auditoria independente (Claude) -----------------------------------------------------------------
def test_audit_contradicting_the_value_discards_it_and_downgrades_eligibility(aconn, cfg):
    cfg.llm.audit.score_threshold = 0
    setup(aconn, cfg, RENT)
    aud = FakeAuditor({"value": "contradicted"}, overall="disputed")
    scan(aconn, cfg, RENT, FakeExtractor(rent_bonus), aud)
    o = opp(aconn)
    assert len(aud.calls) == 1 and o["audit_status"] == "disputed"
    assert o["value_amount"] is None and o["eligibility_status"] == "needs_confirmation"
    assert aconn.execute("select count(*) n from agent_usage where provider='anthropic'").fetchone()["n"] == 1


def test_audit_that_confirms_keeps_everything(aconn, cfg):
    cfg.llm.audit.score_threshold = 0
    setup(aconn, cfg, RENT)
    scan(aconn, cfg, RENT, FakeExtractor(rent_bonus), FakeAuditor())
    o = opp(aconn)
    assert o["audit_status"] == "confirmed" and float(o["value_amount"]) == 3000 and o["eligibility_status"] == "eligible"


def test_audit_is_skipped_when_nothing_triggers_it_and_marked_unavailable_without_a_key(aconn, cfg):
    cfg.llm.audit.score_threshold = 101
    setup(aconn, cfg, RENT)
    aud = FakeAuditor()
    scan(aconn, cfg, RENT, FakeExtractor(rent_bonus), aud)
    assert aud.calls == [] and opp(aconn)["audit_status"] == "not_requested"
    aconn.execute("delete from alerts"); aconn.execute("delete from scores"); aconn.execute("delete from eligibility_checks")
    cfg.llm.audit.score_threshold = 0
    aconn.execute("update documents set processing_status='pending'")  # força reprocessar
    scan(aconn, cfg, RENT, FakeExtractor(rent_bonus), FakeAuditor(configured=False), now=NOW + timedelta(hours=1))
    assert opp(aconn)["audit_status"] == "unavailable"


# ---- controles de coleta -------------------------------------------------------------------------------
def test_source_without_approved_tos_makes_no_request_at_all(aconn, cfg):
    cfg.source(RENT).tos.status = "pending_review"
    setup(aconn, cfg, RENT)
    ext = FakeExtractor(rent_bonus)
    res = scan(aconn, cfg, RENT, ext)
    assert res.status == "blocked_by_tos" and ext.calls == [] and count(aconn, "documents") == 0


def _html_source(cfg):
    src = SourceCfg(id="t-html", name="Fonte HTML de teste", kind="html_page", origin="real", enabled=True,
                    url="https://www.comune.esempio-di-prova.it/bando", country="IT", language="it", interval_minutes=60,
                    rate_limit=RateLimit(requests_per_minute=600, max_requests_per_run=5), tos=Tos(status="allowed"), options={})
    cfg.sources.append(src)
    return src


def test_robots_block_is_recorded_and_penalises_the_schedule(aconn, cfg):
    _html_source(cfg)
    setup(aconn, cfg, "t-html")
    t = httpx.MockTransport(lambda r: httpx.Response(200, text="User-agent: *\nDisallow: /") if r.url.path == "/robots.txt" else httpx.Response(200, text="<html></html>"))
    res = run_source(aconn, cfg, "t-html", trigger="manual", now=NOW, deps=deps(FakeExtractor(rent_bonus)).with_transport(t))
    s = aconn.execute("select * from sources where id='t-html'").fetchone()
    assert res.status == "blocked_by_robots" and s["consecutive_failures"] == 1 and s["next_run_at"] > NOW + timedelta(minutes=60)
    assert count(aconn, "documents") == 0


def test_network_failure_is_a_failed_run_and_keeps_previous_data(aconn, cfg):
    _html_source(cfg)
    setup(aconn, cfg, "t-html")
    t = httpx.MockTransport(lambda r: httpx.Response(404) if r.url.path == "/robots.txt" else httpx.Response(503))
    res = run_source(aconn, cfg, "t-html", trigger="schedule", now=NOW, deps=deps(FakeExtractor(rent_bonus)).with_transport(t))
    assert res.status == "failed" and aconn.execute("select consecutive_failures from sources where id='t-html'").fetchone()["consecutive_failures"] == 1


def test_disabled_source_is_skipped(aconn, cfg):
    setup(aconn, cfg)  # fixtures desabilitadas
    assert scan(aconn, cfg, RENT, FakeExtractor(rent_bonus)).status == "skipped"
