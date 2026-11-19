"""Kit dos testes de pipeline. TUDO aqui é TESTE: extratores/auditores falsos e fixtures sintéticas."""
from __future__ import annotations

import shutil
from datetime import datetime, timezone

from radar.llm import AuditResult, ExtractionResult, LlmCall, LlmError, LlmNotConfigured, Verdict
from radar.pipeline import Deps
from radar.schema import Deadline, MoneyValue, TextItem
from radar.sync import sync_sources
from tests.conftest import REPO_ROOT, make_extraction, make_req

NOW = datetime(2026, 11, 19, 18, 0, tzinfo=timezone.utc)
ENV = {"RADAR_ENV": "test"}
RENT, ESC, EXPIRED, INJECTION = ("test-fixture-rent-bonus", "test-fixture-esc", "test-fixture-expired", "test-fixture-injection")
RENT_URL = "https://www.comune.esempio-di-prova.it/__fixture__/bonus-affitto"


class FakeExtractor:
    def __init__(self, respond, *, configured=True, fail=False):
        self.respond, self.configured, self.fail, self.calls = respond, configured, fail, []

    def model(self):
        if not self.configured:
            raise LlmNotConfigured("OPENAI_API_KEY não definida")
        return "fake-model"

    def extract(self, url, title, text):
        self.calls.append((url, title, text))
        if self.fail:
            raise LlmError("falha simulada do provedor")
        return ExtractionResult(self.respond(url, title, text), LlmCall("openai", "fake-model", 100, 50, 1))


class FakeAuditor:
    def __init__(self, verdicts=None, overall="confirmed", configured=True):
        self.verdicts, self.overall, self.configured, self.calls = verdicts or {}, overall, configured, []

    def model(self):
        if not self.configured:
            raise LlmNotConfigured("ANTHROPIC_API_KEY não definida")
        return "fake-claude"

    def audit(self, url, text, claims):
        self.calls.append(claims)
        vs = [Verdict(claim_id=c["id"], verdict=self.verdicts.get(c["id"], "supported"), note_pt="nota de teste") for c in claims]
        return AuditResult(vs, self.overall, "", LlmCall("anthropic", "fake-claude", 80, 20, 1))


class FakeSender:
    def __init__(self, name="telegram", fail_times=0):
        self.name, self.fail_times, self.sent = name, fail_times, []

    def send(self, message):
        from radar.alerts import AlertSendError

        if self.fail_times > 0:
            self.fail_times -= 1
            raise AlertSendError(f"{self.name}: falha simulada")
        self.sent.append(message)


def rent_bonus(url, title, text):
    big = "4.000,00" in text
    amount_text, amount = ("4.000,00", 4000) if big else ("3.000,00", 3000)
    if "12:00 CET" in text:
        dl = Deadline(iso="2027-03-31T12:00:00", timezone="Europe/Rome", quote="entro il 31 marzo 2027 alle ore 12:00 CET")
    else:
        dl = Deadline(iso="2027-03-31", timezone=None, quote="entro il 31 marzo 2027")
    return make_extraction(
        title_original="Bonus affitto per nuovi residenti", title_pt="Bônus de aluguel para novos moradores",
        issuer="Comune di Esempio di Prova", country="IT", benefit_types=["relocation_grant", "housing_support"],
        category="gov_relocation_incentive", aid_type="grant", benefit_summary_pt="Contribuição única para quem muda a residência.",
        value=MoneyValue(amount=amount, currency="EUR", gross_net="unknown", period="one_time", quote=f"contributo di {amount_text} euro"),
        deadline=dl, status="open", status_quote="Bando aperto",
        requirements=[
            make_req("nationality_any", id="r-nat", values=["EU"], quote="cittadini dell'Unione Europea", verified=False),
            make_req("age_max", id="r-age", number=35, quote="età non superiore a 35 anni", verified=False),
            make_req("residence_after_move", id="r-res", values=["IT"], quote="trasferiscono la residenza anagrafica nel comune entro 6 mesi", verified=False),
        ],
        obligations=[TextItem(text_pt="Manter a residência por 5 anos", quote="mantenere la residenza per almeno 5 anni")],
        not_covered_expenses=[TextItem(text_pt="Viagem, caução e contas", quote="Non sono coperte le spese di viaggio, il deposito cauzionale e le utenze")],
    )


def esc_placement(url, title, text):
    return make_extraction(
        program_hint="European Solidarity Corps", title_original="European Solidarity Corps volunteering placement in Lisbon",
        issuer="Hosting organisation", country="PT", benefit_types=["youth_program_covered", "housing_support"],
        category="youth_program_covered", aid_type="in_kind",
        deadline=Deadline(iso="2026-12-15T12:00:00", timezone="Europe/Paris", quote="15 December 2026, 12:00 CET"),
        status="open", status_quote="Applications are open",
        requirements=[make_req("age_min", id="r-min", number=18, quote="aged 18 to 30", verified=False),
                      make_req("age_max", id="r-max", number=30, quote="aged 18 to 30", verified=False)],
    )


def expired_bando(url, title, text):
    return make_extraction(
        title_original="Bando contributo per il trasferimento della residenza", issuer="Regione di Prova", country="IT",
        benefit_types=["relocation_grant"], category="gov_relocation_incentive", aid_type="grant",
        value=MoneyValue(amount=5000, currency="EUR", gross_net="unknown", period="one_time", quote="Contributo a fondo perduto di 5.000,00 euro"),
        deadline=Deadline(iso="2026-04-30", timezone=None, quote="entro il 30 aprile 2026"),
        status="open", status_quote="Stato del bando: aperto",
        requirements=[make_req("nationality_any", id="r-nat", values=["EU"], quote="cittadini dell'Unione Europea maggiorenni", verified=False)],
    )


def deps(extractor=None, auditor=None, senders=None):
    return Deps(extractor=extractor, auditor=auditor, senders=senders or [], sleep=lambda s: None, env=ENV)


def setup(aconn, cfg, *source_ids, now=NOW):
    """Habilita só as fixtures pedidas (RADAR_ENV=test) e sincroniza config -> banco."""
    for s in cfg.sources:
        s.enabled = s.id in source_ids
    sync_sources(aconn, cfg, ENV, now)


def use_tmp_repo(tmp_path, monkeypatch):
    """Copia as fixtures para um diretório temporário para poder alterá-las entre execuções."""
    dest = tmp_path / "collector" / "tests" / "fixtures" / "html"
    shutil.copytree(REPO_ROOT / "collector" / "tests" / "fixtures" / "html", dest)
    monkeypatch.setenv("RADAR_REPO_ROOT", str(tmp_path))
    return dest


def edit(path, old, new):
    text = path.read_text(encoding="utf-8")
    assert old in text, old
    path.write_text(text.replace(old, new), encoding="utf-8")


def count(conn, table, where="true"):
    return conn.execute(f"select count(*) as n from {table} where {where}").fetchone()["n"]
