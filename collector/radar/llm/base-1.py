"""Base dos adaptadores de IA: erros, registro de chamada (tokens/latência) e POST com retry/backoff."""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable

import httpx

from ..logs import redact


class LlmError(Exception):
    """Falha de IA (rede, HTTP, saída fora do schema). Nunca contém chaves de API."""


class LlmNotConfigured(LlmError):
    """Chave/modelo ausente ou provedor desabilitado: o pipeline segue sem IA (documento fica pending_llm)."""


@dataclass(frozen=True)
class LlmCall:
    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    latency_ms: int


_RETRYABLE = {408, 409, 425, 429, 500, 502, 503, 504, 529}


def _error_detail(resp: httpx.Response) -> str:
    try:
        data = resp.json()
        err = data.get("error")
        msg = err.get("message") if isinstance(err, dict) else err
        return redact(str(msg or "")[:200])
    except (ValueError, AttributeError):
        return ""


def post_json(
    *, provider: str, url: str, headers: dict[str, str], body: dict[str, Any], timeout_s: float, max_retries: int,
    transport: httpx.BaseTransport | None, sleep: Callable[[float], None] = time.sleep,
) -> tuple[dict[str, Any], int]:
    """POST JSON com retry exponencial em 429/5xx/rede. Devolve (json, latência_ms)."""
    last = "sem resposta"
    started = time.monotonic()
    with httpx.Client(transport=transport, timeout=timeout_s) as client:
        for attempt in range(max_retries + 1):
            delay = min(2.0 ** attempt, 30.0)
            try:
                resp = client.post(url, headers=headers, json=body)
            except httpx.HTTPError as exc:
                last = f"erro de rede ({exc.__class__.__name__})"
            else:
                if resp.status_code < 400:
                    try:
                        return resp.json(), int((time.monotonic() - started) * 1000)
                    except ValueError as exc:
                        raise LlmError(f"{provider}: resposta não é JSON") from exc
                if resp.status_code not in _RETRYABLE:
                    raise LlmError(f"{provider}: HTTP {resp.status_code}: {_error_detail(resp)}")
                last = f"HTTP {resp.status_code}"
                retry_after = resp.headers.get("retry-after", "")
                if retry_after.isdigit():
                    delay = min(float(retry_after), 60.0)
            if attempt < max_retries:
                sleep(delay)
    raise LlmError(f"{provider}: tentativas esgotadas ({last})")
