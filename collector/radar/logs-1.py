"""Logs JSON estruturados SEM dados sensíveis (segredos, tokens, e-mails são redigidos)."""
from __future__ import annotations

import json
import logging
import re
import sys
from datetime import datetime, timezone

_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]+"), "Bearer [REDACTED]"),
    (re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}"), "[REDACTED]"),
    (re.compile(r"\b\d{6,}:[A-Za-z0-9_\-]{30,}\b"), "[REDACTED]"),
    (re.compile(r"(?i)\bbot\d+:[A-Za-z0-9_\-]+"), "bot[REDACTED]"),  # token na URL da API do Telegram
    (re.compile(r"(?i)\b(api[_-]?key|token|password|passwd|secret|authorization)\b(\s*[=:]\s*)\S+"), r"\1\2[REDACTED]"),
    (re.compile(r"[\w.+\-]+@[\w\-]+(?:\.[\w\-]+)+"), "[REDACTED]"),
]
_STD = set(logging.LogRecord("x", 0, "", 0, "", None, None).__dict__) | {"message", "asctime"}


def quiet_http_loggers() -> None:
    """httpx/httpcore registram a URL completa em INFO; a URL do Telegram carrega o token do bot."""
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)


def redact(text: str) -> str:
    for pat, repl in _PATTERNS:
        text = pat.sub(repl, text)
    return text


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        data = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": redact(record.getMessage()),
        }
        for key, value in record.__dict__.items():
            if key not in _STD and not key.startswith("_"):
                data[key] = redact(value) if isinstance(value, str) else value
        if record.exc_info:
            data["exc"] = redact(self.formatException(record.exc_info))
        return json.dumps(data, ensure_ascii=False, default=str)


def setup_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())
    quiet_http_loggers()
