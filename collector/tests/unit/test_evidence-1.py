from tests.conftest import make_extraction, make_req

from radar.evidence import date_consistent, verify_extraction
from radar.schema import Deadline, MoneyValue, TextItem

DOC = (
    "Il Comune eroga un contributo di 3.000,00 euro in un'unica soluzione.\n\n"
    "Possono presentare domanda i cittadini dell'Unione Europea di età non superiore a 35 anni.\n\n"
    "La domanda deve essere presentata entro il 31 marzo 2027. Bando aperto.\n\n"
    "Non sono coperte le spese di viaggio."
)
VALUE_Q = "contributo di 3.000,00 euro"
DEADLINE_Q = "entro il 31 marzo 2027"
REQ_Q = "cittadini dell'Unione Europea di età non superiore a 35 anni"


def good(**kw):
    base = dict(
        value=MoneyValue(amount=3000, currency="EUR", gross_net="unknown", period="one_time", quote=VALUE_Q),
        deadline=Deadline(iso="2027-03-31", timezone=None, quote=DEADLINE_Q),
        status="open", status_quote="Bando aperto",
        requirements=[make_req("age_max", number=35, quote=REQ_Q, verified=False)],
        not_covered_expenses=[TextItem(text_pt="Viagem não coberta", quote="Non sono coperte le spese di viaggio")],
    )
    base.update(kw)
    return make_extraction(**base)


def test_fully_supported_extraction_keeps_everything_and_scores_full_ratio():
    v = verify_extraction(good(), DOC)
    assert v.ratio == 1.0 and v.failures == []
    assert v.status_verified and v.deadline_quote_verified
    assert v.extraction.requirements[0].verified is True
    assert v.extraction.value.amount == 3000
    assert all(s.verified and s.start is not None for s in v.spans)


def test_fabricated_value_quote_clears_the_value():
    ex = good(value=MoneyValue(amount=999999, currency="EUR", gross_net="unknown", period="one_time", quote="contributo di 999.999 euro garantito"))
    v = verify_extraction(ex, DOC)
    assert v.extraction.value.amount is None and v.extraction.value.currency is None
    assert any("valor" in f for f in v.failures) and v.ratio < 1.0


def test_real_quote_with_a_different_amount_also_clears_the_value():
    ex = good(value=MoneyValue(amount=30000, currency="EUR", gross_net="unknown", period="one_time", quote=VALUE_Q))
    assert verify_extraction(ex, DOC).extraction.value.amount is None


def test_deadline_without_literal_quote_is_dropped_not_guessed():
    ex = good(deadline=Deadline(iso="2027-03-31", timezone=None, quote="scadenza il 31 marzo 2027 alle 12:00"))
    v = verify_extraction(ex, DOC)
    assert v.extraction.deadline.iso is None and v.deadline_quote_verified is False


def test_deadline_that_contradicts_its_own_quote_is_dropped():
    ex = good(deadline=Deadline(iso="2027-04-30", timezone=None, quote=DEADLINE_Q))
    assert verify_extraction(ex, DOC).extraction.deadline.iso is None


def test_unverified_status_is_not_trusted():
    v = verify_extraction(good(status_quote="Bando aperto a tutti"), DOC)
    assert v.status_verified is False


def test_requirement_with_fabricated_quote_stays_but_unverified():
    ex = good(requirements=[make_req("age_max", number=35, quote="solo under 25", verified=True)])  # IA não pode marcar verified
    v = verify_extraction(ex, DOC)
    assert v.extraction.requirements[0].verified is False
    assert any("requisito" in f for f in v.failures)


def test_obligations_and_uncovered_expenses_without_evidence_are_dropped():
    ex = good(not_covered_expenses=[TextItem(text_pt="Inventado", quote="spese legali coperte")])
    assert verify_extraction(ex, DOC).extraction.not_covered_expenses == []


def test_no_claims_means_zero_ratio_not_full_marks():
    assert verify_extraction(make_extraction(), DOC).ratio == 0.0


def test_date_consistency_helper():
    assert date_consistent("2027-03-31", "entro il 31 marzo 2027")
    assert date_consistent("2026-12-15T12:00:00", "12:00 noon ET, Dec 15")
    assert not date_consistent("2027-04-30", "entro il 31 marzo 2027")
    assert not date_consistent("2027-03-31", "entro il 31 marzo 2028")


def test_timezone_not_stated_in_the_quote_is_discarded():
    # a IA costuma "completar" o fuso; sem texto que o declare, o prazo não pode virar "confirmado"
    ex = good(deadline=Deadline(iso="2027-03-31", timezone="Europe/Rome", quote=DEADLINE_Q))
    v = verify_extraction(ex, DOC)
    assert v.extraction.deadline.iso == "2027-03-31" and v.extraction.deadline.timezone is None


def test_timezone_stated_in_the_quote_is_kept():
    doc = DOC + "\n\nLe domande scadono il 15 dicembre 2026 alle ore 12:00 CET."
    ex = good(deadline=Deadline(iso="2026-12-15T12:00:00", timezone="Europe/Rome", quote="15 dicembre 2026 alle ore 12:00 CET"))
    v = verify_extraction(ex, doc)
    assert v.deadline_quote_verified and v.extraction.deadline.timezone == "Europe/Rome"


def test_french_et_is_not_mistaken_for_eastern_time():
    doc = "Les candidatures sont ouvertes jusqu'au 31 mars 2027 et les dossiers incomplets seront rejetés."
    ex = good(deadline=Deadline(iso="2027-03-31", timezone="America/New_York", quote="jusqu'au 31 mars 2027 et les dossiers"),
              value=MoneyValue(amount=None, currency=None, gross_net="unknown", period="unknown", quote=None),
              status="unknown", status_quote=None, requirements=[], not_covered_expenses=[])
    assert verify_extraction(ex, doc).extraction.deadline.timezone is None
