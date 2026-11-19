"""Motor DETERMINÍSTICO de elegibilidade. A IA extrai fatos; só estas regras decidem.

Princípios:
* cidadania != residência != autorização de trabalho;
* requisito sem citação verificada nunca pode aprovar (no máximo exige confirmação);
* ausência de requisitos extraídos NÃO significa elegível;
* benefício condicionado a mudança posterior é sinalizado em `relocation_condition`.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import datetime

from .config import CEFR_ORDER, EDUCATION_ORDER, Config
from .schema import Extraction, Requirement


@dataclass
class Check:
    requirement_id: str
    kind: str
    result: str                       # pass | fail | unknown | info
    reason_pt: str
    quote: str | None = None
    source_url: str | None = None
    origin: str = "extracted"         # extracted | program_rule | deterministic
    fixable_by_relocation: bool = False


@dataclass
class EligibilityResult:
    status: str                       # eligible | needs_confirmation | ineligible
    relocation_condition: str         # none | before_apply | after_award
    checks: list[Check] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    next_steps: list[str] = field(default_factory=list)
    summary_pt: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


US_NOTE = "Cidadania italiana não concede autorização de trabalho nos EUA; é preciso patrocínio/visto adequado."


def current_age(cfg: Config, now: datetime) -> int:
    elapsed = max(0, (now.date() - cfg.profile.age_as_of).days // 365)
    return cfg.profile.age_years + elapsed


def _kw_match(keyword: str, haystack: str) -> bool:
    return re.search(rf"(?<![a-z0-9]){re.escape(keyword.lower())}(?![a-z0-9])", haystack) is not None


def detect_programs(cfg: Config, ex: Extraction, text: str) -> list[str]:
    hay = " ".join(filter(None, [ex.program_hint, ex.title_original, ex.title_pt, text[:6000]])).lower()
    return [pid for pid, prog in cfg.programs.items() if any(_kw_match(k, hay) for k in prog.match_keywords)]


def _is_employment(ex: Extraction) -> bool:
    return "salary_job" in ex.benefit_types or ex.category == "job_with_package"


def _lang_rank(level: str | None) -> int | None:
    return None if level in (None, "unknown") else CEFR_ORDER.get(level)


def _eval_requirement(cfg: Config, r: Requirement, age: int, url: str) -> Check:
    prof = cfg.profile

    def mk(result: str, reason: str, fix: bool = False) -> Check:
        return Check(r.id, r.kind, result, reason, quote=r.quote, source_url=url, fixable_by_relocation=fix)

    if r.kind != "residence_after_move" and not r.verified and r.mandatory:
        return mk("unknown", f"Requisito não verificado: a citação não confere com o texto da fonte ({r.text_pt}).")

    k = r.kind
    res: Check
    if k == "age_min" and r.number is not None:
        ok = age >= r.number
        res = mk("pass" if ok else "fail", f"Idade mínima {int(r.number)}; candidato tem {age}.")
    elif k == "age_max" and r.number is not None:
        ok = age <= r.number
        res = mk("pass" if ok else "fail", f"Idade máxima {int(r.number)}; candidato tem {age}.")
    elif k == "nationality_any" and r.values:
        allowed = cfg.expand_countries(r.values)
        have = sorted(set(prof.nationalities) & allowed)
        res = mk("pass" if have else "fail", f"Nacionalidade exigida {r.values}; candidato tem {prof.nationalities}" + (f" (atende por {have})." if have else "."))
    elif k == "residence_current_any" and r.values:
        allowed = cfg.expand_countries(r.values)
        ok = prof.residence_country in allowed
        res = mk("pass" if ok else "fail",
                 f"Residência atual exigida em {r.values}; candidato reside em {prof.residence_country}. Cidadania não substitui residência.",
                 fix=not ok)
    elif k == "residence_after_move":
        res = mk("pass", f"Exige mudar a residência depois ({r.text_pt}); o candidato pode cumprir ao se mudar.")
    elif k == "education_min" and r.level in EDUCATION_ORDER:
        ok = EDUCATION_ORDER.index(prof.education) >= EDUCATION_ORDER.index(r.level)
        res = mk("pass" if ok else "fail", f"Escolaridade mínima {r.level}; candidato tem {prof.education}.")
    elif k == "language_min" and r.values and r.level in CEFR_ORDER:
        have = _lang_rank(prof.languages.get(r.values[0].lower()))
        need = CEFR_ORDER[r.level]
        if have is None:
            res = mk("unknown", f"Idioma {r.values[0]} nível {r.level}: o perfil não informa o nível do candidato.")
        else:
            res = mk("pass" if have >= need else "fail", f"Idioma {r.values[0]} exigido {r.level}; candidato tem {prof.languages.get(r.values[0].lower())}.")
    elif k == "experience_min_months" and r.number is not None:
        if prof.work_experience_months is None:
            res = mk("unknown", f"Exige {int(r.number)} meses de experiência; o perfil não informa experiência.")
        else:
            ok = prof.work_experience_months >= r.number
            res = mk("pass" if ok else "fail", f"Experiência exigida {int(r.number)} meses; candidato tem {prof.work_experience_months}.")
    elif k == "work_authorization":
        wa = r.work_authorization
        if wa == "required_from_candidate":
            res = mk("fail", "Exige que o candidato já tenha autorização de trabalho; " + US_NOTE)
        elif wa in ("provided_by_employer", "not_required"):
            res = mk("pass", "A fonte indica que a autorização/patrocínio é fornecida pelo empregador ou dispensada.")
        else:
            res = mk("unknown", "A fonte não esclarece quem fornece a autorização de trabalho.")
    else:
        res = mk("unknown", f"Requisito não verificável automaticamente: {r.text_pt}")

    if not r.mandatory and res.result != "pass":
        res.result = "info"
        res.reason_pt = "(opcional) " + res.reason_pt
    return res


def _eval_program_rule(cfg: Config, program_id: str, rule, age: int, now: datetime) -> Check:
    prog = cfg.programs[program_id]
    url = rule.source_url or prog.source_url
    prof = cfg.profile

    def mk(result: str, fix: bool = False, reason: str | None = None) -> Check:
        return Check(rule.id, rule.kind, result, reason or rule.reason_pt, quote=rule.quote, source_url=url,
                     origin="program_rule", fixable_by_relocation=fix)

    if rule.kind == "residence_current_any":
        ok = prof.residence_country in cfg.expand_countries(rule.values)
        return mk("pass" if ok else "fail", fix=not ok)
    if rule.kind == "age_max" and rule.number is not None:
        return mk("pass" if age <= rule.number else "fail")
    if rule.kind == "age_min" and rule.number is not None:
        return mk("pass" if age >= rule.number else "fail")
    if rule.kind == "nationality_any":
        return mk("pass" if set(prof.nationalities) & cfg.expand_countries(rule.values) else "fail")
    if rule.kind == "h2_nationality":
        lst = cfg.us.h2_eligible_countries
        c = Check(rule.id, "h2_nationality", "unknown", "", source_url=lst.source_url, origin="program_rule")
        if now.date() > lst.valid_through:
            c.reason_pt = (f"A lista de nacionalidades elegíveis em config.us.h2_eligible_countries venceu em "
                           f"{lst.valid_through}; atualize com a notificação vigente antes de confiar nela.")
        elif set(prof.nationalities) & set(lst.countries):
            c.result = "pass"
            c.reason_pt = f"Nacionalidade consta na lista vigente até {lst.valid_through}."
        else:
            c.result = "fail"
            c.reason_pt = f"Nenhuma das nacionalidades {prof.nationalities} consta na lista confirmada {lst.countries}."
        return c
    return mk("unknown")  # manual_confirmation e qualquer outro


def evaluate(cfg: Config, ex: Extraction, *, source_url: str, is_official: bool, now: datetime, text: str = "") -> EligibilityResult:
    age = current_age(cfg, now)
    checks: list[Check] = []
    notes: list[str] = []
    next_steps: list[str] = []

    if not is_official:
        checks.append(Check("official-source", "official_source", "unknown",
                            "A URL não pertence a um domínio oficial da lista do config; não sustenta elegibilidade.",
                            source_url=source_url, origin="deterministic"))

    for r in ex.requirements:
        checks.append(_eval_requirement(cfg, r, age, source_url))

    programs = detect_programs(cfg, ex, text)
    for pid in programs:
        for rule in cfg.programs[pid].rules:
            checks.append(_eval_program_rule(cfg, pid, rule, age, now))

    if ex.country == "US":
        notes.append(US_NOTE)
        if _is_employment(ex) and not any(
            c.kind == "work_authorization" and c.result in ("pass", "fail") for c in checks
        ):
            checks.append(Check("us-work-authorization", "work_authorization", "unknown",
                                "Vaga nos EUA sem evidência de autorização/patrocínio de trabalho adequado.",
                                source_url=source_url, origin="deterministic"))

    if not any(c.kind != "official_source" for c in checks):
        checks.append(Check("requirements-missing", "requirements_missing", "unknown",
                            "Nenhum requisito foi extraído com evidência; não é possível afirmar elegibilidade.",
                            source_url=source_url, origin="deterministic"))

    fails = [c for c in checks if c.result == "fail"]
    unknown = [c for c in checks if c.result == "unknown"]
    has_after_move = any(r.kind == "residence_after_move" for r in ex.requirements)

    if fails:
        status = "ineligible"
        relocation = "before_apply" if all(c.fixable_by_relocation for c in fails) else "none"
    else:
        status = "needs_confirmation" if unknown else "eligible"
        relocation = "after_award" if has_after_move else "none"

    if status == "eligible":
        next_steps.append("Conferir a fonte oficial e preparar a candidatura. O radar nunca envia candidaturas: exige aprovação humana.")
        if ex.country == "US" and _is_employment(ex):
            next_steps.append("Contatar o empregador/recrutador: a petição de visto de trabalho é do empregador.")
    elif status == "needs_confirmation":
        next_steps.append("Confirmar com a instituição emissora os pontos marcados como desconhecidos antes de se candidatar.")
    elif relocation == "before_apply":
        next_steps.append("Só seria possível após mudar a residência para o território exigido; avaliar se compensa.")

    label = {"eligible": "Elegível", "needs_confirmation": "Precisa de confirmação", "ineligible": "Inelegível"}[status]
    reasons = [c.reason_pt for c in (fails or unknown)][:2]
    summary = label + (": " + " ".join(reasons) if reasons else ".")
    if relocation == "after_award":
        summary += " Exige mudança de residência posterior."
    return EligibilityResult(status, relocation, checks, notes, next_steps, summary)
