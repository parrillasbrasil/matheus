"""Fixtures compartilhadas. Tudo aqui é TESTE; nada representa pesquisa ao vivo."""
from __future__ import annotations

import copy
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

os.environ["RADAR_ENV"] = "test"  # fontes de fixture só rodam em ambiente de teste
REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = REPO_ROOT / "config.yaml"
NOW = datetime(2026, 11, 19, 18, 0, tzinfo=timezone.utc)


@pytest.fixture(scope="session")
def repo_config_path() -> Path:
    return CONFIG_PATH


@pytest.fixture()
def raw_config() -> dict:
    with open(CONFIG_PATH, encoding="utf-8") as fh:
        return copy.deepcopy(yaml.safe_load(fh))


@pytest.fixture(scope="session")
def _cfg_session():
    from radar.config import load_config

    return load_config(CONFIG_PATH)


@pytest.fixture()
def cfg(_cfg_session):
    return _cfg_session.model_copy(deep=True)


@pytest.fixture()
def now() -> datetime:
    return NOW


def make_extraction(**overrides):
    from radar.schema import Deadline, Extraction, MoneyValue

    base = dict(
        is_opportunity=True,
        program_hint=None,
        title_original="Titolo di prova",
        title_pt="Título de teste",
        issuer="Ente de teste",
        country="IT",
        city_or_region=None,
        benefit_types=["housing_support"],
        category="gov_relocation_incentive",
        benefit_summary_pt=None,
        value=MoneyValue(amount=None, currency=None, gross_net="unknown", period="unknown", quote=None),
        deadline=Deadline(iso=None, timezone=None, quote=None),
        status="unknown",
        status_quote=None,
        aid_type="unknown",
        requirements=[],
        obligations=[],
        not_covered_expenses=[],
    )
    base.update(overrides)
    return Extraction(**base)


def make_req(kind: str, **overrides):
    from radar.schema import Requirement

    base = dict(
        id=f"r-{kind}",
        kind=kind,
        values=[],
        number=None,
        level=None,
        work_authorization="unknown",
        mandatory=True,
        text_pt=f"requisito {kind}",
        quote="trecho de teste",
        verified=True,
    )
    base.update(overrides)
    return Requirement(**base)
