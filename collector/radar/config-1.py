"""Loader estrito do config.yaml: campos desconhecidos são ERRO, nunca ignorados."""
from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

EDUCATION_ORDER = [
    "none", "primary", "lower_secondary", "high_school_complete",
    "vocational_post_secondary", "bachelor", "master", "doctorate",
]
CEFR_ORDER = {"A1": 1, "A2": 2, "B1": 3, "B2": 4, "C1": 5, "C2": 6, "native": 7}
CATEGORIES = [
    "gov_relocation_incentive", "job_with_package", "scholarship_or_vocational",
    "youth_program_covered", "other_cost_reduction", "unknown",
]
COMPONENTS = ["category_fit", "benefit_value", "eligibility", "citizenship_leverage", "evidence_quality", "deadline_window"]


class ConfigError(Exception):
    pass


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class Profile(Strict):
    label: str
    age_years: int = Field(ge=0, le=120)
    age_as_of: date
    nationalities: list[str]
    residence_country: str
    employed: bool
    education: Literal[tuple(EDUCATION_ORDER)]  # type: ignore[valid-type]
    languages: dict[str, str]
    work_experience_months: int | None = None

    @field_validator("languages")
    @classmethod
    def _langs(cls, v: dict[str, str]) -> dict[str, str]:
        for code, level in v.items():
            if level != "unknown" and level not in CEFR_ORDER:
                raise ValueError(f"nível de idioma inválido para {code}: {level}")
        return v


class FxTable(Strict):
    as_of: date
    approximate: bool
    rates: dict[str, float]


class ReferenceData(Strict):
    country_groups: dict[str, list[str]]
    fx_to_eur: FxTable


class H2Countries(Strict):
    as_of: date
    valid_through: date
    source_url: str
    countries: list[str]
    notes_pt: str = ""


class UsRules(Strict):
    assume_work_authorization_from_citizenship: bool
    h2_eligible_countries: H2Countries

    @field_validator("assume_work_authorization_from_citizenship")
    @classmethod
    def _never_assume(cls, v: bool) -> bool:
        if v:
            raise ValueError(
                "assume_work_authorization_from_citizenship deve ser false: cidadania italiana não concede autorização de trabalho nos EUA"
            )
        return v


class OfficialSources(Strict):
    domain_suffixes: list[str]
    domain_exact: list[str]
    domain_regex: list[str]

    @field_validator("domain_regex")
    @classmethod
    def _compile(cls, v: list[str]) -> list[str]:
        for pat in v:
            re.compile(pat)
        return v


class ProgramRule(Strict):
    id: str
    kind: Literal["residence_current_any", "age_max", "age_min", "nationality_any", "h2_nationality", "manual_confirmation"]
    values: list[str] = []
    number: float | None = None
    quote: str | None = None
    source_url: str | None = None
    reason_pt: str


class Program(Strict):
    label: str
    match_keywords: list[str]
    source_url: str
    checked_on: date
    rules: list[ProgramRule]


class Relevance(Strict):
    min_hits: int = Field(ge=1)
    keywords: dict[str, list[str]]


class ChangeDetection(Strict):
    volatile_patterns: list[str]

    @field_validator("volatile_patterns")
    @classmethod
    def _compile(cls, v: list[str]) -> list[str]:
        for pat in v:
            re.compile(pat)
        return v


class DeadlineScoring(Strict):
    comfortable_days: int
    urgent_factor: float
    unknown_factor: float


class ValueRef(Strict):
    low: float
    high: float


class Scoring(Strict):
    category_weights: dict[str, float]
    component_weights: dict[str, float]
    eligibility_factor: dict[str, float]
    value_reference_eur: ValueRef
    ineligible_cap: float
    citizenship_leverage_group: str
    deadline: DeadlineScoring

    @model_validator(mode="after")
    def _check(self) -> "Scoring":
        if set(self.component_weights) != set(COMPONENTS):
            raise ValueError(f"component_weights deve ter exatamente: {COMPONENTS}")
        if abs(sum(self.component_weights.values()) - 100) > 1e-6:
            raise ValueError("component_weights deve somar 100")
        missing = set(CATEGORIES) - set(self.category_weights)
        if missing:
            raise ValueError(f"category_weights sem: {sorted(missing)}")
        return self


class Urgent(Strict):
    within_days: int
    require_confirmed_deadline: bool
    require_open_status: bool


class Channel(Strict):
    enabled: bool


class AlertChannels(Strict):
    telegram: Channel
    smtp: Channel


class Alerts(Strict):
    min_score: float
    statuses: list[Literal["eligible", "needs_confirmation", "ineligible"]]
    require_official_source: bool
    require_verified_evidence: bool
    realert_on_change: bool
    urgent: Urgent
    channels: AlertChannels


class OpenAICfg(Strict):
    enabled: bool
    api_key_env: str
    model_env: str
    base_url: str
    timeout_s: float
    max_output_tokens: int
    max_retries: int


class AnthropicCfg(Strict):
    enabled: bool
    api_key_env: str
    model_env: str
    default_model: str
    base_url: str
    api_version: str
    timeout_s: float
    max_output_tokens: int
    max_retries: int


class AuditCfg(Strict):
    score_threshold: float
    on_change: bool
    on_conflict: bool


class BudgetCfg(Strict):
    daily_usd_cap: float
    daily_token_cap: int
    max_input_chars_per_document: int


class Price(Strict):
    input: float
    output: float


class LlmCfg(Strict):
    openai: OpenAICfg
    anthropic: AnthropicCfg
    audit: AuditCfg
    budget: BudgetCfg
    pricing_usd_per_mtok: dict[str, Price]


class SchedulerCfg(Strict):
    tick_seconds: int
    default_interval_minutes: int


class QueueCfg(Strict):
    max_attempts: int
    backoff_base_seconds: int
    backoff_max_seconds: int


class Tos(Strict):
    status: Literal["allowed", "pending_review", "forbidden"]
    reviewed_on: date | None = None
    note: str = ""
    evidence_url: str | None = None


class RateLimit(Strict):
    requests_per_minute: float = Field(gt=0)
    max_requests_per_run: int = Field(ge=1)


class SourceCfg(Strict):
    id: str
    name: str
    kind: Literal["seasonal_jobs_json", "html_page", "html_hub", "pdf_document", "fixture"]
    role: Literal["opportunity", "program_rules"] = "opportunity"
    origin: Literal["real", "fixture"]
    enabled: bool
    url: str
    country: str | None
    language: str | None
    interval_minutes: int = Field(ge=1)
    rate_limit: RateLimit
    tos: Tos
    options: dict[str, Any] = {}  # validado de forma estrita por cada conector


class Config(Strict):
    version: int
    profile: Profile
    reference_data: ReferenceData
    us: UsRules
    official_sources: OfficialSources
    programs: dict[str, Program]
    relevance: Relevance
    change_detection: ChangeDetection
    timezones: dict[str, str]
    scoring: Scoring
    alerts: Alerts
    llm: LlmCfg
    scheduler: SchedulerCfg
    queue: QueueCfg
    sources: list[SourceCfg]

    @field_validator("timezones")
    @classmethod
    def _tz(cls, v: dict[str, str]) -> dict[str, str]:
        for country, name in v.items():
            try:
                ZoneInfo(name)
            except ZoneInfoNotFoundError as exc:
                raise ValueError(f"fuso inválido para {country}: {name}") from exc
        return v

    @model_validator(mode="after")
    def _cross(self) -> "Config":
        ids = [s.id for s in self.sources]
        dup = sorted({i for i in ids if ids.count(i) > 1})
        if dup:
            raise ValueError(f"ids de fonte duplicados: {dup}")
        for s in self.sources:
            if (s.origin == "fixture") != (s.kind == "fixture"):
                raise ValueError(f"fonte {s.id}: origin=fixture se, e somente se, kind=fixture")
        return self

    # --- helpers -----------------------------------------------------------
    def expand_countries(self, values: list[str]) -> set[str]:
        out: set[str] = set()
        for v in values:
            key = v.upper()
            out |= set(self.reference_data.country_groups.get(key, [key]))
        return out

    def tz_for_country(self, country: str | None) -> str | None:
        return self.timezones.get((country or "").upper())

    def source(self, source_id: str) -> SourceCfg:
        for s in self.sources:
            if s.id == source_id:
                return s
        raise KeyError(source_id)


def _format_errors(exc: ValidationError) -> str:
    lines = []
    for err in exc.errors():
        loc = ".".join(str(p) for p in err["loc"])
        lines.append(f"{loc}: {err['msg']}")
    return "; ".join(lines)


def load_config(path: str | Path) -> Config:
    p = Path(path)
    if not p.is_file():
        raise ConfigError(f"config.yaml não encontrado: {p}")
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"YAML inválido em {p}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"{p} deve conter um mapeamento YAML na raiz")
    try:
        return Config.model_validate(data)
    except ValidationError as exc:
        raise ConfigError(f"config inválido: {_format_errors(exc)}") from exc
