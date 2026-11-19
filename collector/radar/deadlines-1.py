"""Prazos com fuso horário: nunca confirma prazo sem citação verificada e fuso explícito."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dateutil import parser as dtparser

from .config import Config
from .schema import Deadline


@dataclass(frozen=True)
class ResolvedDeadline:
    utc: datetime | None
    tz: str | None
    tz_assumed: bool
    confirmed: bool


UNKNOWN = ResolvedDeadline(utc=None, tz=None, tz_assumed=False, confirmed=False)


def _zone(name: str | None) -> ZoneInfo | None:
    if not name:
        return None
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return None


def resolve_deadline(cfg: Config, deadline: Deadline, country: str | None, quote_verified: bool, now: datetime) -> ResolvedDeadline:
    iso = (deadline.iso or "").strip()
    if not iso:
        return UNKNOWN
    try:
        parsed = dtparser.isoparse(iso)
    except (ValueError, OverflowError):
        return UNKNOWN
    date_only = len(iso) <= 10
    if date_only:
        parsed = datetime.combine(parsed.date(), time(23, 59, 59))

    explicit_zone = _zone(deadline.timezone)
    if parsed.tzinfo is not None:                         # offset explícito no próprio valor
        utc = parsed.astimezone(timezone.utc)
        tz_name = deadline.timezone if explicit_zone else str(parsed.tzinfo)
        assumed = False
    elif explicit_zone is not None:
        utc = parsed.replace(tzinfo=explicit_zone).astimezone(timezone.utc)
        tz_name, assumed = deadline.timezone, False
    else:
        tz_name = cfg.tz_for_country(country) or "UTC"
        utc = parsed.replace(tzinfo=_zone(tz_name) or timezone.utc).astimezone(timezone.utc)
        assumed = True
    return ResolvedDeadline(utc=utc, tz=tz_name, tz_assumed=assumed, confirmed=bool(quote_verified and not assumed))


def effective_status(declared: str, resolved: ResolvedDeadline, status_verified: bool, now: datetime) -> str:
    """Prazo vencido vence qualquer 'aberto' declarado; 'aberto' exige citação verificada."""
    if resolved.utc is not None and resolved.utc < now:
        return "closed"
    if declared == "closed" and status_verified:
        return "closed"
    if declared == "open" and status_verified:
        return "open"
    return "unknown"


def days_left(resolved: ResolvedDeadline, now: datetime) -> float | None:
    if resolved.utc is None:
        return None
    return (resolved.utc - now) / timedelta(days=1)
