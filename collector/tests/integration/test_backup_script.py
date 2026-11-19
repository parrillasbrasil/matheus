import os
import subprocess
import time
from pathlib import Path

import psycopg
import pytest

pytestmark = pytest.mark.integration
SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "backup.sh"


def run(env_extra, tmp_path):
    env = {**os.environ, "BACKUP_DIR": str(tmp_path), **env_extra}
    env.pop("BACKUP_INTERVAL_SECONDS", None)
    return subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True, text=True, timeout=60)


def test_creates_a_restorable_dump_and_prunes_only_old_ones(db_url, aconn, tmp_path):
    aconn.execute("create table backup_probe (id int)")
    aconn.execute("insert into backup_probe values (42)")
    old = tmp_path / "radar-20200101-000000.dump"
    old.write_text("velho")
    ancient = time.time() - 30 * 86400
    os.utime(old, (ancient, ancient))
    unrelated = tmp_path / "notas.txt"
    unrelated.write_text("não é backup")
    os.utime(unrelated, (ancient, ancient))

    r = run({"DATABASE_URL": db_url, "RETENTION_DAYS": "14"}, tmp_path)
    assert r.returncode == 0, r.stderr

    dumps = sorted(p.name for p in tmp_path.glob("radar-*.dump"))
    assert len(dumps) == 1 and dumps[0] != old.name            # velho removido, novo criado
    assert unrelated.exists()                                   # arquivos que não são backup nunca são tocados
    listing = subprocess.run(["pg_restore", "--list", str(tmp_path / dumps[0])], capture_output=True, text=True)
    assert listing.returncode == 0 and "backup_probe" in listing.stdout   # o dump é legível e contém os dados


def test_failure_exits_nonzero_and_leaves_no_partial_dump(tmp_path):
    r = run({"DATABASE_URL": "postgresql://postgres@127.0.0.1:1/naoexiste"}, tmp_path)
    assert r.returncode != 0
    assert list(tmp_path.glob("radar-*")) == []                 # nem .dump nem .partial
