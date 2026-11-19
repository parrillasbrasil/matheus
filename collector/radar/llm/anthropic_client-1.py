"""Claude: segunda validação INDEPENDENTE da evidência (não recebe nenhum dado do candidato)."""
from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Callable, Literal, Mapping

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError

from ..config import Config
from .base import LlmCall, LlmError, LlmNotConfigured, post_json
from .prompts import build_audit_messages

TOOL_NAME = "report_audit"


class Verdict(BaseModel):
    model_config = ConfigDict(extra="forbid")
    claim_id: str
    verdict: Literal["supported", "contradicted", "unsupported"]
    note_pt: str = ""


class AuditPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    verdicts: list[Verdict]
    overall: Literal["confirmed", "disputed", "inconclusive"]
    comments_pt: str = ""


@dataclass(frozen=True)
class AuditResult:
    verdicts: list[Verdict]
    overall: str
    comments_pt: str
    call: LlmCall


_TOOL = {
    "name": TOOL_NAME,
    "description": "Report the audit verdicts for every claim.",
    "input_schema": {
        "type": "object",
        "properties": {
            "verdicts": {"type": "array", "items": {
                "type": "object",
                "properties": {"claim_id": {"type": "string"},
                               "verdict": {"type": "string", "enum": ["supported", "contradicted", "unsupported"]},
                               "note_pt": {"type": "string"}},
                "required": ["claim_id", "verdict", "note_pt"]}},
            "overall": {"type": "string", "enum": ["confirmed", "disputed", "inconclusive"]},
            "comments_pt": {"type": "string"},
        },
        "required": ["verdicts", "overall", "comments_pt"],
    },
}


class ClaudeAuditor:
    def __init__(self, cfg: Config, env: Mapping[str, str] | None = None, transport: httpx.BaseTransport | None = None,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self.cfg = cfg.llm.anthropic
        self._env = os.environ if env is None else env
        self._transport, self._sleep = transport, sleep

    def model(self) -> str:
        if not self.cfg.enabled:
            raise LlmNotConfigured("Anthropic desabilitada no config.yaml")
        if not self._env.get(self.cfg.api_key_env):
            raise LlmNotConfigured(f"{self.cfg.api_key_env} não definida")
        return self._env.get(self.cfg.model_env) or self.cfg.default_model

    def audit(self, url: str, text: str, claims: list[dict[str, str]]) -> AuditResult:
        model = self.model()
        system, user = build_audit_messages(url, text, claims)
        body = {
            "model": model, "max_tokens": self.cfg.max_output_tokens, "system": system,
            "messages": [{"role": "user", "content": user}],
            "tools": [_TOOL], "tool_choice": {"type": "tool", "name": TOOL_NAME},
        }
        data, latency = post_json(
            provider="anthropic", url=self.cfg.base_url.rstrip("/") + "/v1/messages",
            headers={"x-api-key": self._env[self.cfg.api_key_env], "anthropic-version": self.cfg.api_version, "content-type": "application/json"},
            body=body, timeout_s=self.cfg.timeout_s, max_retries=self.cfg.max_retries, transport=self._transport, sleep=self._sleep,
        )
        block = next((b for b in data.get("content", []) if b.get("type") == "tool_use" and b.get("name") == TOOL_NAME), None)
        if block is None:
            raise LlmError("anthropic: resposta sem a chamada da ferramenta de auditoria")
        try:
            payload = AuditPayload.model_validate(block.get("input"))
        except ValidationError as exc:
            raise LlmError(f"anthropic: saída fora do schema ({exc.error_count()} erro(s))") from exc
        usage = data.get("usage") or {}
        call = LlmCall("anthropic", model, int(usage.get("input_tokens", 0)), int(usage.get("output_tokens", 0)), latency)
        return AuditResult(payload.verdicts, payload.overall, payload.comments_pt, call)
