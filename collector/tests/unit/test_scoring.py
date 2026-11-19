from tests.conftest import make_extraction, make_req

from radar.deadlines import ResolvedDeadline
from radar.eligibility import EligibilityResult
from radar.schema import MoneyValue
from radar.scoring import score_opportunity

NO_DEADLINE = ResolvedDeadline(utc=None, tz=None, tz_assumed=False, confirmed=False)


def elig(status):
    return EligibilityResult(status=status, relocation_condition="none", checks=[], notes=[], next_steps=[], summary_pt="")


def val(amount, cur="EUR"):
    return MoneyValue(amount=amount, currency=cur, gross_net="unknown", period="one_time", quote="q")


def test_eligible_scores_higher_than_needs_confirmation_which_beats_ineligible(cfg, now):
    ex = make_extraction(value=val(15000))
    s = {st: score_opportunity(cfg, ex, elig(st), NO_DEADLINE, 1.0, now).total for st in ("eligible", "needs_confirmation", "ineligible")}
    assert s["eligible"] > s["needs_confirmation"] > s["ineligible"]


def test_ineligible_is_capped(cfg, now):
    ex = make_extraction(value=val(50000))
    assert score_opportunity(cfg, ex, elig("ineligible"), NO_DEADLINE, 1.0, now).total <= cfg.scoring.ineligible_cap


def test_unknown_value_scores_zero_for_value_and_says_so(cfg, now):
    r = score_opportunity(cfg, make_extraction(), elig("eligible"), NO_DEADLINE, 1.0, now)
    assert r.components["benefit_value"] == 0
    assert any("desconhecido" in line for line in r.explanation)


def test_unknown_currency_is_not_guessed(cfg, now):
    r = score_opportunity(cfg, make_extraction(value=val(15000, "XYZ")), elig("eligible"), NO_DEADLINE, 1.0, now)
    assert r.components["benefit_value"] == 0


def test_higher_value_scores_higher(cfg, now):
    lo = score_opportunity(cfg, make_extraction(value=val(600)), elig("eligible"), NO_DEADLINE, 1.0, now).components["benefit_value"]
    hi = score_opportunity(cfg, make_extraction(value=val(15000)), elig("eligible"), NO_DEADLINE, 1.0, now).components["benefit_value"]
    assert hi > lo > 0


def test_category_priority_follows_config_order(cfg, now):
    gov = score_opportunity(cfg, make_extraction(category="gov_relocation_incentive"), elig("eligible"), NO_DEADLINE, 1.0, now)
    other = score_opportunity(cfg, make_extraction(category="other_cost_reduction"), elig("eligible"), NO_DEADLINE, 1.0, now)
    assert gov.components["category_fit"] > other.components["category_fit"]


def test_eu_citizenship_leverage_only_where_free_movement_applies(cfg, now):
    it = score_opportunity(cfg, make_extraction(country="IT"), elig("eligible"), NO_DEADLINE, 1.0, now)
    us = score_opportunity(cfg, make_extraction(country="US"), elig("eligible"), NO_DEADLINE, 1.0, now)
    assert it.components["citizenship_leverage"] > 0
    assert us.components["citizenship_leverage"] == 0
    assert any("não concede autorização de trabalho" in line for line in us.explanation)


def test_evidence_quality_scales_component(cfg, now):
    full = score_opportunity(cfg, make_extraction(), elig("eligible"), NO_DEADLINE, 1.0, now)
    half = score_opportunity(cfg, make_extraction(), elig("eligible"), NO_DEADLINE, 0.5, now)
    assert full.components["evidence_quality"] > half.components["evidence_quality"]


def test_total_is_between_0_and_100_and_explanation_is_readable(cfg, now):
    r = score_opportunity(cfg, make_extraction(value=val(1_000_000)), elig("eligible"), NO_DEADLINE, 1.0, now)
    assert 0 <= r.total <= 100
    assert all(isinstance(line, str) and line for line in r.explanation)
    assert abs(sum(r.components.values()) - r.total) < 0.51
