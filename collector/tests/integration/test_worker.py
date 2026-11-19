from datetime import timedelta

import pytest

import radar.worker as worker
from radar.jobqueue import enqueue
from radar.worker import schedule_due_sources, tick
from tests.integration.pipeline_kit import NOW, RENT, FakeExtractor, FakeSender, count, deps, rent_bonus, setup

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def one_channel(cfg):
    cfg.alerts.channels.smtp.enabled = False


def jobs(conn):
    return conn.execute("select kind, status, payload, attempts, last_error from jobs order by id").fetchall()


def test_only_enabled_allowed_and_due_sources_are_scheduled_and_never_twice(aconn, cfg):
    setup(aconn, cfg, RENT, "us-dol-seasonaljobs-h2b", "it-regione-sardegna-antispopolamento")  # a última tem tos pending_review: não executa
    assert schedule_due_sources(aconn, cfg, NOW) == 2   # fixture RENT + H-2B
    assert schedule_due_sources(aconn, cfg, NOW) == 0   # dedupe_key
    assert {j["payload"]["source_id"] for j in jobs(aconn)} == {RENT, "us-dol-seasonaljobs-h2b"}


def test_a_source_not_due_yet_is_not_scheduled(aconn, cfg):
    setup(aconn, cfg, RENT)
    aconn.execute("update sources set next_run_at=%s where id=%s", (NOW + timedelta(hours=2), RENT))
    assert schedule_due_sources(aconn, cfg, NOW) == 0


def test_tick_runs_the_whole_cycle_scan_then_alert_and_reschedules(aconn, cfg):
    setup(aconn, cfg, RENT)
    sender = FakeSender()
    r = tick(aconn, cfg, deps(FakeExtractor(rent_bonus), senders=[sender]), NOW, worker="w1")
    assert r["scheduled"] >= 1 and r["jobs_done"] == 1 and r["alerts_sent"] == 1
    assert len(sender.sent) == 1 and jobs(aconn)[0]["status"] == "done"
    s = aconn.execute("select last_run_at, next_run_at, consecutive_failures from sources where id=%s", (RENT,)).fetchone()
    assert s["last_run_at"] == NOW and s["next_run_at"] == NOW + timedelta(minutes=60) and s["consecutive_failures"] == 0
    assert tick(aconn, cfg, deps(FakeExtractor(rent_bonus), senders=[sender]), NOW + timedelta(minutes=5), worker="w1")["jobs_done"] == 0
    assert tick(aconn, cfg, deps(FakeExtractor(rent_bonus), senders=[sender]), NOW + timedelta(minutes=61), worker="w1")["jobs_done"] == 1
    assert count(aconn, "opportunities") == 1 and len(sender.sent) == 1   # nada duplicado


def test_unexpected_crash_retries_with_backoff_then_lands_in_the_dlq(aconn, cfg, monkeypatch):
    cfg.queue.max_attempts, cfg.queue.backoff_base_seconds = 2, 60
    setup(aconn, cfg, RENT)

    def boom(*a, **k):
        raise RuntimeError("bug inesperado password=hunter2")

    monkeypatch.setattr(worker, "run_source", boom)
    enqueue(aconn, "scan_source", {"source_id": RENT, "trigger": "manual"}, now=NOW, max_attempts=2, dedupe_key=f"scan:{RENT}")
    assert tick(aconn, cfg, deps(), NOW, worker="w")["jobs_failed"] == 1 and jobs(aconn)[0]["status"] == "queued"
    assert tick(aconn, cfg, deps(), NOW + timedelta(seconds=61), worker="w")["jobs_failed"] == 1
    j = jobs(aconn)[0]
    assert j["status"] == "dead" and "hunter2" not in j["last_error"]


def test_unknown_job_kind_goes_to_the_dlq_instead_of_looping(aconn, cfg):
    setup(aconn, cfg)
    enqueue(aconn, "tipo_inexistente", {}, now=NOW, max_attempts=1)
    tick(aconn, cfg, deps(), NOW, worker="w")
    assert jobs(aconn)[0]["status"] == "dead"


def test_tick_writes_a_heartbeat_and_a_manual_scan_job_is_honoured(aconn, cfg):
    setup(aconn, cfg, RENT)
    enqueue(aconn, "scan_source", {"source_id": RENT, "trigger": "manual"}, now=NOW, dedupe_key=f"scan:{RENT}")
    tick(aconn, cfg, deps(FakeExtractor(rent_bonus)), NOW, worker="w9")
    hb = aconn.execute("select * from service_heartbeats where service='worker'").fetchone()
    assert hb["last_seen"] == NOW and hb["info"]["worker"] == "w9"
    assert aconn.execute("select trigger from source_runs").fetchone()["trigger"] == "manual"
