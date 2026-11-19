"""Schema da extração (saída da IA ou de feed estruturado). Campos desconhecidos são explícitos."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from .config import CATEGORIES

BenefitType = Literal[
    "relocation_grant", "salary_job", "housing_support", "travel_covered", "scholarship",
    "vocational_training", "paid_program", "youth_program_covered", "other",
]
Category = Literal[tuple(CATEGORIES)]  # type: ignore[valid-type]
RequirementKind = Literal[
    "age_min", "age_max", "nationality_any", "residence_current_any", "residence_after_move",
    "education_min", "language_min", "experience_min_months", "work_authorization", "other",
]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class MoneyValue(Strict):
    amount: float | None
    currency: str | None
    gross_net: Literal["gross", "net", "unknown"]
    period: Literal["one_time", "monthly", "yearly", "hourly", "total", "unknown"]
    quote: str | None


class Deadline(Strict):
    iso: str | None          # data ou data-hora ISO-8601 copiada da fonte; null se ausente
    timezone: str | None     # fuso IANA SOMENTE se a fonte o declara; senão null
    quote: str | None


class TextItem(Strict):
    text_pt: str
    quote: str | None


class LlmRequirement(Strict):
    id: str
    kind: RequirementKind
    values: list[str]                    # países/grupos (ex.: "EU", "IT"), idioma ("en"), etc.
    number: float | None                 # idade, meses de experiência
    level: str | None                    # nível de escolaridade ou CEFR
    work_authorization: Literal["provided_by_employer", "required_from_candidate", "not_required", "unknown"]
    mandatory: bool
    text_pt: str
    quote: str | None


class Requirement(LlmRequirement):
    verified: bool = False  # preenchido pelo radar (citação confere literalmente), NUNCA pela IA


class LlmExtraction(Strict):
    is_opportunity: bool
    program_hint: str | None
    title_original: str | None
    title_pt: str | None
    issuer: str | None
    country: str | None                  # ISO-3166 alpha-2
    city_or_region: str | None
    benefit_types: list[BenefitType]
    category: Category
    benefit_summary_pt: str | None
    value: MoneyValue
    deadline: Deadline
    status: Literal["open", "closed", "unknown"]
    status_quote: str | None
    aid_type: Literal["grant", "salary", "in_kind", "reimbursement", "loan", "tax_benefit", "other", "unknown"]
    requirements: list[LlmRequirement]
    obligations: list[TextItem]
    not_covered_expenses: list[TextItem]


class Extraction(LlmExtraction):
    requirements: list[Requirement]  # type: ignore[assignment]


def _strictify(node: Any) -> Any:
    """Converte o JSON Schema do pydantic para o modo estrito (todos os campos obrigatórios)."""
    if isinstance(node, dict):
        out = {k: _strictify(v) for k, v in node.items() if k not in ("default", "title")}
        if out.get("type") == "object" and "properties" in out:
            out["required"] = list(out["properties"].keys())
            out["additionalProperties"] = False
        return out
    if isinstance(node, list):
        return [_strictify(v) for v in node]
    return node


def llm_json_schema() -> dict[str, Any]:
    return _strictify(LlmExtraction.model_json_schema())


def empty_extraction() -> Extraction:
    return Extraction(
        is_opportunity=False, program_hint=None, title_original=None, title_pt=None, issuer=None,
        country=None, city_or_region=None, benefit_types=[], category="unknown", benefit_summary_pt=None,
        value=MoneyValue(amount=None, currency=None, gross_net="unknown", period="unknown", quote=None),
        deadline=Deadline(iso=None, timezone=None, quote=None), status="unknown", status_quote=None,
        aid_type="unknown", requirements=[], obligations=[], not_covered_expenses=[],
    )
