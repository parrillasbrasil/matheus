import json
from datetime import datetime, timezone

import httpx
import pytest

from radar.config import RateLimit, SourceCfg, Tos
from radar.connectors import build_connector
from radar.connectors.base import ConnectorOptionsError
from radar.http import PoliteClient
from radar.textutil import verify_quote
from tests.helpers import fixture_text, make_blank_pdf, make_text_pdf, routes_transport, zip_json

NOW = datetime(2026, 11, 19, 18, 0, tzinfo=timezone.utc)
HTML = {"content-type": "text/html; charset=utf-8"}
PDF = {"content-type": "application/pdf"}


def make_source(kind, url, options=None, country="IT", origin=None):
    return SourceCfg(
        id="test-src", name="t", kind=kind, origin=origin or ("fixture" if kind == "fixture" else "real"), enabled=True,
        url=url, country=country, language=None, interval_minutes=60,
        rate_limit=RateLimit(requests_per_minute=600, max_requests_per_run=20), tos=Tos(status="allowed"), options=options or {},
    )


def make_client(transport):
    return PoliteClient("t", RateLimit(requests_per_minute=600, max_requests_per_run=20), transport=transport, sleep=lambda s: None)


def run(cfg, source, transport):
    stats: dict = {}
    items = list(build_connector(source, cfg).fetch(make_client(transport), NOW, stats))
    return items, stats


# --- HTML / PDF ------------------------------------------------------------------------------
def test_html_page_extracts_title_main_text_and_canonical_url(cfg):
    url = "https://www.comune.esempio-di-prova.it/bando"
    t = routes_transport({url: httpx.Response(200, text=fixture_text("html/rent_bonus_it.html"), headers=HTML)})
    items, _ = run(cfg, make_source("html_page", url), t)
    assert len(items) == 1
    it = items[0]
    assert it.url == "https://www.comune.esempio-di-prova.it/__fixture__/bonus-affitto"
    assert it.title.startswith("[TESTE] Bonus affitto")
    assert "3.000,00 euro" in it.text and "31 marzo 2027" in it.text
    assert "Home | Servizi" not in it.text and "visualizzazioni" not in it.text  # nav/footer removidos
    assert it.content_type == "html" and it.needs_ocr is False


def test_canonical_pointing_to_another_host_is_ignored(cfg):
    html = '<html><head><title>x</title><link rel="canonical" href="https://evil.example.com/p"></head><body><main><p>Contributo 100 euro</p></main></body></html>'
    url = "https://www.comune.esempio.it/p?utm_source=x&id=7"
    items, _ = run(cfg, make_source("html_page", url), routes_transport({url.split("?")[0]: httpx.Response(200, text=html, headers=HTML)}))
    assert items[0].url == "https://www.comune.esempio.it/p?id=7"  # sem utm_*, sem canonical externo


def test_pdf_text_is_extracted(cfg):
    url = "https://www.regione.example.it/doc.pdf"
    items, _ = run(cfg, make_source("pdf_document", url), routes_transport({url: httpx.Response(200, content=make_text_pdf("Contributo 15.000 euro"), headers=PDF)}))
    assert "Contributo 15.000 euro" in items[0].text
    assert items[0].content_type == "pdf" and items[0].needs_ocr is False


def test_scanned_pdf_without_text_is_flagged_for_ocr_not_invented(cfg):
    url = "https://www.regione.example.it/scan.pdf"
    items, _ = run(cfg, make_source("pdf_document", url), routes_transport({url: httpx.Response(200, content=make_blank_pdf(), headers=PDF)}))
    assert items[0].text == "" and items[0].needs_ocr is True


# --- hub ----------------------------------------------------------------------------------------
HUB = (
    '<html><body><main><a href="/bando-affitto">Bando affitto 2027</a> <a href="/contatti">Contatti</a>'
    '<a href="https://blog.esempio.com/bonus">Bonus</a> <a href="/documenti/contributo.pdf">Scarica</a></main></body></html>'
)


def hub_routes(extra=None):
    base = "https://www.comune.esempio.it"
    r = {
        f"{base}/hub": httpx.Response(200, text=HUB, headers=HTML),
        f"{base}/bando-affitto": httpx.Response(200, text="<main><p>Bando affitto contributo 2.000 euro</p></main>", headers=HTML),
        f"{base}/documenti/contributo.pdf": httpx.Response(200, content=make_text_pdf("Contributo 15.000 euro"), headers=PDF),
    }
    r.update(extra or {})
    return r


def test_hub_follows_only_matching_official_links(cfg):
    seen: list[str] = []
    src = make_source("html_hub", "https://www.comune.esempio.it/hub", {"follow_patterns": ["(?i)bando", "(?i)contribut"], "max_links": 5})
    items, stats = run(cfg, src, routes_transport(hub_routes(), seen))
    assert {i.url for i in items} == {
        "https://www.comune.esempio.it/hub",
        "https://www.comune.esempio.it/bando-affitto",
        "https://www.comune.esempio.it/documenti/contributo.pdf",
    }
    assert not any("contatti" in u or "blog.esempio.com" in u for u in seen)


def test_hub_respects_max_links(cfg):
    src = make_source("html_hub", "https://www.comune.esempio.it/hub", {"follow_patterns": ["(?i)bando", "(?i)contribut"], "max_links": 1})
    items, _ = run(cfg, src, routes_transport(hub_routes()))
    assert len(items) == 2  # hub + 1 filho


def test_hub_skips_children_blocked_by_robots_and_keeps_going(cfg):
    base = "https://www.comune.esempio.it"
    robots = httpx.Response(200, text="User-agent: *\nDisallow: /bando-affitto")
    src = make_source("html_hub", f"{base}/hub", {"follow_patterns": ["(?i)bando", "(?i)contribut"], "max_links": 5})
    items, stats = run(cfg, src, routes_transport(hub_routes({f"{base}/robots.txt": robots})))
    assert f"{base}/bando-affitto" not in {i.url for i in items}
    assert f"{base}/documenti/contributo.pdf" in {i.url for i in items}
    assert stats["blocked_by_robots"] == 1


# --- feed dos EUA --------------------------------------------------------------------------------
FEED = "https://api.seasonaljobs.dol.gov/datahub-search/sjCaseData/zip/h2b/{date}"


def seasonal(cfg, routes, **opts):
    src = make_source("seasonal_jobs_json", FEED, {"program": "h2b", "lookback_days": 1, **opts}, country="US")
    return run(cfg, src, routes_transport(routes))


def test_seasonal_jobs_builds_deterministic_extraction_with_verifiable_evidence(cfg):
    records = json.loads(fixture_text("json/seasonal_h2b_sample.json"))
    items, stats = seasonal(cfg, {FEED.format(date="2026-11-19"): httpx.Response(200, content=zip_json(records))})
    assert len(items) == 2 and stats["skipped_no_id"] == 1
    first, second = items
    ex = first.structured
    assert first.url == "https://seasonaljobs.dol.gov/jobs/H-TESTE-0001" and first.external_id == "H-TESTE-0001"
    assert ex.program_hint == "H-2B" and ex.country == "US" and ex.category == "job_with_package"
    assert {"salary_job", "housing_support", "travel_covered"} <= set(ex.benefit_types)
    assert (ex.value.amount, ex.value.currency, ex.value.period) == (17.5, "USD", "hourly")
    wa = next(r for r in ex.requirements if r.kind == "work_authorization")
    assert wa.work_authorization == "provided_by_employer" and verify_quote(first.text, wa.quote).found
    assert verify_quote(first.text, ex.value.quote).found
    # segundo registro: sem moradia informada e com requisito de experiência
    assert "housing_support" not in second.structured.benefit_types
    exp = next(r for r in second.structured.requirements if r.kind == "experience_min_months")
    assert exp.number == 12 and verify_quote(second.text, exp.quote).found
    # nomes de campo ainda não mapeados são reportados para ajustar o mapeamento
    assert "campo_novo_desconhecido" in stats["unmapped_keys"]


def test_seasonal_jobs_day_without_feed_is_no_data_not_failure(cfg):
    items, stats = seasonal(cfg, {})
    assert items == [] and stats["days_without_data"] == 1


def test_seasonal_jobs_accepts_plain_json_without_zip(cfg):
    records = [json.loads(fixture_text("json/seasonal_h2b_sample.json"))[0]]
    items, _ = seasonal(cfg, {FEED.format(date="2026-11-19"): httpx.Response(200, json={"data": records})})
    assert len(items) == 1


def test_seasonal_jobs_rejects_unknown_options(cfg):
    with pytest.raises(ConnectorOptionsError, match="opcao_inventada"):
        build_connector(make_source("seasonal_jobs_json", FEED, {"program": "h2b", "opcao_inventada": 1}, country="US"), cfg)


# --- fixture -----------------------------------------------------------------------------------------
def test_fixture_connector_reads_local_file_and_marks_it_as_test(cfg):
    src = cfg.source("test-fixture-rent-bonus")
    items, _ = run(cfg, src, routes_transport({}))
    assert items[0].url == src.url
    assert items[0].metadata["fixture"] is True
    assert "3.000,00 euro" in items[0].text
