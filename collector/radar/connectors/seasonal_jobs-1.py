"""DOL SeasonalJobs (H-2A/H-2B): feeds JSON oficiais, publicados para indexação por terceiros.

ATENÇÃO: os nomes de campo abaixo são SUPOSIÇÕES. O dicionário oficial (XLSX) não pôde ser lido
no ambiente de construção. Na primeira execução real, `source_runs.stats.unmapped_keys` lista as chaves
que o mapeamento não reconheceu; ajuste `options.field_map` no config.yaml conforme o dicionário oficial.
"""
from __future__ import annotations

import io
import json
import re
import zipfile
from datetime import datetime, timedelta
from typing import Any, Iterator, Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict

from ..http import FetchError, PoliteClient
from ..schema import Deadline, Extraction, MoneyValue, Requirement
from ..textutil import normalize_text, parse_amount
from .base import Connector, RawItem

DEFAULT_FIELD_MAP: dict[str, list[str]] = {
    "case_number": ["case_number", "caseNumber", "job_order_number", "joNumber", "caseId"],
    "job_title": ["job_title", "jobTitle", "title", "occupation_title"],
    "employer": ["employer_business_name", "employerBusinessName", "employer_name", "employer"],
    "city": ["worksite_city", "city", "job_city"],
    "state": ["worksite_state", "state", "job_state"],
    "wage_amount": ["basic_rate_from", "wage_offer_from", "wage", "basic_wage", "pay_rate"],
    "wage_unit": ["pay_unit", "wage_unit", "basic_unit_of_pay"],
    "housing": ["housing_info", "housing_provided", "housing", "housing_description"],
    "transportation": ["transportation_info", "transportation_reimbursement", "transportation", "transportation_subsistence"],
    "start_date": ["begin_date", "start_date", "job_begin_date", "tempneed_start"],
    "end_date": ["end_date", "job_end_date", "tempneed_end"],
    "positions": ["total_positions", "workers_needed", "nbr_workers_requested"],
    "experience_months": ["experience_months", "months_experience", "experience_required_months"],
    "education": ["education_level", "min_education", "education"],
    "description": ["job_duties", "description", "job_description"],
}
_NEGATIVE = {"", "no", "n", "false", "none", "0", "n/a", "na"}
_EDU = [("bachelor", "bachelor"), ("master", "master"), ("associate", "vocational_post_secondary"),
        ("high school", "high_school_complete"), ("ged", "high_school_complete"), ("secondary", "high_school_complete")]
_UNIT = {"hour": "hourly", "hourly": "hourly", "month": "monthly", "monthly": "monthly", "year": "yearly", "yearly": "yearly"}


class SeasonalOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")
    program: Literal["h2a", "h2b"]
    lookback_days: int = 2
    field_map: dict[str, list[str]] = {}
    job_url_template: str = "https://seasonaljobs.dol.gov/jobs/{case_number}"  # REVISAR contra o site real


def _norm(key: str) -> str:
    return re.sub(r"[^a-z0-9]", "", key.lower())


def _flatten(obj: Any, out: dict[str, tuple[str, Any]]) -> None:
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, (dict, list)):
                _flatten(v, out)
            else:
                out.setdefault(_norm(str(k)), (str(k), v))
    elif isinstance(obj, list):
        for v in obj:
            _flatten(v, out)


def _records(payload: Any) -> list[dict]:
    if isinstance(payload, list):
        return [r for r in payload if isinstance(r, dict)]
    if isinstance(payload, dict):
        for key in ("data", "results", "jobs", "records", "items"):
            if isinstance(payload.get(key), list):
                return [r for r in payload[key] if isinstance(r, dict)]
        return [payload]
    return []


def _parse_payload(content: bytes) -> list[dict]:
    if zipfile.is_zipfile(io.BytesIO(content)):
        out: list[dict] = []
        with zipfile.ZipFile(io.BytesIO(content)) as z:
            for name in sorted(z.namelist()):
                if name.lower().endswith(".json"):
                    out += _records(json.loads(z.read(name).decode("utf-8")))
        return out
    return _records(json.loads(content.decode("utf-8")))


class SeasonalJobsConnector(Connector):
    options_model = SeasonalOptions

    def _field(self, flat: dict[str, tuple[str, Any]], name: str, used: set[str]) -> str | None:
        candidates = self.options.field_map.get(name) or DEFAULT_FIELD_MAP[name]
        for cand in candidates:
            hit = flat.get(_norm(cand))
            if hit and hit[1] not in (None, ""):
                used.add(_norm(cand))
                return str(hit[1]).strip()
        return None

    def fetch(self, client: PoliteClient, now: datetime, stats: dict) -> Iterator[RawItem]:
        stats.setdefault("days_without_data", 0)
        stats.setdefault("skipped_no_id", 0)
        unmapped: dict[str, None] = {}
        today = now.astimezone(ZoneInfo("America/New_York")).date()
        seen_ids: set[str] = set()
        for k in range(self.options.lookback_days):
            day = (today - timedelta(days=k)).isoformat()
            try:
                resp = client.get(self.source.url.format(date=day))
            except FetchError as exc:
                if exc.status == 404:
                    stats["days_without_data"] += 1
                    continue
                raise
            try:
                records = _parse_payload(resp.content)
            except (ValueError, zipfile.BadZipFile) as exc:
                raise FetchError(f"feed ilegível ({exc.__class__.__name__})") from exc
            for rec in records:
                flat: dict[str, tuple[str, Any]] = {}
                _flatten(rec, flat)
                used: set[str] = set()
                values = {name: self._field(flat, name, used) for name in DEFAULT_FIELD_MAP}
                for nk, (orig, _) in flat.items():
                    if nk not in used:
                        unmapped.setdefault(orig, None)
                case = values["case_number"]
                if not case:
                    stats["skipped_no_id"] += 1
                    continue
                if case in seen_ids:
                    continue
                seen_ids.add(case)
                yield self._to_item(case, values)
        stats["unmapped_keys"] = sorted(unmapped)

    def _to_item(self, case: str, v: dict[str, str | None]) -> RawItem:
        prog = "H-2B" if self.options.program == "h2b" else "H-2A"
        lines = [f"Program: {prog} temporary labor certification (DOL SeasonalJobs feed)", f"Case number: {case}"]
        if v["job_title"]:
            lines.append(f"Job title: {v['job_title']}")
        if v["employer"]:
            lines.append(f"Employer: {v['employer']}")
        place = ", ".join(x for x in (v["city"], v["state"]) if x)
        if place:
            lines.append(f"Worksite: {place}")

        value = MoneyValue(amount=None, currency=None, gross_net="unknown", period="unknown", quote=None)
        if v["wage_amount"]:
            unit = (v["wage_unit"] or "").strip()
            wage_line = f"Wage offered: {v['wage_amount']} USD" + (f" per {unit}" if unit else "")
            lines.append(wage_line)
            amount = parse_amount(v["wage_amount"])
            if amount is not None:
                value = MoneyValue(amount=float(amount), currency="USD", gross_net="gross",
                                   period=_UNIT.get(unit.lower(), "unknown"), quote=wage_line)

        benefits = ["salary_job"]
        if v["housing"] and v["housing"].lower() not in _NEGATIVE:
            lines.append(f"Housing: {v['housing']}")
            benefits.append("housing_support")
        if v["transportation"] and v["transportation"].lower() not in _NEGATIVE:
            lines.append(f"Transportation: {v['transportation']}")
            benefits.append("travel_covered")
        if v["start_date"] or v["end_date"]:
            lines.append(f"Employment period: {v['start_date'] or '?'} to {v['end_date'] or '?'}")
        if v["positions"]:
            lines.append(f"Positions: {v['positions']}")

        reqs = [Requirement(
            id="feed-work-authorization", kind="work_authorization", values=["US"], number=None, level=None,
            work_authorization="provided_by_employer", mandatory=True,
            text_pt=f"Vaga certificada pelo programa {prog}: o empregador patrocina o visto de trabalho.",
            quote=f"Program: {prog}", verified=False)]
        if v["education"]:
            lines.append(f"Minimum education: {v['education']}")
            low = v["education"].lower()
            level = next((lvl for key, lvl in _EDU if key in low), None)
            if level:
                reqs.append(Requirement(id="feed-education", kind="education_min", values=[], number=None, level=level,
                                        work_authorization="unknown", mandatory=True,
                                        text_pt=f"Escolaridade mínima: {v['education']}.",
                                        quote=f"Minimum education: {v['education']}", verified=False))
            elif low not in _NEGATIVE:
                reqs.append(Requirement(id="feed-education", kind="other", values=[], number=None, level=None,
                                        work_authorization="unknown", mandatory=True,
                                        text_pt=f"Escolaridade exigida (não mapeada): {v['education']}.",
                                        quote=f"Minimum education: {v['education']}", verified=False))
        if v["experience_months"]:
            lines.append(f"Experience (months): {v['experience_months']}")
            months = parse_amount(v["experience_months"])
            if months and months > 0:
                reqs.append(Requirement(id="feed-experience", kind="experience_min_months", values=[], number=float(months),
                                        level=None, work_authorization="unknown", mandatory=True,
                                        text_pt=f"Experiência mínima: {int(months)} meses.",
                                        quote=f"Experience (months): {v['experience_months']}", verified=False))
        if v["description"]:
            lines.append(f"Duties: {v['description']}")

        summary = f"Vaga temporária {prog} nos EUA: {v['job_title'] or 'cargo não informado'}" + (f" em {place}" if place else "")
        ex = Extraction(
            is_opportunity=True, program_hint=prog, title_original=v["job_title"], title_pt=None, issuer=v["employer"],
            country="US", city_or_region=place or None, benefit_types=benefits, category="job_with_package",
            benefit_summary_pt=summary + ".", value=value, deadline=Deadline(iso=None, timezone=None, quote=None),
            status="unknown", status_quote=None, aid_type="salary", requirements=reqs, obligations=[], not_covered_expenses=[],
        )
        url = self.options.job_url_template.format(case_number=case)
        return RawItem(url=url, title=v["job_title"], text=normalize_text("\n\n".join(lines)), content_type="json",
                       external_id=case, structured=ex, metadata={"program": prog, "feed": "dol-seasonaljobs"})
