"""Verificação de evidência: nada que a IA afirmou sobrevive sem citação literal no documento oficial.

Regras (todas determinísticas):
- valor: a citação precisa existir no texto E conter exatamente o valor numérico; senão o valor vira desconhecido;
- prazo: a citação precisa existir E a data do ISO precisa constar nela; senão o prazo vira desconhecido;
- status: 'open'/'closed' só vale se a citação existir (efeito decidido em deadlines.effective_status);
- requisitos: ficam, mas `verified` só é True se a citação existir (a IA nunca decide `verified`);
- obrigações/despesas não cobertas sem citação verificada são descartadas.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from dateutil import parser as dtparser

from .schema import Deadline, Extraction, MoneyValue
from .textutil import amount_in_text, verify_quote


@dataclass(frozen=True)
class Span:
    field: str
    quote: str
    verified: bool
    start: int | None
    end: int | None


@dataclass
class Verified:
    extraction: Extraction
    spans: list[Span] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    claims: int = 0
    supported: int = 0
    status_verified: bool = False
    deadline_quote_verified: bool = False

    @property
    def ratio(self) -> float:
        return self.supported / self.claims if self.claims else 0.0


# --- datas ------------------------------------------------------------------------------------
_MONTHS: dict[str, int] = {}
for _m, _names in enumerate(
    [
        "january gennaio enero janeiro janvier januar jan gen ene janv",
        "february febbraio febrero fevereiro fevrier februar feb fev febr",
        "march marzo marco mars marz mar",
        "april aprile abril avril apr abr avr",
        "may maggio mayo maio mai mag",
        "june giugno junio junho juin juni jun giu",
        "july luglio julio julho juillet juli jul lug juil",
        "august agosto aout ago aug",
        "september settembre septiembre setiembre setembro septembre sep sept set",
        "october ottobre octubre outubro octobre oktober oct ott out okt",
        "november novembre noviembre novembro nov",
        "december dicembre diciembre dezembro decembre dezember dec dic dez",
    ],
    start=1,
):
    for _n in _names.split():
        _MONTHS[_n] = _m

_MONTH_RE = "|".join(sorted(map(re.escape, _MONTHS), key=len, reverse=True))
_DAY_MONTH = re.compile(rf"(?<!\d)(\d{{1,2}})(?:st|nd|rd|th|er|o|ª|°)?\.?\s+(?:(?:de|of|di|del)\s+)?({_MONTH_RE})\b")
_MONTH_DAY = re.compile(rf"\b({_MONTH_RE})\b\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?(?!\d)")
_NUMERIC = re.compile(r"(?<!\d)(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{4}|\d{2})(?!\d)")
_ISO = re.compile(r"(?<!\d)(\d{4})-(\d{2})-(\d{2})(?!\d)")
_YEAR = re.compile(r"(?<!\d)(20\d{2})(?!\d)")


# Fuso "declarado pela fonte": sigla em MAIÚSCULAS (evita o "et" francês), offset após horário ou nome por extenso.
_TZ_ABBR = re.compile(r"\b(?:UTC|GMT|CEST|CET|EEST|EET|WEST|WET|BST|EDT|EST|CDT|CST|MDT|MST|PDT|PST|ET|CT|MT|PT)\b")
_TZ_PHRASE = re.compile(
    r"\d{1,2}[:.h]\d{2}(?::\d{2})?\s?(?:Z|[+\-−]\d{2}:?\d{2})\b"
    r"|\b(?:eastern|central|mountain|pacific)\s+(?:standard\s+|daylight\s+)?time\b"
    r"|ora italiana|italian time|heure de paris|hora peninsular|hor[aá]rio de bras[ií]lia|hora de bras[ií]lia",
    re.IGNORECASE,
)


def timezone_stated(quote: str | None) -> bool:
    return bool(quote and (_TZ_ABBR.search(quote) or _TZ_PHRASE.search(quote)))


def _fold(text: str) -> str:
    s = unicodedata.normalize("NFKD", text)
    return "".join(c for c in s if not unicodedata.combining(c)).lower()


def date_consistent(iso: str, quote: str) -> bool:
    """O dia/mês do ISO precisa aparecer na citação; se a citação traz ano(s), o ano do ISO também."""
    try:
        parsed = dtparser.isoparse(iso.strip())
    except (ValueError, OverflowError):
        return False
    text = _fold(quote)
    pairs: set[tuple[int, int]] = set()  # (mês, dia)
    years = {int(y) for y in _YEAR.findall(text)}
    for day, name in _DAY_MONTH.findall(text):
        pairs.add((_MONTHS[name], int(day)))
    for name, day in _MONTH_DAY.findall(text):
        pairs.add((_MONTHS[name], int(day)))
    for a, b, y in _NUMERIC.findall(text):
        pairs.add((int(b), int(a)))   # dd/mm/aaaa
        pairs.add((int(a), int(b)))   # mm/dd/aaaa
        years.add(int(y) if len(y) == 4 else 2000 + int(y))
    for y, m, d in _ISO.findall(text):
        pairs.add((int(m), int(d)))
        years.add(int(y))
    if (parsed.month, parsed.day) not in pairs:
        return False
    return not years or parsed.year in years


# --- verificação ----------------------------------------------------------------------------------
def verify_extraction(ex: Extraction, doc_text: str) -> Verified:
    out = Verified(extraction=ex)
    updates: dict = {}

    def claim(field_name: str, quote: str | None, ok_extra: bool = True) -> bool:
        """Registra uma afirmação; devolve True se a citação existe literalmente (e ok_extra)."""
        out.claims += 1
        m = verify_quote(doc_text, quote)
        ok = m.found and ok_extra
        if quote and quote.strip():
            out.spans.append(Span(field_name, quote, ok, m.start if m.found else None, m.end if m.found else None))
        if ok:
            out.supported += 1
        return ok

    # valor
    v = ex.value
    if v.amount is not None:
        ok = claim("value", v.quote, ok_extra=bool(v.quote) and amount_in_text(v.amount, v.quote or ""))
        if not ok:
            out.failures.append("valor: citação ausente, inexistente no documento ou sem o valor informado; valor descartado")
            updates["value"] = MoneyValue(amount=None, currency=None, gross_net="unknown", period="unknown", quote=None)

    # prazo
    d = ex.deadline
    if (d.iso or "").strip():
        quote_ok = verify_quote(doc_text, d.quote).found
        ok = claim("deadline", d.quote, ok_extra=quote_ok and date_consistent(d.iso or "", d.quote or ""))
        out.deadline_quote_verified = ok
        if ok:
            # a IA costuma "completar" o fuso: sem texto que o declare, o prazo não pode virar "confirmado"
            updates["deadline"] = d if (d.timezone is None or timezone_stated(d.quote)) else d.model_copy(update={"timezone": None})
        else:
            out.failures.append("prazo: citação ausente, inexistente ou incompatível com a data; prazo descartado")
            updates["deadline"] = Deadline(iso=None, timezone=None, quote=None)

    # status
    if ex.status in ("open", "closed"):
        out.status_verified = claim("status", ex.status_quote)
        if not out.status_verified:
            out.failures.append("status: citação ausente ou inexistente; status não confiável")

    # requisitos (a IA nunca decide `verified`)
    reqs = []
    for r in ex.requirements:
        ok = claim(f"requirement:{r.id}", r.quote)
        if not ok:
            out.failures.append(f"requisito {r.id}: citação ausente ou inexistente no documento")
        reqs.append(r.model_copy(update={"verified": ok}))
    updates["requirements"] = reqs

    # obrigações e despesas não cobertas: sem evidência, descartar
    for attr, label in (("obligations", "obrigação"), ("not_covered_expenses", "despesa não coberta")):
        kept = []
        for item in getattr(ex, attr):
            if claim(f"{attr}", item.quote):
                kept.append(item)
            else:
                out.failures.append(f"{label} sem evidência verificada: descartada")
        updates[attr] = kept

    out.extraction = ex.model_copy(update=updates)
    return out
