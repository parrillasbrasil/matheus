import json
import logging
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from radar.alerts import (
    AlertInput, AlertSendError, SmtpSender, TelegramSender, channels_from_env, decide_alert, render_message,
)
from radar.deadlines import ResolvedDeadline

NOW = datetime(2026, 11, 19, 18, 0, tzinfo=timezone.utc)
NO_DL = ResolvedDeadline(utc=None, tz=None, tz_assumed=False, confirmed=False)


def dl(days, confirmed=True, assumed=False):
    return ResolvedDeadline(utc=NOW + timedelta(days=days), tz="Europe/Rome", tz_assumed=assumed, confirmed=confirmed)


def inp(**kw):
    base = dict(status="open", eligibility_status="eligible", score=80.0, is_official=True, verified_evidence=3, deadline=dl(60))
    base.update(kw)
    return AlertInput(**base)


def decide(cfg, a, change="new", meaningful=True, ever=False, urgent_sent=False):
    return decide_alert(cfg, a, change=change, meaningful_change=meaningful, ever_alerted=ever, urgent_sent=urgent_sent, now=NOW)


def test_new_eligible_open_opportunity_alerts(cfg):
    d = decide(cfg, inp())
    assert d.send and d.kind == "new"


def test_possibly_eligible_with_evidence_alerts_too(cfg):
    assert decide(cfg, inp(eligibility_status="needs_confirmation")).send


def test_expired_or_closed_opportunity_never_alerts_as_open_application(cfg):
    for a in (inp(status="closed"), inp(status="open", deadline=dl(-3))):
        d = decide(cfg, a)
        assert not d.send and ("vencid" in d.reason or "encerrad" in d.reason)


@pytest.mark.parametrize(
    "kw,word",
    [
        (dict(eligibility_status="ineligible"), "inelegível"),
        (dict(score=10.0), "pontuação"),
        (dict(is_official=False), "oficial"),
        (dict(verified_evidence=0), "evidência"),
    ],
)
def test_policy_suppressions_explain_why(cfg, kw, word):
    d = decide(cfg, inp(**kw))
    assert not d.send and word in d.reason


def test_urgent_only_with_confirmed_deadline_and_open_status(cfg):
    assert decide(cfg, inp(deadline=dl(3))).kind == "urgent"
    assert decide(cfg, inp(deadline=dl(3, confirmed=False, assumed=True))).kind == "new"   # fuso presumido
    assert decide(cfg, inp(deadline=dl(3), status="unknown")).kind == "new"                # status não confirmado aberto
    assert decide(cfg, inp(deadline=dl(30))).kind == "new"


def test_unchanged_never_alerts_and_trivial_changes_do_not_realert(cfg):
    assert not decide(cfg, inp(), change="unchanged").send
    assert not decide(cfg, inp(), change="changed", meaningful=False, ever=True).send


def test_meaningful_change_realerts_as_changed_or_new_if_never_alerted(cfg):
    assert decide(cfg, inp(), change="changed", ever=True).kind == "changed"
    assert decide(cfg, inp(), change="changed", ever=False).kind == "new"


def test_urgent_fires_once_when_an_unchanged_opportunity_enters_the_window(cfg):
    d = decide(cfg, inp(deadline=dl(3)), change="unchanged", meaningful=False, ever=True)
    assert d.send and d.kind == "urgent"
    assert not decide(cfg, inp(deadline=dl(3)), change="unchanged", meaningful=False, ever=True, urgent_sent=True).send


def test_unchanged_far_from_deadline_stays_silent_even_if_never_alerted_urgent(cfg):
    assert not decide(cfg, inp(deadline=dl(30)), change="unchanged", meaningful=False, ever=True).send


def test_realert_can_be_disabled_in_config(cfg):
    cfg.alerts.realert_on_change = False
    assert not decide(cfg, inp(), change="changed", ever=True).send


# --- mensagem e canais --------------------------------------------------------------------
OPP = dict(
    title_pt="Bônus de aluguel", title_original="Bonus affitto", canonical_url="https://www.comune.x.it/bando",
    data_origin="real", issuer="Comune X", country="IT", value_amount=3000, value_currency="EUR", value_period="one_time",
    deadline_at=datetime(2027, 3, 31, 21, 59, 59, tzinfo=timezone.utc), deadline_tz="Europe/Rome", deadline_confirmed=True,
    status="open", eligibility_status="eligible", relocation_condition="after_award",
    eligibility_summary="Elegível. Exige mudança de residência posterior.", current_score=88.5,
    evidence=[{"field": "value", "quote": "contributo di 3.000,00 euro"}],
)


def test_message_has_official_link_evidence_deadline_tz_and_no_application_disclaimer():
    m = render_message(OPP, "new")
    assert "https://www.comune.x.it/bando" in m.text
    assert "contributo di 3.000,00 euro" in m.text
    assert "Europe/Rome" in m.text and "mudança de residência" in m.text
    assert "nenhuma candidatura foi enviada" in m.text.lower()
    assert "[TESTE]" not in m.subject


def test_fixture_origin_is_labelled_test_and_unknowns_are_explicit():
    opp = dict(OPP, data_origin="fixture", value_amount=None, deadline_at=None, deadline_tz=None)
    m = render_message(opp, "new")
    assert m.subject.startswith("[TESTE]")
    assert "desconhecido" in m.text


def test_urgent_subject_is_marked():
    assert "URGENTE" in render_message(OPP, "urgent").subject


def test_telegram_sender_posts_message_and_never_logs_the_token(caplog):
    caplog.set_level(logging.INFO)  # o httpx loga a URL (que carrega o token) em INFO
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"], seen["body"] = str(request.url), json.loads(request.content)
        return httpx.Response(200, json={"ok": True})

    TelegramSender("123456:TOKEN", "42", transport=httpx.MockTransport(handler)).send(render_message(OPP, "new"))
    assert seen["url"] == "https://api.telegram.org/bot123456:TOKEN/sendMessage"
    assert seen["body"]["chat_id"] == "42" and "Bônus de aluguel" in seen["body"]["text"]
    assert "TOKEN" not in caplog.text


def test_telegram_failure_raises_a_redacted_error():
    t = httpx.MockTransport(lambda r: httpx.Response(400, json={"ok": False, "description": "chat not found"}))
    with pytest.raises(AlertSendError) as ei:
        TelegramSender("123456:TOKEN", "42", transport=t).send(render_message(OPP, "new"))
    assert "TOKEN" not in str(ei.value)


def test_smtp_sender_builds_a_message():
    sent = []

    class FakeSMTP:
        def __init__(self, host, port): sent.append(("connect", host, port))
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def starttls(self): sent.append(("starttls",))
        def login(self, u, p): sent.append(("login", u))
        def send_message(self, msg): sent.append(("msg", msg["Subject"], msg["To"]))

    SmtpSender("smtp.exemplo.com", 587, "user", "pw", "radar@exemplo.com", "pai@exemplo.com", smtp_factory=FakeSMTP).send(render_message(OPP, "new"))
    assert ("starttls",) in sent and any(s[0] == "msg" and "Bônus de aluguel" in s[1] for s in sent)


def test_channels_follow_config_and_environment(cfg):
    assert channels_from_env(cfg, {}) == []
    env = {"TELEGRAM_BOT_TOKEN": "1:abc", "TELEGRAM_CHAT_ID": "9"}
    assert [c.name for c in channels_from_env(cfg, env)] == ["telegram"]
    cfg.alerts.channels.telegram.enabled = False
    assert channels_from_env(cfg, env) == []
