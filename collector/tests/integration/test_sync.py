import pytest

from radar.sync import sync_sources
from tests.integration.pipeline_kit import ENV, NOW, RENT, count

pytestmark = pytest.mark.integration


def row(conn, sid):
    return conn.execute("select * from sources where id=%s", (sid,)).fetchone()


def test_every_configured_source_is_upserted_with_its_tos_status(aconn, cfg):
    sync_sources(aconn, cfg, {}, NOW)
    assert count(aconn, "sources") == len(cfg.sources)
    assert row(aconn, "it-regione-sardegna-antispopolamento")["tos_status"] == "pending_review"
    assert row(aconn, "us-dol-seasonaljobs-h2b")["tos_status"] == "allowed"


def test_fixture_sources_can_never_be_enabled_outside_the_test_environment(aconn, cfg):
    cfg.source(RENT).enabled = True
    sync_sources(aconn, cfg, {"RADAR_ENV": "production"}, NOW)
    assert row(aconn, RENT)["enabled"] is False and row(aconn, RENT)["origin"] == "fixture"
    sync_sources(aconn, cfg, ENV, NOW)
    assert row(aconn, RENT)["enabled"] is True


def test_resync_is_idempotent_and_keeps_the_schedule(aconn, cfg):
    sync_sources(aconn, cfg, {}, NOW)
    aconn.execute("update sources set last_run_at=%s, consecutive_failures=2 where id='us-dol-seasonaljobs-h2b'", (NOW,))
    sync_sources(aconn, cfg, {}, NOW)
    r = row(aconn, "us-dol-seasonaljobs-h2b")
    assert count(aconn, "sources") == len(cfg.sources) and r["last_run_at"] == NOW and r["consecutive_failures"] == 2


def test_config_changes_reach_the_database(aconn, cfg):
    sync_sources(aconn, cfg, {}, NOW)
    cfg.source("us-dol-seasonaljobs-h2b").interval_minutes = 99
    sync_sources(aconn, cfg, {}, NOW)
    assert row(aconn, "us-dol-seasonaljobs-h2b")["interval_minutes"] == 99


def test_a_source_removed_from_config_is_disabled_not_deleted(aconn, cfg):
    sync_sources(aconn, cfg, {}, NOW)
    cfg.sources = [s for s in cfg.sources if s.id != "us-dol-seasonaljobs-h2a"]
    sync_sources(aconn, cfg, {}, NOW)
    assert row(aconn, "us-dol-seasonaljobs-h2a")["enabled"] is False


def test_candidate_profile_is_stored_from_config(aconn, cfg):
    sync_sources(aconn, cfg, {}, NOW)
    p = aconn.execute("select * from candidate_profiles").fetchone()
    assert p["label"] == cfg.profile.label and p["profile"]["nationalities"] == ["BR", "IT"] and p["config_hash"]
