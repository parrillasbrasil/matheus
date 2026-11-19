from tests.conftest import make_extraction, make_req

from radar.eligibility import evaluate

ESC_URL = "https://youth.europa.eu/solidarity/faq/3_en"
GOV_URL = "https://www.regione.sardegna.it/bando-teste"


def run(cfg, ex, now, url=GOV_URL, official=True, text=""):
    return evaluate(cfg, ex, source_url=url, is_official=official, now=now, text=text)


def kinds(res, result):
    return [c.kind for c in res.checks if c.result == result]


# --- cidadania != residência ------------------------------------------------
def test_esc_with_brazil_residence_is_not_automatically_eligible(cfg, now):
    ex = make_extraction(
        program_hint="European Solidarity Corps",
        category="youth_program_covered",
        benefit_types=["youth_program_covered"],
        requirements=[make_req("age_max", number=30), make_req("nationality_any", values=["EU"])],
    )
    res = run(cfg, ex, now, url=ESC_URL, text="European Solidarity Corps volunteering")
    assert res.status == "ineligible"
    assert res.relocation_condition == "before_apply"
    fails = [c for c in res.checks if c.result == "fail"]
    assert any(
        c.kind == "residence_current_any" and c.origin == "program_rule" and "youth.europa.eu" in (c.source_url or "") and c.quote
        for c in fails
    )
    # a cidadania italiana passou, mas não basta
    assert any(c.kind == "nationality_any" and c.result == "pass" for c in res.checks)


def test_required_italian_residence_fails_even_though_citizenship_passes(cfg, now):
    ex = make_extraction(
        requirements=[make_req("nationality_any", values=["IT"]), make_req("residence_current_any", values=["IT"])]
    )
    res = run(cfg, ex, now)
    assert res.status == "ineligible"
    assert res.relocation_condition == "before_apply"


def test_benefit_conditioned_on_later_move_is_eligible_with_after_award_flag(cfg, now):
    ex = make_extraction(
        requirements=[
            make_req("nationality_any", values=["EU"]),
            make_req("residence_after_move", values=["IT"]),
            make_req("age_max", number=35),
        ]
    )
    res = run(cfg, ex, now)
    assert res.status == "eligible"
    assert res.relocation_condition == "after_award"


# --- trabalho nos EUA -----------------------------------------------------------
def test_us_job_without_sponsorship_evidence_needs_confirmation(cfg, now):
    ex = make_extraction(
        country="US", benefit_types=["salary_job"], category="job_with_package",
        requirements=[make_req("education_min", level="high_school_complete")],
    )
    res = run(cfg, ex, now, url="https://seasonaljobs.dol.gov/x")
    assert res.status == "needs_confirmation"
    assert "work_authorization" in kinds(res, "unknown")
    assert any("não concede autorização de trabalho" in n for n in res.notes)


def test_us_job_requiring_candidate_to_bring_authorization_is_ineligible(cfg, now):
    ex = make_extraction(
        country="US", benefit_types=["salary_job"], category="job_with_package",
        requirements=[make_req("work_authorization", work_authorization="required_from_candidate")],
    )
    res = run(cfg, ex, now, url="https://seasonaljobs.dol.gov/x")
    assert res.status == "ineligible"
    assert res.relocation_condition == "none"


def test_us_h2b_sponsored_but_nationality_list_expired_needs_confirmation(cfg, now):
    ex = make_extraction(
        country="US", program_hint="H-2B", benefit_types=["salary_job"], category="job_with_package",
        requirements=[make_req("work_authorization", work_authorization="provided_by_employer")],
    )
    res = run(cfg, ex, now, url="https://seasonaljobs.dol.gov/x")
    assert res.status == "needs_confirmation"
    assert "h2_nationality" in kinds(res, "unknown")


def test_us_h2b_sponsored_with_valid_listed_nationality_is_eligible(cfg, now):
    cfg.us.h2_eligible_countries.valid_through = "2027-11-30"
    ex = make_extraction(
        country="US", program_hint="H-2B", benefit_types=["salary_job"], category="job_with_package",
        requirements=[make_req("work_authorization", work_authorization="provided_by_employer")],
    )
    res = run(cfg, ex, now, url="https://seasonaljobs.dol.gov/x")
    assert res.status == "eligible"
    assert res.next_steps  # contatar empregador: a petição é dele


def test_us_h2b_unlisted_nationalities_are_ineligible(cfg, now):
    cfg.us.h2_eligible_countries.valid_through = "2027-11-30"
    cfg.us.h2_eligible_countries.countries = ["AR"]
    ex = make_extraction(
        country="US", program_hint="H-2B", benefit_types=["salary_job"], category="job_with_package",
        requirements=[make_req("work_authorization", work_authorization="provided_by_employer")],
    )
    assert run(cfg, ex, now, url="https://seasonaljobs.dol.gov/x").status == "ineligible"


# --- demais requisitos ------------------------------------------------------------
def test_age_above_limit_is_ineligible_and_not_fixable_by_moving(cfg, now):
    res = run(cfg, make_extraction(requirements=[make_req("age_max", number=19)]), now)
    assert res.status == "ineligible"
    assert res.relocation_condition == "none"


def test_age_minimum_met_passes(cfg, now):
    res = run(cfg, make_extraction(requirements=[make_req("age_min", number=18)]), now)
    assert res.status == "eligible"


def test_unverified_mandatory_requirement_can_only_need_confirmation(cfg, now):
    ex = make_extraction(requirements=[make_req("education_min", level="bachelor", verified=False)])
    res = run(cfg, ex, now)
    assert res.status == "needs_confirmation"
    assert "education_min" in kinds(res, "unknown")


def test_education_above_profile_is_ineligible_and_at_profile_passes(cfg, now):
    assert run(cfg, make_extraction(requirements=[make_req("education_min", level="bachelor")]), now).status == "ineligible"
    assert run(cfg, make_extraction(requirements=[make_req("education_min", level="high_school_complete")]), now).status == "eligible"


def test_language_levels_pass_fail_and_unknown(cfg, now):
    assert run(cfg, make_extraction(requirements=[make_req("language_min", values=["en"], level="B1")]), now).status == "eligible"
    assert run(cfg, make_extraction(requirements=[make_req("language_min", values=["en"], level="C1")]), now).status == "ineligible"
    # italiano não declarado no perfil: não dá para afirmar nada
    assert run(cfg, make_extraction(requirements=[make_req("language_min", values=["it"], level="B2")]), now).status == "needs_confirmation"


def test_experience_requirement_is_unknown_when_profile_has_no_data(cfg, now):
    res = run(cfg, make_extraction(requirements=[make_req("experience_min_months", number=12)]), now)
    assert res.status == "needs_confirmation"


def test_optional_other_requirement_does_not_block(cfg, now):
    ex = make_extraction(requirements=[make_req("other", mandatory=False)])
    assert run(cfg, ex, now).status == "eligible"


def test_mandatory_other_requirement_needs_confirmation(cfg, now):
    ex = make_extraction(requirements=[make_req("other", mandatory=True)])
    assert run(cfg, ex, now).status == "needs_confirmation"


def test_non_official_source_can_never_be_eligible(cfg, now):
    res = run(cfg, make_extraction(requirements=[make_req("age_min", number=18)]), now, url="https://blog.example.com/x", official=False)
    assert res.status == "needs_confirmation"
    assert "official_source" in kinds(res, "unknown")


def test_no_requirements_extracted_is_not_eligible_by_default(cfg, now):
    # sem requisito algum, a IA pode ter simplesmente falhado em ler a página
    res = run(cfg, make_extraction(requirements=[]), now)
    assert res.status == "needs_confirmation"


def test_eures_targeted_mobility_needs_confirmation_because_sources_conflict(cfg, now):
    ex = make_extraction(
        program_hint="EURES Targeted Mobility Scheme", category="job_with_package",
        requirements=[make_req("nationality_any", values=["EU"]), make_req("age_min", number=18)],
    )
    res = run(cfg, ex, now, url="https://eures.europa.eu/x", text="Targeted Mobility Scheme (Your first EURES job)")
    assert res.status == "needs_confirmation"
    assert any(c.origin == "program_rule" and c.result == "unknown" for c in res.checks)
