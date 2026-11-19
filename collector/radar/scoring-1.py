"""Pontuação 0-100 baseada nos pesos do config.yaml, com explicação legível."""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .config import Config
from .deadlines import ResolvedDeadline
from .eligibility import US_NOTE, EligibilityResult
from .schema import Extraction


@dataclass
class ScoreResult:
    total: float
    components: dict[str, float] = field(default_factory=dict)
    explanation: list[str] = field(default_factory=list)


def _value_in_eur(cfg: Config, ex: Extraction) -> tuple[float | None, str]:
    v = ex.value
    if v.amount is None or not v.currency:
        return None, "valor do benefício desconhecido (não pontua)"
    rate = cfg.reference_data.fx_to_eur.rates.get(v.currency.upper())
    if rate is None:
        return None, f"moeda {v.currency} sem câmbio no config (valor tratado como desconhecido)"
    eur = v.amount * rate
    if v.period == "hourly":
        return None, "salário por hora: não anualizado por falta de carga horária (não pontua)"
    if v.period == "monthly":
        eur *= 12
        return eur, f"valor ≈ {eur:,.0f} EUR/ano (mensal × 12, câmbio de referência)"
    return eur, f"valor ≈ {eur:,.0f} EUR (câmbio de referência do config)"


def score_opportunity(cfg: Config, ex: Extraction, elig: EligibilityResult, resolved: ResolvedDeadline,
                      evidence_ratio: float, now: datetime) -> ScoreResult:
    sc = cfg.scoring
    w = sc.component_weights
    comps: dict[str, float] = {}
    why: list[str] = []

    cat_w = sc.category_weights.get(ex.category, sc.category_weights["unknown"])
    comps["category_fit"] = w["category_fit"] * cat_w
    why.append(f"categoria {ex.category}: {cat_w:.0%} do peso de prioridade")

    eur, vmsg = _value_in_eur(cfg, ex)
    why.append(vmsg)
    if eur is None or eur < sc.value_reference_eur.low:
        comps["benefit_value"] = 0.0
    else:
        lo, hi = sc.value_reference_eur.low, sc.value_reference_eur.high
        comps["benefit_value"] = w["benefit_value"] * min(1.0, math.log(eur / lo) / math.log(hi / lo))

    fac = sc.eligibility_factor[elig.status]
    comps["eligibility"] = w["eligibility"] * fac
    why.append(f"elegibilidade {elig.status}: {fac:.0%} do peso")

    group = cfg.expand_countries([sc.citizenship_leverage_group])
    has_eu_passport = bool(set(cfg.profile.nationalities) & cfg.expand_countries(["EU"]))
    if ex.country and ex.country.upper() in group and has_eu_passport:
        comps["citizenship_leverage"] = w["citizenship_leverage"]
        why.append("cidadania italiana facilita entrada/residência/trabalho nesse país (livre circulação)")
    else:
        comps["citizenship_leverage"] = 0.0
        if ex.country == "US":
            why.append(US_NOTE + " (sem alavancagem)")
        else:
            why.append("cidadania italiana não dá vantagem comprovada nesse país")

    ratio = max(0.0, min(1.0, evidence_ratio))
    comps["evidence_quality"] = w["evidence_quality"] * ratio
    why.append(f"evidência verificada: {ratio:.0%} dos campos")

    d = sc.deadline
    if resolved.utc is None:
        f_dead, dmsg = d.unknown_factor, "prazo desconhecido"
    elif resolved.utc < now:
        f_dead, dmsg = 0.0, "prazo vencido"
    else:
        days = (resolved.utc - now) / timedelta(days=1)
        f_dead = 1.0 if days >= d.comfortable_days else d.urgent_factor
        dmsg = f"prazo em {days:.0f} dias" + (" (fuso presumido)" if resolved.tz_assumed else "")
    comps["deadline_window"] = w["deadline_window"] * f_dead
    why.append(dmsg)

    comps = {k: round(v, 2) for k, v in comps.items()}
    total = round(min(100.0, max(0.0, sum(comps.values()))), 2)
    if elig.status == "ineligible" and total > sc.ineligible_cap:
        total = sc.ineligible_cap
        why.append(f"pontuação limitada a {sc.ineligible_cap:g} por inelegibilidade")
    return ScoreResult(total=total, components=comps, explanation=why)
