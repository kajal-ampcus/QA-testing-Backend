# QA Platform

Agentic AI automation testing platform — automates the QA lifecycle (requirements → application discovery → test design → automation → execution → failure triage → reporting) for testers without source-code access to the application under test.

## Start here

- `CLAUDE.md` — project context auto-loaded by Claude Code every session
- `docs/architecture/agentic-qa-platform-architecture.md` — full system design (37 sections)
- `docs/architecture/agentic-qa-platform-io-contracts-and-devtools.md` — agent I/O contracts + Chrome DevTools MCP integration
- `docs/PROJECT_STRUCTURE.md` — this repo's folder layout and the reasoning behind it
- `docs/CODEBASE_GUIDE.md` — file-by-file inventory of every folder, request-flow walkthrough, and setup instructions
- `docs/IMPLEMENTATION_PLAN.md` — phased build plan (generated via the Claude Code kickoff prompt)

## Status

Projects, requirement understanding and approval, application discovery, and test-case generation are implemented. Discovery runs as an `arq` job in a separate worker. Test validation, automation, execution, triage, and reporting remain later milestones.

The sibling `../frontend` application now connects to these APIs.
See its README for frontend setup and `docs/FRONTEND_INTEGRATION.md` for the
architecture review, endpoint mapping, and remaining milestones.

After starting the API on port 8000, run `npm.cmd install` and `npm.cmd run dev`
from `../frontend`, then open http://localhost:3000. The frontend
uses a development proxy by default. For a separately hosted frontend, set
`CORS_ORIGINS` in the backend `.env` to a JSON array of allowed origins.

## Run everything in Docker

From this directory, with Docker Desktop running:

```powershell
docker compose pull --ignore-buildable
docker compose up -d --build --wait --wait-timeout 180
docker compose ps -a
```

Open http://localhost:3000. One **qa-platform** application contains the frontend,
API, worker, PostgreSQL, Redis, and MinIO. Migrations and bucket creation run
automatically as setup jobs. The existing backend `.env` supplies LLM and
credential-encryption settings; secrets are excluded from build contexts.

See [Docker setup](deploy/docker/README.md) for ports, configuration, persistence,
troubleshooting, and the difference between setup jobs and running services.

## Local development without containerized application services

`pyproject.toml` requires Python **>=3.12**. On Windows, if `python --version`
shows something older, use the `py` launcher to pick a supported version
(e.g. `py -3.13`) — check what's installed with `py -0`.

```bash
# 1. venv on a supported Python version
py -3.13 -m venv .venv

py -3.13 -m venv .venv         # Windows
# source .venv/bin/activate     # macOS/Linux

# 2. install deps (editable, with dev extras)
pip install -e ".[dev]"

# 3. bring up Postgres/Redis/MinIO via Docker Compose
docker compose up -d postgres redis minio storage-init

# 4. migrate the database
python -m alembic upgrade head

# 5. run the API and, in a separate terminal, the worker
uvicorn apps.api.main:app --reload
arq apps.worker.arq_worker.WorkerSettings
```

Equivalent shortcuts once deps are installed: `make run-api` and `make run-worker`.
Set `DATABASE_URL` and `REDIS_URL` in `.env` to match the running services.

Once running:
- `GET /health` — liveness, no setup required
- `GET /health/db` — verifies Postgres connectivity; needs `DATABASE_URL`
  reachable (set in `.env`, copied from `.env.example`)

To start discovery, create a project with `application_url`, then call
`POST /api/v1/application-maps/projects/{project_id}/discover` with
`{"focus_requirements": []}`. Omitting `url` uses the project's application
URL. A nonempty focus list must contain approved requirement IDs or codes.
The response contains a `job_id`; poll
`GET /api/v1/application-maps/jobs/{job_id}` and then read
`GET /api/v1/application-maps/projects/{project_id}`. Redis is required to
enqueue discovery, and the separate worker is required to complete it.

### Authenticated application discovery

For an application that redirects to a login page, configure an encryption key
in your uncommitted `.env` and submit a test account once through the backend
API. The API returns an opaque reference; the project, Redis job payload, and
application map never contain the username or password.

```env
CREDENTIAL_ENCRYPTION_KEY=replace-with-a-Fernet-key
```

```json
POST /api/v1/projects/{project_id}/credentials
{
  "username": "qa@example.test",
  "password": "your-test-password",
  "username_selector": "Email",
  "password_selector": "Password",
  "submit_selector": "Sign in"
}
```

The returned reference is set as the project's default automatically. Then
trigger discovery normally. Login controls are detected by common accessible
names (`Email`/`Username`, `Password`, `Sign in`/`Login`); supply the optional
selector fields when yours differ.

For request-by-request API testing, import
`postman/qa-platform.postman_collection.json` into Postman. See
`postman/README.md` for the project, requirement, approval, and discovery order.
If a requirement has ambiguities, `GET /api/v1/requirements/{requirement_id}`
shows their zero-based array positions. Submit one tester decision per item to
`POST /api/v1/requirements/{requirement_id}/clarifications` with
`expected_version`, `resolved_by`, and `resolutions`. This appends an auditable
version without another LLM extraction. Fetch the new pending approval before
calling its approve endpoint.

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

The last command generates SQL without connecting. Application models and
migrations are implemented; run `upgrade head` whenever the database schema is
behind the code.

If PostgreSQL reports password authentication failure, correct `DATABASE_URL`
in `.env` (or the overriding environment variable) to match the database user.
