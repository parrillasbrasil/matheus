"""Política de alertas, mensagens e canais (Telegram, SMTP).

O radar NUNCA envia candidaturas; alerta só informa, com fonte oficial e evidência.
"""
from __future__ import annotations

import smtplib
from dataclasses import dataclass
from datetime import datetime
from email.message import EmailMessage
from typing import Any, Callable, Mapping
from zoneinfo import ZoneInfo

import httpx

from .config import Config
from .deadlines import ResolvedDeadline, days_left
from .logs import quiet_http_loggers, redact


# --- política -----------------------------------------------------------------------------------------
@dataclass(frozen=True)
class AlertInput:
    status: str                   # status EFETIVO (open | closed | unknown)
    eligibility_status: str       # eligible | needs_confirmation | ineligible
    score: float
    is_official: bool
    verified_evidence: int
    deadline: ResolvedDeadline


@dataclass(frozen=True)
class AlertDecision:
    send: bool
    kind: str | None
    reason: str


_ELIG_LABEL = {"eligible": "elegível", "needs_confirmation": "a confirmar", "ineligible": "inelegível"}


def decide_alert(
    cfg: Config, a: AlertInput, *, change: str, meaningful_change: bool, ever_alerted: bool,
    now: datetime, urgent_sent: bool = False,
) -> AlertDecision:
    p = cfg.alerts
    no = lambda reason: AlertDecision(False, None, reason)  # noqa: E731

    left = days_left(a.deadline, now)
    if a.status == "closed":
        return no("oportunidade encerrada: não gera alerta de candidatura aberta")
    if left is not None and left < 0:
        return no("prazo vencido: não gera alerta de candidatura aberta")
    if a.eligibility_status not in p.statuses:
        return no(f"elegibilidade {_ELIG_LABEL.get(a.eligibility_status, a.eligibility_status)} não gera alerta")
    if a.score < p.min_score:
        return no(f"pontuação {a.score:.0f} abaixo do mínimo {p.min_score:.0f}")
    if p.require_official_source and not a.is_official:
        return no("fonte não oficial")
    if p.require_verified_evidence and a.verified_evidence <= 0:
        return no("sem evidência verificada")

    base: str | None = None
    if change == "new":
        base = "new"
    elif change == "changed" and meaningful_change:
        if not ever_alerted:
            base = "new"
        elif p.realert_on_change:
            base = "changed"

    urgent = (
        left is not None and left <= p.urgent.within_days
        and (not p.urgent.require_confirmed_deadline or a.deadline.confirmed)
        and (not p.urgent.require_open_status or a.status == "open")
    )
    if urgent and not urgent_sent:
        return AlertDecision(True, "urgent", "prazo confirmado próximo e candidatura aberta")
    if base:
        return AlertDecision(True, base, "oportunidade nova" if base == "new" else "mudança relevante na fonte")
    return no("sem mudança relevante")


# --- mensagem -------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Message:
    subject: str
    text: str


_PERIOD = {"one_time": "pagamento único", "monthly": "por mês", "yearly": "por ano", "hourly": "por hora", "total": "total", "unknown": ""}
_STATUS = {"open": "aberta", "closed": "encerrada", "unknown": "desconhecida (confirme na fonte)"}
_RELOCATION = {
    "after_award": "exige mudança de residência após a concessão do benefício",
    "before_apply": "exige já residir/ter mudado antes de se candidatar",
}
_KIND = {"new": "Nova oportunidade", "changed": "Oportunidade atualizada", "urgent": "URGENTE — prazo próximo"}


def _money(amount: Any, currency: str | None, period: str | None) -> str:
    if amount is None:
        return "desconhecido"
    amt = float(amount)
    s = f"{amt:,.0f}" if amt == int(amt) else f"{amt:,.2f}"
    s = s.replace(",", "X").replace(".", ",").replace("X", ".")
    return " ".join(x for x in (s, currency or "", f"({_PERIOD.get(period or 'unknown', '')})" if _PERIOD.get(period or "unknown") else "") if x)


def _deadline(opp: Mapping[str, Any]) -> str:
    at = opp.get("deadline_at")
    if at is None:
        return "desconhecido"
    tz = opp.get("deadline_tz") or "UTC"
    try:
        local = at.astimezone(ZoneInfo(tz))
    except Exception:  # fuso inválido: mostra em UTC
        local, tz = at, "UTC"
    note = "confirmado na fonte" if opp.get("deadline_confirmed") else "prazo/fuso NÃO confirmado — verifique na fonte"
    return f"{local:%d/%m/%Y %H:%M} ({tz}) — {note}"


def render_message(opp: Mapping[str, Any], kind: str) -> Message:
    origin = opp.get("data_origin", "unknown")
    prefix = {"fixture": "[TESTE] ", "unknown": "[ORIGEM DESCONHECIDA] "}.get(origin, "")
    title = opp.get("title_pt") or opp.get("title_original") or "(sem título)"
    lines = [title]
    if opp.get("title_original") and opp.get("title_original") != title:
        lines.append(f"Título original: {opp['title_original']}")
    lines.append(f"Emissor: {opp.get('issuer') or 'desconhecido'} · País: {opp.get('country') or 'desconhecido'}")
    lines.append(f"Benefício (valor): {_money(opp.get('value_amount'), opp.get('value_currency'), opp.get('value_period'))}")
    lines.append(f"Prazo: {_deadline(opp)}")
    lines.append(f"Situação: {_STATUS.get(opp.get('status') or 'unknown', 'desconhecida')}")
    score = opp.get("current_score")
    lines.append(f"Elegibilidade: {_ELIG_LABEL.get(opp.get('eligibility_status') or '', 'desconhecida')}"
                 + (f" · pontuação {float(score):.0f}/100" if score is not None else ""))
    if opp.get("eligibility_summary"):
        lines.append(opp["eligibility_summary"])
    if opp.get("relocation_condition") in _RELOCATION:
        lines.append(f"Atenção: {_RELOCATION[opp['relocation_condition']]}.")
    evidence = opp.get("evidence") or []
    if evidence:
        lines.append("Evidências (trechos literais da fonte oficial):")
        for ev in evidence[:3]:
            q = str(ev.get("quote", ""))
            lines.append(f"  • [{ev.get('field', '?')}] “{q[:280]}{'…' if len(q) > 280 else ''}”")
    lines.append(f"Fonte oficial: {opp.get('canonical_url')}")
    if origin == "fixture":
        lines.append("ATENÇÃO: dado de TESTE (fixture); não é resultado de pesquisa real.")
    lines.append("Nenhuma candidatura foi enviada: o radar nunca se candidata sozinho. Confirme tudo na fonte oficial antes de agir.")
    return Message(subject=f"{prefix}{_KIND.get(kind, kind)}: {title}", text="\n".join(lines))


# --- canais --------------------------------------------------------------------------------------------
class AlertSendError(Exception):
    pass


class TelegramSender:
    name = "telegram"

    def __init__(self, token: str, chat_id: str, transport: httpx.BaseTransport | None = None, timeout_s: float = 15.0) -> None:
        self._token, self._chat_id, self._transport, self._timeout = token, chat_id, transport, timeout_s
        quiet_http_loggers()  # a URL da API carrega o token: nunca pode ir para log

    def send(self, message: Message) -> None:
        body = {"chat_id": self._chat_id, "text": f"{message.subject}\n\n{message.text}"[:4000], "disable_web_page_preview": True}
        try:
            with httpx.Client(transport=self._transport, timeout=self._timeout) as client:
                resp = client.post(f"https://api.telegram.org/bot{self._token}/sendMessage", json=body)
        except httpx.HTTPError as exc:
            raise AlertSendError(f"telegram: falha de rede ({exc.__class__.__name__})") from None
        try:
            data = resp.json()
        except ValueError:
            data = {}
        if resp.status_code != 200 or not data.get("ok"):
            raise AlertSendError(redact(f"telegram: HTTP {resp.status_code}: {data.get('description', 'sem detalhe')}").replace(self._token, "[REDACTED]"))


def _default_smtp(host: str, port: int):
    return (smtplib.SMTP_SSL if port == 465 else smtplib.SMTP)(host, port, timeout=30)


class SmtpSender:
    name = "smtp"

    def __init__(self, host: str, port: int, user: str | None, password: str | None, sender: str, recipient: str,
                 smtp_factory: Callable[[str, int], Any] = _default_smtp) -> None:
        self._host, self._port, self._user, self._password = host, port, user, password
        self._sender, self._recipient, self._factory = sender, recipient, smtp_factory

    def send(self, message: Message) -> None:
        msg = EmailMessage()
        msg["Subject"], msg["From"], msg["To"] = message.subject, self._sender, self._recipient
        msg.set_content(message.text)
        try:
            with self._factory(self._host, self._port) as smtp:
                if self._port != 465:
                    smtp.starttls()
                if self._user:
                    smtp.login(self._user, self._password or "")
                smtp.send_message(msg)
        except (smtplib.SMTPException, OSError) as exc:
            raise AlertSendError(f"smtp: {exc.__class__.__name__}") from None


def channels_from_env(cfg: Config, env: Mapping[str, str]) -> list[TelegramSender | SmtpSender]:
    out: list[TelegramSender | SmtpSender] = []
    ch = cfg.alerts.channels
    if ch.telegram.enabled and env.get("TELEGRAM_BOT_TOKEN") and env.get("TELEGRAM_CHAT_ID"):
        out.append(TelegramSender(env["TELEGRAM_BOT_TOKEN"], env["TELEGRAM_CHAT_ID"]))
    if ch.smtp.enabled and env.get("SMTP_HOST") and env.get("SMTP_FROM") and env.get("SMTP_TO"):
        out.append(SmtpSender(env["SMTP_HOST"], int(env.get("SMTP_PORT") or 587), env.get("SMTP_USER") or None,
                              env.get("SMTP_PASSWORD") or None, env["SMTP_FROM"], env["SMTP_TO"]))
    return out
