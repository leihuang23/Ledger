from __future__ import annotations

import subprocess
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.agent.service import abandon_orphaned_active_runs
from app.db.session import SessionLocal, engine
from app.logging_config import configure_logging, get_logger
from app.seed import ensure_seeded_if_empty
from app.tools.service import register_builtin_tools

BOOTSTRAP_LOCK_ID = 0x4F707341

logger = get_logger(__name__)


@contextmanager
def bootstrap_lock(db_engine: Engine = engine) -> Iterator[None]:
    """Serialize first-boot migrations and seeding across API replicas."""
    if db_engine.dialect.name != "postgresql":
        yield
        return

    with db_engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        acquired = False
        try:
            connection.execute(
                text("SELECT pg_advisory_lock(:lock_id)"),
                {"lock_id": BOOTSTRAP_LOCK_ID},
            )
            acquired = True
            yield
        finally:
            if acquired:
                connection.execute(
                    text("SELECT pg_advisory_unlock(:lock_id)"),
                    {"lock_id": BOOTSTRAP_LOCK_ID},
                )


def run_migrations() -> None:
    subprocess.run(["alembic", "upgrade", "head"], check=True)


def run_startup_bootstrap() -> None:
    configure_logging()
    with bootstrap_lock(engine):
        run_migrations()
        with SessionLocal() as session:
            register_builtin_tools(session)
            result = ensure_seeded_if_empty(session)
            if result:
                logger.info(
                    "Seeded empty database",
                    extra={"fingerprint": result.fingerprint},
                )
            else:
                logger.info("Seed data already present; skipping reseed")
            abandoned_count = abandon_orphaned_active_runs(session)
            if abandoned_count:
                logger.info(
                    "Marked stale agent runs failed",
                    extra={"abandoned_count": abandoned_count},
                )


def main() -> None:
    run_startup_bootstrap()


if __name__ == "__main__":
    main()
