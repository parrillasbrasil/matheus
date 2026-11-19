"""Decide se uma URL pertence a um domínio oficial (lista vem do config.yaml)."""
from __future__ import annotations

import re
from urllib.parse import urlparse

from .config import Config


def host_of(url: str) -> str | None:
    try:
        parsed = urlparse(url)
    except ValueError:
        return None
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return None
    return parsed.hostname.lower().rstrip(".")


def is_official_url(cfg: Config, url: str) -> bool:
    host = host_of(url)
    if not host:
        return False
    src = cfg.official_sources
    for exact in src.domain_exact:
        exact = exact.lower()
        if host == exact or host == f"www.{exact}" or host.endswith("." + exact):
            return True
    for suffix in src.domain_suffixes:
        s = suffix.lower()
        if host.endswith(s) or host == s.lstrip("."):
            return True
    return any(re.match(pat, host) for pat in src.domain_regex)
