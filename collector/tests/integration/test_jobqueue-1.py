import threading
from datetime import datetime, timedelta, timezone

import psycopg
import pytest
from psycopg.rows import dict_row

from radar.jobqueue import claim, complete, enqueue, fail, release_stale, requeue_dead, stats

pytestmark = pytest.mark.integration
NOW = datetime(2026, 11, 19, 18, 0, tzinfo=timezone.utc)


@pytest.fixture()
def qcfg(cfg):
    cfg.queue.max_attempts, cfg.queue.backoff_base_seconds, cfg.queue.backoff_max_seconds = 3, 60, 200
    return cfg.queue


def row(conn, job_id):
    return conn.execute("select * from jobs where id=%s", (job_id,)).fetchone()


def test_enqueue_then_claim_marks_running_and_counts_the_attempt(conn):
    jid = enqueue(conn, "scan_source", {"source_id": "s1"}, now=NOW)
    job = claim(conn, "w1", NOW)
    assert job.id == jid and job.kind == "scan_source" and job.payload == {"source_id": "s1"} and job.attempts == 1
    r = row(conn, jid)
    assert r["status"] == "running" and r["locked_by"] == "w1" and claim(conn, "w2", NOW) is None


def test_future_jobs_wait_and_older_run_at_goes_first(conn):
    later = enqueue(conn, "a", run_at=NOW + timedelta(minutes=5), now=NOW)
    second = enqueue(conn, "b", run_at=NOW - timedelta(minutes=1), now=NOW)
    first = enqueue(conn, "c", run_at=NOW - timedelta(minutes=9), now=NOW)
    assert [claim(conn, "w", NOW).id, claim(conn, "w", NOW).id, claim(conn, "w", NOW)] == [first, second, None]
    assert claim(conn, "w", NOW + timedelta(minutes=6)).id == later


def test_dedupe_key_blocks_duplicates_until_the_job_finishes(conn):
    a = enqueue(conn, "scan_source", dedupe_key="scan:s1", now=NOW)
    assert enqueue(conn, "scan_source", dedupe_key="scan:s1", now=NOW) is None
    complete(conn, claim(conn, "w", NOW).id, NOW)
    assert enqueue(conn, "scan_source", dedupe_key="scan:s1", now=NOW) not in (None, a)


def test_failure_backs_off_exponentially_then_goes_to_the_dlq(conn, qcfg):
    jid = enqueue(conn, "x", max_attempts=qcfg.max_attempts, now=NOW)
    j = claim(conn, "w", NOW)
    assert fail(conn, j, "erro 1", qcfg, NOW) == "retry"
    r = row(conn, jid)
    assert r["status"] == "queued" and r["run_at"] == NOW + timedelta(seconds=60) and r["locked_by"] is None
    j = claim(conn, "w", NOW + timedelta(seconds=60))
    assert fail(conn, j, "erro 2", qcfg, NOW) == "retry" and row(conn, jid)["run_at"] == NOW + timedelta(seconds=120)
    j = claim(conn, "w", NOW + timedelta(seconds=120))
    assert fail(conn, j, "erro 3 token=abc123", qcfg, NOW) == "dead"
    r = row(conn, jid)
    assert r["status"] == "dead" and r["attempts"] == 3 and "abc123" not in r["last_error"] and r["finished_at"] is not None


def test_backoff_is_capped(conn, qcfg):
    enqueue(conn, "x", max_attempts=10, now=NOW)
    for k in range(1, 5):
        j = claim(conn, "w", NOW + timedelta(days=k))
        fail(conn, j, "e", qcfg, NOW)
    assert row(conn, j.id)["run_at"] == NOW + timedelta(seconds=200)


def test_requeue_from_the_dlq_resets_attempts(conn, qcfg):
    jid = enqueue(conn, "x", max_attempts=1, now=NOW)
    fail(conn, claim(conn, "w", NOW), "boom", qcfg, NOW)
    assert row(conn, jid)["status"] == "dead"
    assert requeue_dead(conn, jid, NOW) is True and requeue_dead(conn, jid, NOW) is False
    r = row(conn, jid)
    assert r["status"] == "queued" and r["attempts"] == 0 and r["last_error"] is None


def test_stale_running_jobs_from_a_crashed_worker_are_released(conn):
    jid = enqueue(conn, "x", now=NOW)
    claim(conn, "w", NOW)
    assert release_stale(conn, NOW + timedelta(minutes=5), stale_after_s=600) == 0
    assert release_stale(conn, NOW + timedelta(minutes=11), stale_after_s=600) == 1
    assert row(conn, jid)["status"] == "queued"


def test_stats_count_by_status(conn, qcfg):
    enqueue(conn, "c", max_attempts=1, run_at=NOW - timedelta(minutes=1), now=NOW)  # o mais antigo é o primeiro a ser reivindicado
    enqueue(conn, "a", now=NOW); enqueue(conn, "b", now=NOW)
    fail(conn, claim(conn, "w", NOW), "x", qcfg, NOW)
    s = stats(conn)
    assert s["queued"] == 2 and s["dead"] == 1 and s["running"] == 0


def test_concurrent_workers_never_claim_the_same_job(db_url, conn):
    ids = {enqueue(conn, "x", now=NOW) for _ in range(30)}
    got, lock = [], threading.Lock()

    def worker(name):
        with psycopg.connect(db_url, row_factory=dict_row) as c:
            while (j := claim(c, name, NOW)) is not None:
                with lock:
                    got.append(j.id)

    threads = [threading.Thread(target=worker, args=(f"w{i}",)) for i in range(4)]
    [t.start() for t in threads]; [t.join() for t in threads]
    assert sorted(got) == sorted(ids)  # todos processados, nenhum duplicado
