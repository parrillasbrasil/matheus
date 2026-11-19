from datetime import date

import pytest

from radar.cli import main
from radar.readiness import readiness

FULL_ENV = {
    "DATABASE_URL": "postgresql://x", "OPENAI_API_KEY": "k", "OPENAI_MODEL": "m", "ANTHROPIC_API_KEY": "k",
    "TELEGRAM_BOT_TOKEN": "1:a", "TELEGRAM_CHAT_ID": "1", "RADAR_CONTACT": "pai@exemplo.com",
}
TODAY = date(2026, 11, 19)


def texts(findings, level=None):
    return " | ".join(f.text for f in findings if level is None or f.level == level)


def test_missing_credentials_are_reported_with_their_consequence(cfg):
    f = readiness(cfg, {}, TODAY)
    assert "DATABASE_URL" in texts(f, "blocker")
    w = texts(f, "warn")
    assert "OPENAI_API_KEY" in w and "pending_llm" in w and "ANTHROPIC_API_KEY" in w and "Telegram" in w and "RADAR_CONTACT" in w


def test_configured_environment_has_no_credential_warnings(cfg):
    w = texts(readiness(cfg, FULL_ENV, TODAY), "warn") + texts(readiness(cfg, FULL_ENV, TODAY), "blocker")
    for word in ("OPENAI", "ANTHROPIC", "Telegram", "DATABASE_URL", "RADAR_CONTACT"):
        assert word not in w


def test_pending_tos_sources_and_expired_reference_data_are_reported(cfg):
    w = texts(readiness(cfg, FULL_ENV, TODAY), "warn")
    assert "it-regione-sardegna-antispopolamento" in w and "ToS" in w     # habilitada, mas não executa
    assert "H-2" in w and "vencida" in w                                    # lista de países do H-2 expirou em 2025-11-07
    assert "câmbio" not in w                                                # FX é de hoje
    assert "câmbio" in texts(readiness(cfg, FULL_ENV, date(2027, 6, 1)), "warn")


def test_check_config_ok_exit_code_and_output(capsys, repo_config_path, monkeypatch):
    for k in FULL_ENV:
        monkeypatch.delenv(k, raising=False)
    assert main(["check-config", "--config", str(repo_config_path)]) == 1   # DATABASE_URL ausente é bloqueio
    out = capsys.readouterr().out
    assert "config.yaml válido" in out and "BLOQUEIO" in out
    monkeypatch.setenv("DATABASE_URL", "postgresql://x")
    assert main(["check-config", "--config", str(repo_config_path)]) == 0


def test_check_config_rejects_unknown_fields(capsys, tmp_path, repo_config_path):
    bad = tmp_path / "config.yaml"
    bad.write_text(repo_config_path.read_text() + "\ncampo_inventado: 1\n", encoding="utf-8")
    assert main(["check-config", "--config", str(bad)]) == 2
    assert "campo_inventado" in capsys.readouterr().err


def test_missing_database_url_is_a_clean_error_for_db_commands(capsys, repo_config_path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert main(["sync", "--config", str(repo_config_path)]) == 2
    assert "DATABASE_URL" in capsys.readouterr().err
