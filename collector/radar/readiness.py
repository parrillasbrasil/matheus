"""Relatório de prontidão: o que falta para o radar operar de verdade (sem inventar nada: só olha config e ambiente)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Mapping

from .config import Config


@dataclass(frozen=True)
class Finding:
    level: str   # ok | warn | blocker
    text: str


def readiness(cfg: Config, env: Mapping[str, str], today: date) -> list[Finding]:
    out: list[Finding] = []
    add = lambda level, text: out.append(Finding(level, text))  # noqa: E731
    o, a = cfg.llm.openai, cfg.llm.anthropic

    if not env.get("DATABASE_URL"):
        add("blocker", "DATABASE_URL não definido: sem banco o radar não roda.")
    if not env.get(o.api_key_env) or not env.get(o.model_env):
        add("warn", f"{o.api_key_env}/{o.model_env} ausentes: a extração por IA fica pausada (documentos ficam pending_llm; feeds estruturados seguem).")
    if not env.get(a.api_key_env):
        add("warn", f"{a.api_key_env} ausente: auditoria independente (Claude) indisponível (audit_status=unavailable).")
    telegram = env.get("TELEGRAM_BOT_TOKEN") and env.get("TELEGRAM_CHAT_ID")
    smtp = env.get("SMTP_HOST") and env.get("SMTP_FROM") and env.get("SMTP_TO")
    if not telegram and not smtp:
        add("warn", "Nenhum canal de alerta configurado (Telegram ou SMTP): alertas ficam pendentes até haver credenciais.")
    if not env.get("RADAR_CONTACT"):
        add("warn", "RADAR_CONTACT ausente: o User-Agent do coletor deve informar um contato (boa prática de coleta).")

    for s in cfg.sources:
        if s.origin == "real" and s.enabled and s.tos.status != "allowed":
            add("warn", f"Fonte {s.id} habilitada, mas ToS/robots = {s.tos.status}: NÃO executa até a revisão (ver README).")

    h2 = cfg.us.h2_eligible_countries
    if h2.valid_through < today:
        add("warn", f"Lista de países elegíveis ao H-2 vencida em {h2.valid_through}: vagas H-2 ficam 'precisa de confirmação' até atualizar ({h2.source_url}).")
    if (today - cfg.reference_data.fx_to_eur.as_of).days > 90:
        add("warn", f"Tabela de câmbio de {cfg.reference_data.fx_to_eur.as_of} tem mais de 90 dias: valores em EUR podem estar defasados.")

    enabled_real = [s for s in cfg.sources if s.origin == "real" and s.enabled and s.tos.status == "allowed"]
    add("ok", f"{len(enabled_real)} fonte(s) real(is) pronta(s) para executar de {sum(1 for s in cfg.sources if s.origin == 'real')} configurada(s).")
    return out
