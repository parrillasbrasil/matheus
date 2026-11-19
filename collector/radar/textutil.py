"""Normalização, hash de conteúdo, verificação literal de evidência, valores monetários e seleção de trechos."""
from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

# Mapa 1:1 (preserva comprimento) para comparar citações sem perder os offsets
_FOLD = str.maketrans(
    {
        "‘": "'", "’": "'", "‚": "'", "‛": "'",
        "“": '"', "”": '"', "„": '"', "«": '"', "»": '"',
        "–": "-", "—": "-", "−": "-", " ": " ", " ": " ", " ": " ",
    }
)


def normalize_text(raw: str) -> str:
    s = unicodedata.normalize("NFKC", raw)
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    s = re.sub(r"[ \t\f\v]+", " ", s)
    s = re.sub(r" *\n *", "\n", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


def content_hash(text: str, volatile_patterns: list[str] | tuple[str, ...] = ()) -> str:
    s = normalize_text(text)
    for pat in volatile_patterns:
        s = re.sub(pat, "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class QuoteMatch:
    found: bool
    start: int | None = None
    end: int | None = None


def verify_quote(doc_text: str, quote: str | None) -> QuoteMatch:
    """A citação precisa aparecer LITERALMENTE no documento (tolera só caixa, espaços e aspas tipográficas)."""
    if not quote or not quote.strip():
        return QuoteMatch(False)
    q = unicodedata.normalize("NFKC", quote).translate(_FOLD)
    tokens = q.split()
    if not tokens:
        return QuoteMatch(False)
    pattern = r"\s+".join(re.escape(t) for t in tokens)
    m = re.search(pattern, doc_text.translate(_FOLD), flags=re.IGNORECASE)
    return QuoteMatch(True, m.start(), m.end()) if m else QuoteMatch(False)


# --- valores monetários -----------------------------------------------------------
_NUM = r"\d{1,3}(?:[  ]\d{3})+(?:[.,]\d{1,2})?|\d+(?:[.,]\d+)*"
_SUFFIX = r"(?:\s?(?P<suf>mila|mil|milioni|millones|million|k)\b)?"
_AMOUNT_RE = re.compile(rf"(?P<num>{_NUM}){_SUFFIX}", re.IGNORECASE)
_MULT = {"mila": 1000, "mil": 1000, "k": 1000, "milioni": 10**6, "millones": 10**6, "million": 10**6}


def _to_decimal(num: str) -> Decimal | None:
    s = num.replace(" ", "").replace(" ", "")
    has_dot, has_comma = "." in s, "," in s
    if has_dot and has_comma:
        dec = "." if s.rfind(".") > s.rfind(",") else ","
        thou = "," if dec == "." else "."
        s = s.replace(thou, "").replace(dec, ".")
    elif has_dot or has_comma:
        sep = "." if has_dot else ","
        parts = s.split(sep)
        if len(parts) > 2 or (len(parts[-1]) == 3 and 1 <= len(parts[0]) <= 3):
            s = "".join(parts)            # separador de milhar
        else:
            s = ".".join(parts)           # separador decimal
    try:
        return Decimal(s)
    except InvalidOperation:
        return None


def _match_value(m: re.Match[str]) -> Decimal | None:
    d = _to_decimal(m.group("num"))
    if d is None:
        return None
    suf = (m.group("suf") or "").lower()
    return d * _MULT[suf] if suf else d


def parse_amount(text: str) -> Decimal | None:
    m = _AMOUNT_RE.search(text)
    return _match_value(m) if m else None


def amount_in_text(amount: float | Decimal, text: str) -> bool:
    target = Decimal(str(amount))
    for m in _AMOUNT_RE.finditer(text):
        v = _match_value(m)
        if v is not None and v == target:
            return True
    return False


# --- relevância e economia de tokens ----------------------------------------------------
def relevance_hits(text: str, keywords: list[str]) -> int:
    low = text.lower()
    return sum(1 for k in set(keywords) if k.lower() in low)


@dataclass(frozen=True)
class Passages:
    text: str
    truncated: bool


_DATE_OR_MONEY = re.compile(r"(\d{1,2}[/.\-]\d{1,2}[/.\-]\d{2,4})|(€|\$|£|eur\b|euro\b|usd\b)|\d{4}", re.IGNORECASE)


def select_passages(text: str, keywords: list[str], max_chars: int) -> Passages:
    """Mantém título + parágrafos mais relevantes até max_chars (consumo de tokens otimizado)."""
    if len(text) <= max_chars:
        return Passages(text, False)
    paras = [p for p in re.split(r"\n\s*\n", text) if p.strip()]
    if len(paras) <= 1:
        paras = [p for p in text.split("\n") if p.strip()] or [text]
    low_kw = [k.lower() for k in set(keywords)]

    def score(p: str) -> float:
        low = p.lower()
        s = 3.0 * sum(1 for k in low_kw if k in low)
        s += 1.0 if re.search(r"\d", p) else 0.0
        s += 1.5 if _DATE_OR_MONEY.search(p) else 0.0
        return s

    chosen: set[int] = {0}
    used = min(len(paras[0]), max_chars)
    for idx in sorted(range(1, len(paras)), key=lambda i: (-score(paras[i]), i)):
        if score(paras[idx]) <= 0:
            continue
        add = len(paras[idx]) + 2
        if used + add <= max_chars:
            chosen.add(idx)
            used += add
    out = "\n\n".join(paras[i] for i in sorted(chosen))
    truncated = len(chosen) < len(paras)
    if len(out) > max_chars:
        out, truncated = out[:max_chars], True
    return Passages(out, truncated)
