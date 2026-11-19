"""Fixtures de banco: um template migrado 1x por sessão; cada teste ganha uma cópia isolada.

Requer RADAR_TEST_DATABASE_URL apontando para um servidor PostgreSQL descartável
(ex.: `scripts/local-pg.sh start` ou o serviço db-test do docker-compose.test.yml).
"""
from __future__ import annotations

import os
import uuid
from pathlib import Path

import psycopg
import pytest
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row

from radar.migrate import migrate

MIGRATIONS = Path(__file__).resolve().parents[3] / "db" / "migrations"
ADMIN_URL = os.environ.get("RADAR_TEST_DATABASE_URL")


def _with_db(url: str, name: str) -> str:
    return make_conninfo(url, dbname=name)


def _drop(name: str) -> None:
    with psycopg.connect(ADMIN_URL, autocommit=True) as c:
        c.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


@pytest.fixture(scope="session")
def template_db():
    if not ADMIN_URL:
        pytest.skip("RADAR_TEST_DATABASE_URL não definido: testes de integração exigem PostgreSQL real")
    name = f"radar_tpl_{uuid.uuid4().hex[:8]}"
    with psycopg.connect(ADMIN_URL, autocommit=True) as c:
        c.execute(f'CREATE DATABASE "{name}"')
    migrate(_with_db(ADMIN_URL, name), MIGRATIONS)
    yield name
    _drop(name)


@pytest.fixture()
def db_url(template_db):
    name = f"radar_t_{uuid.uuid4().hex[:10]}"
    with psycopg.connect(ADMIN_URL, autocommit=True) as c:
        c.execute(f'CREATE DATABASE "{name}" TEMPLATE "{template_db}"')
    yield _with_db(ADMIN_URL, name)
    _drop(name)


@pytest.fixture()
def conn(db_url):
    with psycopg.connect(db_url, row_factory=dict_row) as c:
        yield c


@pytest.fixture()
def aconn(db_url):
    """Conexão em autocommit, como no worker real (cada escrita é uma transação explícita)."""
    with psycopg.connect(db_url, row_factory=dict_row, autocommit=True) as c:
        yield c
