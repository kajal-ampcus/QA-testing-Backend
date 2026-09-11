# QA Platform

Agentic AI automation testing platform — automates the QA lifecycle (requirements → application discovery → test design → automation → execution → failure triage → reporting) for testers without source-code access to the application under test.

## Start here

- `CLAUDE.md` — project context auto-loaded by Claude Code every session
- `docs/architecture/agentic-qa-platform-architecture.md` — full system design (37 sections)
- `docs/architecture/agentic-qa-platform-io-contracts-and-devtools.md` — agent I/O contracts + Chrome DevTools MCP integration
- `docs/PROJECT_STRUCTURE.md` — this repo's folder layout and the reasoning behind it
- `docs/IMPLEMENTATION_PLAN.md` — phased build plan (generated via the Claude Code kickoff prompt)

## Status

**Phase 0 — repo scaffolding.** Directory structure and tooling config are in place; no agent reasoning logic is implemented yet. See `docs/IMPLEMENTATION_PLAN.md` for current phase and next milestone once it exists.

## Local development

`pyproject.toml` requires Python **>=3.12**. On Windows, if `python --version`
shows something older, use the `py` launcher to pick a supported version
(e.g. `py -3.13`) — check what's installed with `py -0`.

```bash
# 1. venv on a supported Python version
py -3.13 -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # macOS/Linux

# 2. install deps (editable, with dev extras)
pip install -e ".[dev]"

# 3. (optional) bring up Postgres/Redis/MinIO via Docker Compose
docker compose -f deploy/docker/docker-compose.yml up -d

# 4. run the API
uvicorn apps.api.main:app --reload
```

Equivalent shortcuts once deps are installed: `make dev` (installs deps +
pre-commit) and `make run-api`. See `Makefile` for the rest of the common
commands (`make test`, `make lint`, `make migrate`, `make docker-up`) —
`make run-worker` isn't wired up yet since the worker/queue library
(Celery+Redis vs. `arq`) is still undecided, per `CLAUDE.md`.

Once running:
- `GET /health` — liveness, no setup required
- `GET /health/db` — verifies Postgres connectivity; needs `DATABASE_URL`
  reachable (set in `.env`, copied from `.env.example`)

Only these two endpoints exist today — everything else in the app is still a
Phase 0 stub (see `docs/IMPLEMENTATION_PLAN.md` for what's next).

## Database migrations

Alembic and the API read `DATABASE_URL` from the project-root `.env` file.
An environment variable takes precedence. Use a `postgresql+asyncpg://` URL;
URL-encode special characters in passwords. Credentials are not stored in
`alembic.ini`.

```bash
python -m alembic current
python -m alembic upgrade head
python -m alembic upgrade head --sql
```

The last command generates SQL without connecting. The migration framework
is configured, but application models and migration revisions are still to
be implemented. Once models exist, import their modules in
`infra/db/models/__init__.py`, then generate and review a migration:

```bash
python -m alembic revision --autogenerate -m "create initial tables"
python -m alembic upgrade head
```

If PostgreSQL reports password authentication failure, correct `DATABASE_URL`
in `.env` (or the overriding environment variable) to match the database user.
