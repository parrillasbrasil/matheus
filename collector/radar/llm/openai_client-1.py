"""OpenAI: extração estruturada (Chat Completions com JSON Schema estrito). A chave vem só do ambiente."""
from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Callable, Mapping

import httpx
from pydantic import ValidationError

from ..config import Config
from ..schema import Extraction, LlmExtraction, Requirement, llm_json_schema
from .base import LlmCall, LlmError, LlmNotConfigured, post_json
from .prompts import build_extraction_messages


@dataclass(frozen=True)
class ExtractionResult:
    extraction: Extraction
    call: LlmCall


def to_extraction(llm: LlmExtraction) -> Extraction:
    data = llm.model_dump()
    data["requirements"] = [Requirement(**r, verified=False) for r in data["requirements"]]
    return Extraction(**data)


class OpenAIExtractor:
    def __init__(self, cfg: Config, env: Mapping[str, str] | None = None, transport: httpx.BaseTransport | None = None,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self.cfg = cfg.llm.openai
        self._env = os.environ if env is None else env
        self._transport, self._sleep = transport, sleep

    def model(self) -> str:
        """Modelo vindo do ambiente (sem default de propósito); levanta LlmNotConfigured se faltar chave/modelo."""
        if not self.cfg.enabled:
            raise LlmNotConfigured("OpenAI desabilitada no config.yaml")
        if not self._env.get(self.cfg.api_key_env):
            raise LlmNotConfigured(f"{self.cfg.api_key_env} não definida")
        model = self._env.get(self.cfg.model_env)
        if not model:
            raise LlmNotConfigured(f"{self.cfg.model_env} não definida (escolha o modelo no ambiente)")
        return model

    def extract(self, url: str, title: str | None, text: str) -> ExtractionResult:
        model = self.model()
        system, user = build_extraction_messages(url, title, text)
        body = {
            "model": model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "response_format": {"type": "json_schema", "json_schema": {"name": "opportunity_extraction", "strict": True, "schema": llm_json_schema()}},
            "max_completion_tokens": self.cfg.max_output_tokens,
        }
        data, latency = post_json(
            provider="openai", url=self.cfg.base_url.rstrip("/") + "/chat/completions",
            headers={"Authorization": f"Bearer {self._env[self.cfg.api_key_env]}", "Content-Type": "application/json"},
            body=body, timeout_s=self.cfg.timeout_s, max_retries=self.cfg.max_retries, transport=self._transport, sleep=self._sleep,
        )
        try:
            message = data["choices"][0]["message"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LlmError("openai: resposta sem choices") from exc
        if message.get("refusal"):
            raise LlmError("openai: o modelo recusou a requisição")
        try:
            llm = LlmExtraction.model_validate_json(message.get("content") or "")
        except ValidationError as exc:
            raise LlmError(f"openai: saída fora do schema ({exc.error_count()} erro(s))") from exc
        usage = data.get("usage") or {}
        call = LlmCall("openai", model, int(usage.get("prompt_tokens", 0)), int(usage.get("completion_tokens", 0)), latency)
        return ExtractionResult(to_extraction(llm), call)
