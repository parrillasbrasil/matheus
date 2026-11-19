"""Defesa barata contra prompt injection: páginas que tentam instruir "assistentes de IA" não são enviadas ao LLM."""
from __future__ import annotations

import re

_PATTERNS = [
    r"(?:ignore|disregard|forget)\s+(?:all\s+|any\s+)?(?:the\s+)?(?:previous|prior|above|earlier)\s+(?:instructions|prompts?|rules)",
    r"ignor[ae]\s+(?:tutte\s+)?le\s+istruzioni\s+(?:precedenti|sopra)",
    r"ignor[ae]\s+(?:todas\s+)?(?:as|las)\s+(?:instru[çc][õo]es|instrucciones)\s+(?:anteriores|previas)",
    r"(?:notice|message|note|attention)\s+to\s+(?:all\s+)?(?:ai|llm|language\s+model)s?(?:\s+assistants?)?",
    r"\bto\s+ai\s+assistants?\b",
    r"you\s+are\s+now\s+(?:an?\s+)?(?:unrestricted|different|new)\b",
    r"<\|\s*im_(?:start|end)\s*\|>",
    r"\bsystem\s+(?:prompt|override)\b",
]
_COMPILED = [re.compile(p, re.IGNORECASE) for p in _PATTERNS]


def looks_like_prompt_injection(text: str) -> list[str]:
    """Devolve os padrões que casaram (lista vazia = nada suspeito)."""
    return [p.pattern for p in _COMPILED if p.search(text)]
