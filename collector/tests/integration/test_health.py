from datetime import timedelta

import pytest

from radar.cli import main
from radar.health import worker_health
from tests.integration.pipeline_kit import NOW

pytestmark = pytest.mark.integration


def beat(conn, at):
    conn.execute("insert into service_heartbeats (service, last_seen) values ('worker', %s) "
                 "on conflict (service) do update set last_seen = excluded.last_seen", (at,))


def test_no_heartbeat_means_unhealthy_with_a_clear_reason(aconn):
    ok, why = worker_health(aconn, NOW, max_age_s=900)
    assert ok is False and "nunca" in why


def test_recent_heartbeat_is_healthy_and_old_one_is_not(aconn):
    beat(aconn, NOW - timedelta(seconds=60))
    assert worker_health(aconn, NOW, max_age_s=900)[0] is True
    beat(aconn, NOW - timedelta(seconds=901))
    ok, why = worker_health(aconn, NOW, max_age_s=900)
    assert ok is False and "901" in why


def test_cli_healthcheck_exit_codes(db_url, aconn, monkeypatch, capsys):
    monkeypatch.setenv("DATABASE_URL", db_url)
    assert main(["healthcheck"]) == 1                       # sem heartbeat
    aconn.execute("insert into service_heartbeats (service, last_seen) values ('worker', now())")
    assert main(["healthcheck"]) == 0
    assert "ok" in capsys.readouterr().out.lower()
    monkeypatch.delenv("DATABASE_URL")
    assert main(["healthcheck"]) == 2                       # configuração ausente
