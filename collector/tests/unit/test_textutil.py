from decimal import Decimal

import pytest

from radar.textutil import (
    amount_in_text,
    content_hash,
    normalize_text,
    parse_amount,
    relevance_hits,
    select_passages,
    verify_quote,
)

DOC = (
    "Bando per il trasferimento della residenza.\n\n"
    "Il contributo è pari a 15.000,00 euro per ciascun beneficiario.\n\n"
    "La domanda deve essere presentata entro il 30 aprile 2027."
)


def test_normalize_text_collapses_whitespace_and_unifies_unicode():
    raw = "  Ciao mondo \t  \n\n\n\n  “testo”  "
    out = normalize_text(raw)
    assert "  " not in out
    assert "\n\n\n" not in out
    assert out.startswith("Ciao mondo")


def test_content_hash_ignores_volatile_patterns_but_not_real_changes():
    pats = [r"(?i)ultima modifica[^\n]*"]
    a = "Contributo 15.000 euro\nUltima modifica: 01/10/2026"
    b = "Contributo 15.000 euro\nUltima modifica: 19/11/2026"
    c = "Contributo 20.000 euro\nUltima modifica: 01/10/2026"
    assert content_hash(a, pats) == content_hash(b, pats)
    assert content_hash(a, pats) != content_hash(c, pats)


def test_verify_quote_finds_literal_text_with_offsets():
    m = verify_quote(DOC, "contributo è pari a 15.000,00 euro")
    assert m.found is True
    assert DOC.lower()[m.start : m.end] == "contributo è pari a 15.000,00 euro"


def test_verify_quote_tolerates_case_whitespace_and_curly_quotes_only():
    doc = "Il “Bonus   Casa” vale\n 5.000 euro"
    assert verify_quote(doc, 'il "bonus casa" vale 5.000 euro').found is True


@pytest.mark.parametrize(
    "fake",
    [
        "il contributo è pari a 25.000,00 euro",  # valor alterado
        "contributo garantito a tutti i residenti all'estero",  # inventada
        "",
    ],
)
def test_verify_quote_rejects_anything_not_literally_in_the_document(fake):
    assert verify_quote(DOC, fake).found is False


@pytest.mark.parametrize(
    "text,expected",
    [
        ("15.000,00", Decimal("15000.00")),
        ("15,000.00", Decimal("15000.00")),
        ("15.000", Decimal("15000")),
        ("€ 15 000", Decimal("15000")),
        ("1.234.567,89", Decimal("1234567.89")),
        ("15mila", Decimal("15000")),
        ("2.500,5", Decimal("2500.5")),
        ("abc", None),
    ],
)
def test_parse_amount_handles_local_formats(text, expected):
    assert parse_amount(text) == expected


def test_amount_in_text_confirms_value_appears_in_the_quote():
    assert amount_in_text(15000, "contributo pari a 15.000,00 euro") is True
    assert amount_in_text(25000, "contributo pari a 15.000,00 euro") is False


def test_select_passages_keeps_relevant_paragraphs_and_flags_truncation():
    noise = "\n\n".join(f"Paragrafo irrilevante numero {i} " + "lorem ipsum " * 40 for i in range(40))
    doc = "Titolo del bando\n\n" + noise + "\n\nIl contributo di 15.000 euro per chi trasferisce la residenza."
    out = select_passages(doc, ["contributo", "residenza"], max_chars=1200)
    assert out.truncated is True
    assert len(out.text) <= 1300
    assert "15.000 euro" in out.text
    assert out.text.startswith("Titolo del bando")


def test_select_passages_small_document_is_returned_whole():
    out = select_passages(DOC, ["contributo"], max_chars=5000)
    assert out.truncated is False
    assert "30 aprile 2027" in out.text


def test_relevance_hits_counts_distinct_keywords():
    assert relevance_hits(DOC, ["contributo", "residenza", "bando", "inexistente"]) == 3
