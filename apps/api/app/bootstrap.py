from __future__ import annotations

import subprocess
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.db.session import SessionLocal, engine
from app.seed import ensure_seeded_if_empty

BOOTSTRAP_LOCK_ID = 0x4F707341


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
    with bootstrap_lock(engine):
        run_migrations()
        with SessionLocal() as session:
            result = ensure_seeded_if_empty(session)
            if result:
                print(f"Seeded empty database (fingerprint={result.fingerprint})")
            else:
                print("Seed data already present; skipping reseed")


def main() -> None:
    run_startup_bootstrap()


if __name__ == "__main__":
    main()
