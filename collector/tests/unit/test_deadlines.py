from datetime import datetime, timezone

from radar.deadlines import effective_status, resolve_deadline
from radar.schema import Deadline


def test_date_without_timezone_assumes_country_tz_and_is_not_confirmed(cfg, now):
    r = resolve_deadline(cfg, Deadline(iso="2026-11-30", timezone=None, quote="entro il 30/11"), "IT", quote_verified=True, now=now)
    assert r.tz == "Europe/Rome"
    assert r.tz_assumed is True
    assert r.confirmed is False
    # fim do dia local (23:59:59 em Roma = 22:59:59 UTC)
    assert r.utc == datetime(2026, 11, 30, 22, 59, 59, tzinfo=timezone.utc)


def test_explicit_timezone_and_verified_quote_is_confirmed(cfg, now):
    r = resolve_deadline(
        cfg,
        Deadline(iso="2026-12-15T12:00:00", timezone="America/New_York", quote="12:00 noon ET, Dec 15"),
        "US",
        quote_verified=True,
        now=now,
    )
    assert r.tz_assumed is False
    assert r.confirmed is True
    assert r.utc == datetime(2026, 12, 15, 17, 0, tzinfo=timezone.utc)


def test_unverified_quote_never_confirms_a_deadline(cfg, now):
    r = resolve_deadline(cfg, Deadline(iso="2026-12-15", timezone="Europe/Rome", quote="x"), "IT", quote_verified=False, now=now)
    assert r.confirmed is False


def test_missing_or_garbage_deadline_is_unknown_not_invented(cfg, now):
    for iso in (None, "", "breve", "31/31/2026"):
        r = resolve_deadline(cfg, Deadline(iso=iso, timezone=None, quote=None), "IT", quote_verified=False, now=now)
        assert r.utc is None and r.confirmed is False


def test_past_deadline_forces_closed_even_if_source_says_open(cfg, now):
    r = resolve_deadline(cfg, Deadline(iso="2026-04-30", timezone="Europe/Rome", quote="q"), "IT", quote_verified=True, now=now)
    assert effective_status("open", r, status_verified=True, now=now) == "closed"


def test_open_requires_verified_status_and_non_past_deadline(cfg, now):
    r = resolve_deadline(cfg, Deadline(iso="2027-04-30", timezone="Europe/Rome", quote="q"), "IT", quote_verified=True, now=now)
    assert effective_status("open", r, status_verified=True, now=now) == "open"
    assert effective_status("open", r, status_verified=False, now=now) == "unknown"
    assert effective_status("unknown", r, status_verified=False, now=now) == "unknown"
    assert effective_status("closed", r, status_verified=True, now=now) == "closed"
