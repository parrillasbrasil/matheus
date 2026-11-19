import shutil

import psycopg
import pytest

from radar.migrate import MigrationError, migrate
from tests.integration.conftest import MIGRATIONS

pytestmark = pytest.mark.integration

SPEC_TABLES = [
    "candidate_profiles", "sources", "source_runs", "documents", "opportunities", "opportunity_versions",
    "evidence_spans", "eligibility_checks", "scores", "alerts", "applications", "audit_events", "agent_usage",
]
INFRA_TABLES = ["jobs", "llm_cache", "service_heartbeats", "schema_migrations"]


def _tables(conn):
    rows = conn.execute("select table_name from information_schema.tables where table_schema='public'").fetchall()
    return {r["table_name"] for r in rows}


def test_all_tables_from_the_spec_exist(conn):
    assert set(SPEC_TABLES) <= _tables(conn)


def test_queue_cache_and_heartbeat_tables_exist(conn):
    assert set(INFRA_TABLES) <= _tables(conn)


def test_migrate_is_idempotent(db_url):
    assert migrate(db_url, MIGRATIONS) == []


def test_modifying_an_applied_migration_is_detected(db_url, tmp_path):
    copy = tmp_path / "migrations"
    shutil.copytree(MIGRATIONS, copy)
    first = sorted(copy.glob("*.sql"))[0]
    first.write_text(first.read_text() + "\n-- alterado depois de aplicado\n")
    with pytest.raises(MigrationError, match="alterada"):
        migrate(db_url, copy)


def _insert_source(conn):
    conn.execute(
        "insert into sources (id,name,kind,origin,enabled,url,interval_minutes,rate_limit,tos_status) "
        "values ('s1','Fonte','html_page','real',true,'https://x.gov.it',60,'{}','allowed')"
    )


def _opportunity_sql(status="open", elig="eligible", origin="real", reloc="none"):
    return (
        "insert into opportunities (opportunity_key,source_id,canonical_url,is_official,data_origin,status,"
        "eligibility_status,relocation_condition,extraction,extraction_origin,last_verified_at,content_hash) "
        f"values ('k1','s1','https://x.gov.it/a',true,'{origin}','{status}','{elig}','{reloc}','{{}}','llm',now(),'h')"
    )


def test_valid_opportunity_row_is_accepted(conn):
    _insert_source(conn)
    conn.execute(_opportunity_sql())


@pytest.mark.parametrize(
    "kwargs",
    [dict(elig="elegivel"), dict(status="aberto"), dict(origin="simulado"), dict(reloc="later")],
)
def test_check_constraints_reject_invalid_enums(conn, kwargs):
    _insert_source(conn)
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(_opportunity_sql(**kwargs))


def test_fixture_origin_source_must_not_have_real_opportunities(conn):
    # a origem 'fixture' da fonte tem que contaminar a oportunidade: não existe oportunidade 'real' vinda de fonte de teste
    conn.execute(
        "insert into sources (id,name,kind,origin,enabled,url,interval_minutes,rate_limit,tos_status) "
        "values ('t1','Teste','fixture','fixture',true,'https://x.gov.it',60,'{}','allowed')"
    )
    with pytest.raises(psycopg.errors.RaiseException):
        conn.execute(
            "insert into opportunities (opportunity_key,source_id,canonical_url,is_official,data_origin,status,"
            "eligibility_status,relocation_condition,extraction,extraction_origin,last_verified_at,content_hash) "
            "values ('k2','t1','https://x.gov.it/b',true,'real','open','eligible','none','{}','llm',now(),'h')"
        )
