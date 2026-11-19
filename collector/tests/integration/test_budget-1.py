from datetime import datetime, timedelta, timezone

import pytest

from radar.llm.base import LlmCall
from radar.llm.budget import cache_get, cache_key, cache_put, can_spend, cost_usd, record_usage, usage_today

NOW = datetime(2026, 11, 19, 18, 0, tzinfo=timezone.utc)
CALL = LlmCall("openai", "modelo-x", 1000, 500, 120)


def test_cost_is_unknown_without_pricing_and_computed_with_it(cfg):
    assert cost_usd(cfg, "modelo-x", 1000, 500) is None
    cfg.llm.pricing_usd_per_mtok = {"modelo-x": {"input": 2.0, "output": 8.0}}
    assert cost_usd(cfg, "modelo-x", 1_000_000, 500_000) == pytest.approx(6.0)
    assert cost_usd(cfg, "outro", 10, 10) is None


def test_cache_key_depends_on_everything_that_changes_the_answer():
    base = cache_key("openai", "m", "extract-v1", "hash1", "schema1")
    assert base == cache_key("openai", "m", "extract-v1", "hash1", "schema1")
    for other in (cache_key("anthropic", "m", "extract-v1", "hash1", "schema1"), cache_key("openai", "m2", "extract-v1", "hash1", "schema1"),
                  cache_key("openai", "m", "extract-v2", "hash1", "schema1"), cache_key("openai", "m", "extract-v1", "hash2", "schema1"),
                  cache_key("openai", "m", "extract-v1", "hash1", "schema2")):
        assert other != base


@pytest.mark.integration
def test_cache_roundtrip(conn):
    assert cache_get(conn, "k") is None
    cache_put(conn, "k", "openai", "m", {"a": 1}, 10, 5)
    cache_put(conn, "k", "openai", "m", {"a": 2}, 10, 5)  # idempotente: mantém a primeira
    assert cache_get(conn, "k") == {"a": 1}


@pytest.mark.integration
def test_usage_today_sums_only_the_current_utc_day_and_flags_unknown_cost(conn, cfg):
    record_usage(conn, CALL, cfg, "extraction", now=NOW)
    record_usage(conn, CALL, cfg, "extraction", now=NOW - timedelta(days=1))
    u = usage_today(conn, NOW)
    assert u.tokens == 1500 and u.cost_usd == 0 and u.unknown_cost_calls == 1


@pytest.mark.integration
def test_cached_and_failed_calls_spend_no_tokens(conn, cfg):
    record_usage(conn, CALL, cfg, "extraction", cached=True, now=NOW)
    record_usage(conn, None, cfg, "audit", status="error", error="HTTP 500 api_key=zzz", provider="anthropic", model="c", now=NOW)
    assert usage_today(conn, NOW).tokens == 0
    err = conn.execute("select error from agent_usage where status='error'").fetchone()["error"]
    assert "zzz" not in err


@pytest.mark.integration
def test_token_cap_blocks_even_when_cost_is_unknown(conn, cfg):
    cfg.llm.budget.daily_token_cap = 2000
    record_usage(conn, CALL, cfg, "extraction", now=NOW)
    assert can_spend(conn, cfg, NOW, estimated_tokens=400) == (True, "")
    ok, why = can_spend(conn, cfg, NOW, estimated_tokens=600)
    assert not ok and "tokens" in why


@pytest.mark.integration
def test_usd_cap_only_counts_calls_with_known_price(conn, cfg):
    cfg.llm.pricing_usd_per_mtok = {"modelo-x": {"input": 1000.0, "output": 1000.0}}
    cfg.llm.budget.daily_usd_cap = 1.0
    assert can_spend(conn, cfg, NOW)[0]
    record_usage(conn, CALL, cfg, "extraction", now=NOW)  # custa US$ 1,50
    ok, why = can_spend(conn, cfg, NOW)
    assert not ok and "US$" in why
