from datetime import datetime, timedelta, timezone

import pytest

from radar.lifecycle import refresh_opportunities
from radar.notify import dispatch_alerts
from radar.pipeline import run_source
from tests.integration.pipeline_kit import (
    NOW, RENT, FakeExtractor, FakeSender, count, deps, edit, rent_bonus, setup, use_tmp_repo,
)

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def one_channel(cfg):
    cfg.alerts.channels.smtp.enabled = False


def scan(conn, cfg, now=NOW):
    return run_source(conn, cfg, RENT, trigger="manual", now=now, deps=deps(FakeExtractor(rent_bonus)))


def alerts(conn):
    return conn.execute("select kind, channel, status, attempts, last_error, suppression_reason from alerts order by created_at, kind").fetchall()


# ---- despacho -------------------------------------------------------------------------------------------
def test_pending_alert_is_sent_exactly_once_with_the_official_link_and_test_label(aconn, cfg):
    setup(aconn, cfg, RENT); scan(aconn, cfg)
    s = FakeSender()
    assert dispatch_alerts(aconn, cfg, [s], NOW)["sent"] == 1
    assert dispatch_alerts(aconn, cfg, [s], NOW)["sent"] == 0 and len(s.sent) == 1
    m = s.sent[0]
    assert m.subject.startswith("[TESTE]") and "https://www.comune.esempio-di-prova.it/__fixture__/bonus-affitto" in m.text
    assert "contributo di 3.000,00 euro" in m.text and "Nenhuma candidatura foi enviada" in m.text
    assert alerts(aconn)[0]["status"] == "sent"


def test_failed_channel_is_retried_with_backoff_and_eventually_marked_failed(aconn, cfg):
    cfg.queue.max_attempts, cfg.queue.backoff_base_seconds = 2, 60
    setup(aconn, cfg, RENT); scan(aconn, cfg)
    s = FakeSender(fail_times=5)
    assert dispatch_alerts(aconn, cfg, [s], NOW)["failed_attempts"] == 1
    a = alerts(aconn)[0]
    assert a["status"] == "pending" and a["attempts"] == 1 and "falha simulada" in a["last_error"]
    assert dispatch_alerts(aconn, cfg, [s], NOW + timedelta(seconds=30))["failed_attempts"] == 0   # ainda em backoff
    dispatch_alerts(aconn, cfg, [s], NOW + timedelta(seconds=61))
    assert alerts(aconn)[0]["status"] == "failed"


def test_alert_waits_when_the_channel_has_no_credentials_and_goes_out_later(aconn, cfg):
    setup(aconn, cfg, RENT); scan(aconn, cfg)
    assert dispatch_alerts(aconn, cfg, [], NOW)["waiting"] == 1 and alerts(aconn)[0]["status"] == "pending"
    s = FakeSender()
    dispatch_alerts(aconn, cfg, [s], NOW + timedelta(days=1))
    assert len(s.sent) == 1


def test_alert_for_an_opportunity_that_closed_before_sending_is_suppressed(aconn, cfg):
    setup(aconn, cfg, RENT); scan(aconn, cfg)
    s = FakeSender()
    r = dispatch_alerts(aconn, cfg, [s], datetime(2027, 4, 2, tzinfo=timezone.utc))  # depois de 31/03/2027
    a = alerts(aconn)[0]
    assert r["suppressed"] == 1 and s.sent == [] and a["status"] == "suppressed" and "encerrad" in a["suppression_reason"]


# ---- ciclo de vida --------------------------------------------------------------------------------------
def test_open_opportunity_is_closed_when_its_deadline_passes_without_any_new_scan(aconn, cfg):
    setup(aconn, cfg, RENT); scan(aconn, cfg)
    assert refresh_opportunities(aconn, cfg, NOW + timedelta(days=30))["closed"] == 0
    assert refresh_opportunities(aconn, cfg, datetime(2027, 4, 1, tzinfo=timezone.utc))["closed"] == 1
    assert aconn.execute("select status from opportunities").fetchone()["status"] == "closed"
    assert aconn.execute("select count(*) n from audit_events where event='opportunity_closed_by_deadline'").fetchone()["n"] == 1


def test_urgent_alert_fires_once_when_a_confirmed_deadline_enters_the_window(aconn, cfg, tmp_path, monkeypatch):
    html = use_tmp_repo(tmp_path, monkeypatch) / "rent_bonus_it.html"
    edit(html, "entro il 31 marzo 2027.", "entro il 31 marzo 2027 alle ore 12:00 CET.")   # prazo COM fuso declarado
    setup(aconn, cfg, RENT); scan(aconn, cfg)
    assert [a["kind"] for a in alerts(aconn)] == ["new"]
    assert refresh_opportunities(aconn, cfg, NOW + timedelta(days=60))["urgent"] == 0
    assert refresh_opportunities(aconn, cfg, datetime(2027, 3, 28, 12, tzinfo=timezone.utc))["urgent"] == 1
    assert refresh_opportunities(aconn, cfg, datetime(2027, 3, 29, 12, tzinfo=timezone.utc))["urgent"] == 0   # só uma vez
    assert sorted(a["kind"] for a in alerts(aconn)) == ["new", "urgent"]


def test_no_urgent_alert_when_the_deadline_timezone_was_only_assumed(aconn, cfg):
    setup(aconn, cfg, RENT); scan(aconn, cfg)     # fixture sem fuso declarado => prazo NÃO confirmado
    assert refresh_opportunities(aconn, cfg, datetime(2027, 3, 28, 12, tzinfo=timezone.utc))["urgent"] == 0
