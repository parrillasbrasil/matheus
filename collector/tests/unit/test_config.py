import pytest
import yaml

from radar.config import ConfigError, load_config
from radar.official import is_official_url


def _dump(tmp_path, data):
    p = tmp_path / "config.yaml"
    p.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    return p


def test_repo_config_loads_with_profile_from_the_user(repo_config_path):
    cfg = load_config(repo_config_path)
    assert cfg.profile.age_years == 20
    assert cfg.profile.nationalities == ["BR", "IT"]
    assert cfg.profile.residence_country == "BR"
    assert cfg.profile.education == "high_school_complete"
    assert cfg.profile.languages["en"] == "B1"


def test_unknown_top_level_field_is_rejected(tmp_path, raw_config):
    raw_config["campo_misterioso"] = 1
    with pytest.raises(ConfigError, match="campo_misterioso"):
        load_config(_dump(tmp_path, raw_config))


def test_unknown_nested_field_is_rejected(tmp_path, raw_config):
    raw_config["profile"]["numero_do_sapato"] = 42
    with pytest.raises(ConfigError, match="numero_do_sapato"):
        load_config(_dump(tmp_path, raw_config))


def test_citizenship_can_never_imply_us_work_authorization(tmp_path, raw_config):
    raw_config["us"]["assume_work_authorization_from_citizenship"] = True
    with pytest.raises(ConfigError, match="assume_work_authorization_from_citizenship"):
        load_config(_dump(tmp_path, raw_config))


def test_duplicate_source_ids_are_rejected(tmp_path, raw_config):
    raw_config["sources"].append(dict(raw_config["sources"][0]))
    with pytest.raises(ConfigError, match="duplicad"):
        load_config(_dump(tmp_path, raw_config))


def test_score_component_weights_must_sum_to_100(tmp_path, raw_config):
    first = next(iter(raw_config["scoring"]["component_weights"]))
    raw_config["scoring"]["component_weights"][first] += 7
    with pytest.raises(ConfigError, match="100"):
        load_config(_dump(tmp_path, raw_config))


def test_missing_file_is_a_config_error(tmp_path):
    with pytest.raises(ConfigError, match="não encontrado"):
        load_config(tmp_path / "nao-existe.yaml")


def test_norway_code_is_a_country_string_not_a_yaml_boolean(cfg):
    # YAML 1.1 lê NO sem aspas como false; o loader estrito precisa garantir que isso não passe
    assert "NO" in cfg.reference_data.country_groups["EU_EEA"]
    assert cfg.tz_for_country("NO") == "Europe/Oslo"


def test_unquoted_norway_is_rejected(tmp_path, raw_config):
    raw_config["reference_data"]["country_groups"]["EU_EEA"].append(False)
    with pytest.raises(ConfigError):
        load_config(_dump(tmp_path, raw_config))


def test_real_sources_are_never_fixture_origin_and_vice_versa(cfg):
    for s in cfg.sources:
        assert (s.origin == "fixture") == (s.kind == "fixture"), s.id


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://www.regione.sardegna.it/misure-anti-spopolamento", True),
        ("https://youth.europa.eu/solidarity/faq/3_en", True),
        ("https://seasonaljobs.dol.gov/feeds", True),
        ("https://www.comune.esempio.it/bando", True),
        ("https://www.sardegnaimpresa.eu/it/agevolazioni/x", True),
        ("https://www.greenme.it/lifestyle/isola-sardegna", False),
        ("https://impatria.com/?p=10189", False),
        ("https://evil.example.com/youth.europa.eu", False),
        ("https://youth.europa.eu.evil.example.com/", False),
        ("not a url", False),
    ],
)
def test_is_official_url(cfg, url, expected):
    assert is_official_url(cfg, url) is expected
