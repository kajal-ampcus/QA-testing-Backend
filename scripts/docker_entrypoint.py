"""Derive the internal Docker database URL, then replace this process with the service."""

import os
import sys

from sqlalchemy.engine import URL


def configure_database() -> None:
    if os.environ.get("DB_HOST"):
        os.environ["DATABASE_URL"] = URL.create(
            "postgresql+asyncpg",
            username=os.environ["POSTGRES_USER"],
            password=os.environ["POSTGRES_PASSWORD"],
            host=os.environ["DB_HOST"],
            port=int(os.environ.get("DB_PORT", "5432")),
            database=os.environ["POSTGRES_DB"],
        ).render_as_string(hide_password=False)


if __name__ == "__main__":
    configure_database()
    if len(sys.argv) < 2:
        raise SystemExit("A service command is required")
    os.execvp(sys.argv[1], sys.argv[1:])
