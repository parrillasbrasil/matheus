"""CLI do radar: check-config | migrate | sync | scan | tick | worker | healthcheck."""
from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import threading
from datetime import date, datetime, timezone
from pathlib import Path

from .config import Config, ConfigError, load_config
from .logs import setup_logging

DEFAULT_CONFIG = "config.yaml"


def _load(args: argparse.Namespace) -> Config:
    return load_config(args.config or os.environ.get("RADAR_CONFIG", DEFAULT_CONFIG))


def _dsn() -> str:
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise ConfigError("DATABASE_URL não definido")
    return dsn


def cmd_check_config(args: argparse.Namespace) -> int:
    from .readiness import readiness

    cfg = _load(args)
    real = [s for s in cfg.sources if s.origin == "real"]
    print(f"config.yaml válido: {len(real)} fonte(s) real(is), {len(cfg.sources) - len(real)} fixture(s) de teste, perfil '{cfg.profile.label}'.")
    blocked = False
    for f in readiness(cfg, os.environ, date.today()):
        print({"ok": "OK       ", "warn": "ATENÇÃO  ", "blocker": "BLOQUEIO "}[f.level] + f.text)
        blocked = blocked or f.level == "blocker"
    return 1 if blocked else 0


def cmd_migrate(args: argparse.Namespace) -> int:
    from .migrate import migrate

    applied = migrate(_dsn(), args.dir or os.environ.get("RADAR_MIGRATIONS_DIR", "db/migrations"))
    print("migrations aplicadas:", applied or "nenhuma (já atualizado)")
    return 0


def cmd_sync(args: argparse.Namespace) -> int:
    from .db import connect
    from .sync import sync_sources

    cfg = _load(args)
    with connect(_dsn(), autocommit=True) as conn:
        sync_sources(conn, cfg, os.environ, datetime.now(timezone.utc))
    print(f"{len(cfg.sources)} fonte(s) sincronizadas.")
    return 0


def cmd_scan(args: argparse.Namespace) -> int:
    from .db import connect
    from .pipeline import run_source
    from .worker import deps_from_env

    cfg = _load(args)
    ids = args.source or [s.id for s in cfg.sources if s.enabled and s.tos.status == "allowed" and s.origin == "real"]
    deps = deps_from_env(cfg)
    with connect(_dsn(), autocommit=True) as conn:
        for sid in ids:
            res = run_source(conn, cfg, sid, trigger="manual", now=datetime.now(timezone.utc), deps=deps)
            print(f"{sid}: {res.status} {res.stats}")
    return 0


def cmd_tick(args: argparse.Namespace) -> int:
    from .db import connect
    from .worker import deps_from_env, tick

    cfg = _load(args)
    with connect(_dsn(), autocommit=True) as conn:
        print(tick(conn, cfg, deps_from_env(cfg), datetime.now(timezone.utc), worker="cli"))
    return 0


def cmd_worker(args: argparse.Namespace) -> int:
    from .worker import deps_from_env, run_forever

    cfg = _load(args)
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())   # parada limpa no redeploy do Coolify
    logging.getLogger("radar").info("worker iniciado")
    run_forever(_dsn(), cfg, deps_from_env(cfg), worker=os.environ.get("HOSTNAME", "worker"), stop=stop)
    return 0


def cmd_healthcheck(args: argparse.Namespace) -> int:
    """Exit 0 se o worker deu sinal de vida recentemente; 1 se não; 2 se faltar configuração. Usado no HEALTHCHECK do Docker."""
    from .db import connect
    from .health import worker_health

    try:
        with connect(_dsn(), autocommit=True) as conn:
            ok, why = worker_health(conn, datetime.now(timezone.utc), max_age_s=args.max_age)
    except ConfigError:
        raise
    except Exception as exc:  # noqa: BLE001 - banco fora do ar também é "não saudável"
        print(f"não saudável: banco inacessível ({exc.__class__.__name__})")
        return 1
    print(("" if ok else "não saudável: ") + why)
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="radar", description="Global Opportunity Radar")
    parser.add_argument("--config", default=None, help="caminho do config.yaml (padrão: $RADAR_CONFIG ou ./config.yaml)")
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name, fn, helptext in (
        ("check-config", cmd_check_config, "valida o config.yaml e mostra o que falta para operar"),
        ("migrate", cmd_migrate, "aplica as migrations SQL"),
        ("sync", cmd_sync, "sincroniza config.yaml -> banco"),
        ("scan", cmd_scan, "executa uma varredura manual"),
        ("tick", cmd_tick, "uma iteração do worker"),
        ("worker", cmd_worker, "loop 24/7 do worker"),
        ("healthcheck", cmd_healthcheck, "exit 0 se o worker deu sinal de vida há pouco (HEALTHCHECK do Docker)"),
    ):
        p = sub.add_parser(name, help=helptext)
        p.set_defaults(fn=fn)
        p.add_argument("--config", default=argparse.SUPPRESS)
        if name == "scan":
            p.add_argument("--source", action="append", help="id da fonte (repetível); padrão: todas as habilitadas e com ToS aprovado")
        if name == "migrate":
            p.add_argument("--dir", default=None)
        if name == "healthcheck":
            p.add_argument("--max-age", type=int, default=900, help="segundos máximos desde o último heartbeat (padrão 900)")
    args = parser.parse_args(argv)
    setup_logging(os.environ.get("LOG_LEVEL", "INFO"))
    try:
        return args.fn(args)
    except ConfigError as exc:
        print(f"erro: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
