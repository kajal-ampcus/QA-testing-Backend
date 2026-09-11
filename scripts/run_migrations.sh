#!/usr/bin/env bash
# Runs pending Alembic migrations against DATABASE_URL. See Makefile's `migrate` target.
set -euo pipefail
alembic upgrade head
