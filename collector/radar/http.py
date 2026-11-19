"""Cliente HTTP 'educado': robots.txt, limite de taxa por fonte, orçamento por execução, redirects revalidados."""
from __future__ import annotations

import os
import time
from typing import Callable
from urllib import robotparser
from urllib.parse import urljoin, urlparse

import httpx

from . import __version__
from .config import RateLimit

BOT_NAME = "GlobalOpportunityRadar"
_REDIRECTS = (301, 302, 303, 307, 308)


class FetchError(Exception):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class BlockedByRobots(Exception):
    pass


class RequestBudgetExceeded(Exception):
    pass


def default_user_agent() -> str:
    contact = os.environ.get("RADAR_CONTACT", "contato-nao-configurado")
    return f"{BOT_NAME}/{__version__} (+pesquisa pessoal sem fins comerciais; contato: {contact})"


class PoliteClient:
    def __init__(
        self,
        source_id: str,
        rate_limit: RateLimit,
        *,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        user_agent: str | None = None,
        timeout: float = 30.0,
        max_redirects: int = 3,
        max_bytes: int = 25_000_000,
    ) -> None:
        self.source_id = source_id
        self.rate_limit = rate_limit
        self.requests_made = 0
        self._sleep, self._clock = sleep, clock
        self._last: float | None = None
        self._max_redirects, self._max_bytes = max_redirects, max_bytes
        self._robots: dict[str, robotparser.RobotFileParser | None] = {}
        self._http = httpx.Client(
            transport=transport, timeout=timeout, follow_redirects=False,
            headers={"User-Agent": user_agent or default_user_agent(), "Accept-Language": "it,en;q=0.8,pt;q=0.6"},
        )

    # -- internos -----------------------------------------------------------------
    def _wait(self) -> None:
        interval = 60.0 / self.rate_limit.requests_per_minute
        now = self._clock()
        if self._last is not None and now - self._last < interval:
            self._sleep(interval - (now - self._last))
        self._last = self._clock()

    def _send(self, url: str, accept: str | None = None) -> httpx.Response:
        self._wait()
        try:
            return self._http.get(url, headers={"Accept": accept} if accept else None)
        except httpx.HTTPError as exc:
            raise FetchError(f"falha de rede em {url}: {exc.__class__.__name__}") from exc

    def _robots_for(self, url: str) -> robotparser.RobotFileParser | None:
        p = urlparse(url)
        origin = f"{p.scheme}://{p.netloc}"
        if origin in self._robots:
            return self._robots[origin]
        try:
            resp = self._send(f"{origin}/robots.txt")
        except FetchError as exc:
            raise BlockedByRobots(f"robots.txt indisponível em {origin}: bloqueio conservador") from exc
        if resp.status_code == 200:
            rp = robotparser.RobotFileParser()
            rp.parse(resp.text.splitlines())
            self._robots[origin] = rp
        elif 400 <= resp.status_code < 500 and resp.status_code != 429:
            self._robots[origin] = None  # sem robots.txt => permitido
        else:
            raise BlockedByRobots(f"robots.txt indisponível em {origin} (HTTP {resp.status_code}): bloqueio conservador")
        return self._robots[origin]

    def _allowed(self, url: str) -> bool:
        rp = self._robots_for(url)
        return rp is None or rp.can_fetch(BOT_NAME, url)

    # -- API pública -----------------------------------------------------------------
    def get(self, url: str, *, accept: str | None = None) -> httpx.Response:
        if urlparse(url).scheme not in ("http", "https"):
            raise FetchError(f"esquema não permitido: {url}")
        current = url
        resp: httpx.Response | None = None
        for _ in range(self._max_redirects + 1):
            if self.requests_made >= self.rate_limit.max_requests_per_run:
                raise RequestBudgetExceeded(f"limite de {self.rate_limit.max_requests_per_run} requisições por execução")
            if not self._allowed(current):
                raise BlockedByRobots(f"robots.txt proíbe {current}")
            resp = self._send(current, accept)
            self.requests_made += 1
            loc = resp.headers.get("location")
            if resp.status_code in _REDIRECTS and loc:
                current = urljoin(current, loc)
                if urlparse(current).scheme not in ("http", "https"):
                    raise FetchError(f"redirect para esquema não permitido: {current}")
                continue
            break
        assert resp is not None
        if resp.status_code in _REDIRECTS:
            raise FetchError("redirects demais", resp.status_code)
        if resp.status_code >= 400:
            raise FetchError(f"HTTP {resp.status_code} em {current}", resp.status_code)
        if len(resp.content) > self._max_bytes:
            raise FetchError(f"resposta maior que {self._max_bytes} bytes")
        return resp

    def close(self) -> None:
        self._http.close()
