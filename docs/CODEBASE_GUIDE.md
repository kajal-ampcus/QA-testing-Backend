# Codebase Guide

A practical, file-by-file map of this repo: what each folder is for, what
each file does (or will do), how a request flows through them, and how to
set up and run the project locally. This is a companion to
`docs/PROJECT_STRUCTURE.md` (the *rationale* for the layout) — this doc is
the *inventory*.

> **Current status:** Phase 0. Every file below marked "Phase 0 stub" has a
> docstring only — no real logic yet. The exceptions are called out
> explicitly. See `docs/IMPLEMENTATION_PLAN.md` for what gets built next
> (Milestone 1: data layer + Requirement Understanding Agent + first
> approval gate).
>
> **Missing docs:** `docs/architecture/agentic-qa-platform-architecture.md`
> and `docs/architecture/agentic-qa-platform-io-contracts-and-devtools.md`
> are referenced throughout this repo (and by every docstring's "Section N"
> citation below) but are **not present** in `docs/architecture/` yet. This
> guide is reconstructed from `CLAUDE.md`, `docs/PROJECT_STRUCTURE.md`, and
> `docs/IMPLEMENTATION_PLAN.md` plus the file docstrings themselves — add the
> two source docs and re-check specifics against them before relying on this
> for exact field-level contracts.

---

## 1. Setup — running it locally

Requires Python **>=3.12** (`pyproject.toml`). On Windows, check what's
installed with `py -0`; if `python --version` shows something older, target
a specific version with `py -3.13`, `py -3.12`, etc.

```bash
# 1. venv on a supported Python version
py -3.13 -m venv .venv
.venv\Scripts\activate           # Windows
# source .venv/bin/activate      # macOS/Linux

# 2. install deps (editable, with dev extras)
pip install -e ".[dev]"

# 3. copy env config and fill in real values
copy .env.example .env           # Windows
# cp .env.example .env           # macOS/Linux

# 4. (optional) bring up Postgres/Redis/MinIO via Docker Compose
docker compose -f deploy/docker/docker-compose.yml up -d

# 5. run the API
uvicorn apps.api.main:app --reload
```

Shortcuts once deps are installed (see `Makefile`):

| Command | Does |
|---|---|
| `make dev` | `pip install -e ".[dev]"` + `pre-commit install` |
| `make run-api` | `uvicorn apps.api.main:app --reload` |
| `make run-worker` | not wired up yet — worker/queue library undecided |
| `make test` | `pytest` |
| `make lint` | `ruff check .` + `ruff format --check .` |
| `make typecheck` | `mypy .` |
| `make check-arch` | `lint-imports` — fails the build on an architecture-boundary violation |
| `make migrate` | `alembic upgrade head` |
| `make seed` | `python scripts/seed_dev_db.py` |
| `make docker-up` / `docker-down` | the Compose stack |

Once running, only two endpoints exist today:

- `GET /health` — liveness, no setup required
- `GET /health/db` — verifies Postgres connectivity against `DATABASE_URL`
  (read from `.env` via `infra/db/settings.py`)

### Database migrations

```bash
python -m alembic current
python -m alembic upgrade head          # apply migrations
python -m alembic upgrade head --sql    # generate SQL without connecting
```

Alembic reads `DATABASE_URL` the same way the API does (`infra/db/settings.py`
→ project-root `.env`; an actual environment variable overrides it). Once
real models exist under `infra/db/models/`, import them in
`infra/db/models/__init__.py` so `Base.metadata` sees them, then:

```bash
python -m alembic revision --autogenerate -m "create initial tables"
python -m alembic upgrade head
```

If Postgres reports password authentication failure, fix `DATABASE_URL` in
`.env` to match the actual DB user/password.

### Outstanding setup decisions

Two things are still unresolved and block later milestones (see
`docs/IMPLEMENTATION_PLAN.md`):

- **Worker/queue library** — Celery+Redis vs. `arq`. Blocks Milestone 3 and
  Milestone 6 (real background task dispatch).
- **Package manager** — `uv` vs. Poetry vs. plain `pip`. Doesn't block
  functional work but should be settled soon.

---

## 2. The layering rule

```
apps/            entrypoints (HTTP + worker) — imports core/, schemas/, infra/
   │
   ▼
core/            business logic (agents, orchestrator, policy, tool_gateway)
   │              — never imports apps/
   ▼
domain/          pure entities + enums — no framework imports at all

schemas/         pydantic wire format          infra/    adapters (DB, storage,
   (used by apps/ + core/)                                LLM, queue, secrets)
```

**Hard rules, enforced in CI by `import-linter`** (`pyproject.toml` →
`[tool.importlinter]`, run via `make check-arch` / `lint-imports`, wired into
`.github/workflows/ci.yml`):

1. `core/` never imports `apps/`.
2. `core/agents/test_execution/` never imports `infra/llm/` — the structural
   guarantee that Test Execution has **zero LLM-in-the-loop**.
3. `core/agents/*` never imports `infra/secrets/` or
   `core/tool_gateway/mcp_clients/` directly — everything goes through
   `core/tool_gateway/gateway.py`.
4. `domain/` never imports `fastapi`, `sqlalchemy`, `apps/`, or `infra/`.

If a file you add violates one of these, CI fails on the `lint-imports` step
— that's intentional, not a bug to work around.

---

## 3. Folder-by-folder, file-by-file

### `apps/api/` — FastAPI HTTP layer (thin, no business logic)

| File | Purpose |
|---|---|
| `main.py` | App factory (`create_app()`); mounts `health` router now, will mount `routers/v1/*`, middleware, and the websocket router once they're real. |
| `dependencies.py` | DI providers: `get_db_session`, `get_current_user`, `get_tool_gateway`. Stub. |
| `middleware/tenant_scope.py` | Injects `project_id` scope on every request — the one place cross-tenant queries are structurally prevented. |
| `middleware/request_logging.py` | Structured request logging. Stub. |
| `middleware/error_handling.py` | Centralized exception → HTTP response mapping. Stub. |
| `routers/health.py` | **Real.** `GET /health` (liveness) and `GET /health/db` (Postgres connectivity check). |
| `routers/v1/projects.py` | Project CRUD — the root entity everything else scopes under. Stub. |
| `routers/v1/requirements.py` | Surfaces `core/agents/requirement_understanding`, triggers the agent, `POST /requirements/{id}/approve`. Stub — **first real router in Milestone 1**. |
| `routers/v1/application_maps.py` | Surfaces Application Discovery output, triggers `run_discovery.py`. Stub. |
| `routers/v1/test_cases.py` | Test Design + Validation output, tester edits (versioned). Stub. |
| `routers/v1/automation.py` | Automation Generation + Review output, selector-healing review. Stub. |
| `routers/v1/executions.py` | Triggers Test Execution (`run_execution.py`), surfaces `test_runs`/`test_results`. Stub. |
| `routers/v1/failures.py` | Failure Analysis output + evidence viewer + reproduction status. Stub. |
| `routers/v1/defects.py` | Proposed vs. filed defects, `POST /defects/{id}/approve-and-file`. Stub. |
| `routers/v1/reports.py` | Test Reporting Agent output, export (PDF/HTML). Stub. |
| `routers/v1/approvals.py` | **The one consistent approve/reject path** every human-approval gate posts through — writes to the `approvals` table. Stub. |
| `routers/v1/agent_activity.py` | Read-only feed of `agent_runs` (decisions/evidence/confidence) — the "Agent Activity" transparency screen. Stub. |
| `websockets/agent_events.py` | Live orchestrator state-transition stream (no polling). Stub — acceptable to do a simplified polling version first. |

### `apps/worker/` — background job runner (same `core/`, different entrypoint)

| File | Purpose |
|---|---|
| `celery_app.py` | Worker entrypoint. Stub — pending Celery vs. `arq` decision. |
| `tasks/run_discovery.py` | Wraps `core/agents/application_discovery`; where sharded/parallel crawl fan-out will live (post-MVP). |
| `tasks/run_execution.py` | Wraps `core/agents/test_execution` — runs the compiled Playwright suite, persists results/evidence. Zero LLM-in-the-loop. |
| `tasks/run_regression_trigger.py` | Wraps `core/agents/regression_maintenance` — webhook/schedule re-entry. Deferred past MVP (manual re-run at MVP). |

### `core/agents/` — one folder per agent, 10 agents total, never merge two agents' logic

| Agent folder | Role | Key files |
|---|---|---|
| `requirement_understanding/` | Agent 1 — free text → structured requirement (`REQ-ID`, acceptance criteria, ambiguity flags). LLM only, no browser/MCP tools (can't hallucinate a browsing action). | `agent.py`, `prompts.py`, `schemas.py` |
| `application_discovery/` | Agent 2 — builds the Application Map via `chrome-devtools-mcp`, no source code access. Every element must cite a DOM/accessibility source. | `agent.py`, `crawler.py` (priority-queue crawl + sharding), `fingerprint.py` (state dedup hash), `prompts.py`, `schemas.py` |
| `test_design/` | Agent 3 (absorbs Test Strategy) — requirements + application_map → test cases. Steps reference map state/element IDs, never raw selectors. No browser access. | `agent.py`, `prompts.py`, `schemas.py` |
| `test_case_validation/` | Agent 4 — independent, stateless auditor of Test Design output. Splits deterministic `element_exists_in_map` check from LLM "is this a real duplicate" judgment. | `agent.py`, `schemas.py` |
| `automation_generation/` | Agent 5 (absorbs Automation Planning) — approved test cases → Playwright POM code. | `agent.py`, `selector_strategy.py` (role/label → data-testid → CSS → XPath priority), `page_object_templates/` |
| `automation_review/` | Agent 6 — independent code review before automation touches a real browser. Lint counts, not booleans. | `agent.py`, `lint_rules.py` (no hardcoded `sleep()`/secrets, XPath-fallback counts) |
| `test_execution/` | Agent 7 (absorbs Browser Evidence/Observability) — runs reviewed automation via Playwright, captures 5 evidence channels. **Pass/fail is a plain value comparison, never an LLM call.** `infra/llm` import is forbidden here by lint rule. | `agent.py`, `playwright_runner.py` |
| `failure_analysis/` | Agent 8 (absorbs Failure Reproduction + Defect Analysis) — deterministic pre-checks first, then `chrome-devtools-mcp` reproduction, then 11-value classification. | `agent.py`, `classification_taxonomy.py`, `reproduction.py` (`DETERMINISTIC` vs. `FLAKY_TEST`) |
| `reporting/` | Agent 9 — aggregates results/failures/defects/traceability. `metrics` (deterministic) kept structurally separate from `narrative` (LLM-authored). | `agent.py`, `schemas.py` |
| `test_data/` | Agent 10 — **cross-cutting service, not a pipeline stage.** Called by Test Design and Test Execution. Valid/invalid/boundary/dependent/cleanup data generation. | `agent.py`, `schemas.py` |
| `regression_maintenance/` | Agent 11* — thin scheduler, diffs `application_map`/`automation` versions to decide `UI_DRIFT` vs. `FUNCTIONAL_CHANGE` re-entry point. Deferred auto-triggering past MVP. | `agent.py`, `diffing.py` |

*(CLAUDE.md says "10 agents, not 17" — the numbering above (1–11) follows the
architecture doc's own agent numbering as cited in the docstrings; confirm
the exact count/roster against the architecture doc once it's added.)*

Plus:

| File | Purpose |
|---|---|
| `core/agents/base.py` | `BaseAgent` — enforces the shared input/output envelope (`schemas/envelope.py`) every agent extends. A `decisions[]` entry missing `evidence`/`source` fails validation here, before it reaches the orchestrator or a human. |

### `core/orchestrator/` — drives the pipeline state machine

| File | Purpose |
|---|---|
| `state_machine.py` | `CREATED → REQUIREMENTS_ANALYZED → APPLICATION_DISCOVERED → TEST_CASES_GENERATED → TEST_CASES_VALIDATED → AUTOMATION_GENERATED → AUTOMATION_VALIDATED → EXECUTION → FAILURE_ANALYSIS → REPORT → COMPLETED`, plus `BLOCKED` (3 exhausted rejection cycles) and re-entry from `COMPLETED` back to `APPLICATION_DISCOVERED` (regression trigger). |
| `transitions.py` | Per-transition rules: trigger, artifact passed, storage, failure/retry behavior, hard-stop conditions. |
| `retry_policy.py` | Two distinct retry concepts kept separate: infra retries (transient tool failure, exponential backoff, capped 2–3) vs. content retries (validation-rejection loop, capped 3). |

### `core/policy_safety/` — infrastructure, deliberately not an agent

(Sits next to `agents/`, not inside it, so it can't be "helpfully" refactored
into an agent and become prompt-injectable.)

| File | Purpose |
|---|---|
| `approval_gates.py` | Decides whether a given action requires human approval (requirement interpretation, validated test cases, destructive/production automation, defect filing). Consulted by the orchestrator at each transition. |
| `destructive_action_lexicon.py` | Matches element labels (delete, cancel subscription, submit payment, deactivate...) + mutation-endpoint patterns to tag elements `DESTRUCTIVE` — done once at Discovery time, not re-derived downstream. |
| `environment_policy.py` | Extra approval gates auto-apply when `environment.type == production`; governs which safety checks are active per environment. |

### `core/tool_gateway/` — the single choke point for every external tool call

No agent imports `mcp_clients/` or `infra/secrets/` directly — always
through here.

| File | Purpose |
|---|---|
| `gateway.py` | Least-privilege routing — each agent gets only the tool subset it needs, enforced here (not by prompting). |
| `secret_resolver.py` | `credential_ref` (e.g. `"cred:qa_admin"`) → real secret, resolved only at point of use. No agent/log/prompt ever sees a literal password. |
| `mcp_clients/chrome_devtools_client.py` | Wraps `chrome-devtools-mcp` for Application Discovery + Failure reproduction. Applies `--isolated`, `--allowedUrlPattern`. |
| `mcp_clients/jira_client.py` | Defect filing / ticket linking. Deferred past MVP — manual-only at MVP. |
| `mcp_clients/github_client.py` | Push-triggered regression runs, optional PR creation. Deferred past MVP. |
| `playwright_client.py` | Wraps Playwright for Test Execution only — deliberately separate from `mcp_clients/` (exploratory tools) since Execution has zero LLM-in-the-loop. |

### `core/confidence/` and `core/traceability/`

| File | Purpose |
|---|---|
| `confidence/scoring.py` | Confidence computed from evidence agreement + check determinism — **never self-reported by an LLM**. Thresholds: ≥0.90 autonomous, 0.70–0.89 flagged, <0.70 blocked. Recalibrates over time from human overrides. |
| `traceability/graph.py` | `REQ → TC → AUTO → RUN → FAIL → DEF` joins via chained foreign keys — the one place this query logic lives. |

### `domain/` — pure entities + enums, no ORM/pydantic coupling

| File | Purpose |
|---|---|
| `entities/requirement.py` | `id (REQ-ID)`, `version`, `title`, `description`, `acceptance_criteria[]`, `domain_tags[]`, `status`. |
| `entities/application_map.py` | `version`, `base_url`, `states[]` (each with `state_id`, `url_pattern`, `fingerprint`, `reached_via`, `elements[]`, `evidence_ref`), `status`. |
| `entities/test_case.py` | `id (TC-ID)`, `version`, `requirement_id`, `category`, `steps[]` (state/element refs, never selectors), `expected_result`, `not_applicable_categories[]`. |
| `entities/automation_script.py` | `id (AUTO-ID)`, `version`, `test_case_id`, `framework`, `file_path`, `selector_strategy[]`, `risk`. |
| `entities/test_run.py` | `TestRun` (id, environment_id, started/finished_at) + `TestResult` (id, automation_script_id, status, `assertion{expected,actual,source}`, `evidence{screenshot,video,trace,console_log,network_log}`). |
| `entities/failure.py` | `id (FAIL-ID)`, `test_result_id`, `classification`, `confidence`, `evidence_cited[]`, `reproduction{attempts,outcome}`. |
| `entities/defect.py` | `id (DEF-ID)`, `failure_id`, `title`, `severity_suggested`, `linked_ids[]` (REQ/TC/AUTO/RUN/FAIL chain), `status (PROPOSED|FILED)`. |
| `enums.py` | `EvidenceSource`, `FailureClassification` (11 values), `ConfidenceBand` (`HIGH ≥0.90`, `REVIEW 0.70–0.89`, `BLOCK <0.70`), `ApprovalStatus`, `OrchestratorState`, `RiskLevel` (`SAFE\|REVIEW\|DESTRUCTIVE`). |

### `schemas/` — pydantic wire format (API bodies + agent I/O envelope)

| File | Purpose |
|---|---|
| `envelope.py` | **The one and only** agent I/O envelope. Input: `agent_run_id, project_id, trigger, context_refs, payload, constraints{confidence_threshold, max_retries}`. Output: `agent_run_id, status (SUCCESS\|PARTIAL\|BLOCKED\|FAILED), artifacts[], decisions[] (decision, reason, evidence[], confidence, source), requires_human_approval, errors[]`. A `decisions[]` entry without non-empty `evidence` must fail validation. |
| `requirement.py`, `test_case.py`, `automation.py`, `execution.py`, `failure.py`, `report.py` | Request/response bodies for their respective `routers/v1/*.py` — mirror the `domain/entities/*` but are the wire format, not the domain object. |

### `infra/` — adapters to the outside world, each swappable

| File | Purpose |
|---|---|
| `db/session.py` | **Real.** Async SQLAlchemy engine + `AsyncSessionLocal` factory, sourced from `DatabaseSettings`. |
| `db/settings.py` | **Real.** `DatabaseSettings(BaseSettings)` — reads `DATABASE_URL` from project-root `.env`, env var overrides. |
| `db/models/__init__.py` | Declares `Base`; will import every model module so Alembic autogenerate sees the full schema. |
| `db/models/project.py`, `requirement.py`, `application_map.py`, `test_case.py`, `automation.py`, `execution.py`, `failure.py`, `defect.py`, `agent_run.py`, `approval.py`, `audit_log.py` | SQLAlchemy ORM tables mirroring `domain/entities/*`. `requirement`/`test_case`/`application_map`/`automation` are append-only versioned (`*_versions` tables, edits never overwrite). Large binaries (screenshots, video, trace) are referenced by object-storage key, never stored in Postgres. `agent_run` is the audit trail behind the Agent Activity screen; `audit_log` is the complementary "what did the agent actually touch" record (every MCP/tool call, minus secrets). |
| `db/repositories/base_repo.py` | Shared versioning/tenant-scoping helpers — deliberately **not** a generic CRUD-everything base. |
| `db/repositories/requirement_repo.py`, `test_case_repo.py` | One repository per aggregate root. Others (automation, execution, failure, defect, agent_run, approval) get added the same way as each milestone needs them. |
| `llm/anthropic_client.py` | **The only module that calls the Claude API.** Enforces structured tool-use output matching `schemas/envelope.py`'s `AgentOutputEnvelope`, so a malformed decision fails here, not downstream. `core/agents/test_execution/` must never import this (import-linter enforced). |
| `object_storage/s3_client.py` | S3-compatible client (MinIO locally, S3 in prod) for screenshots/video/trace/HAR — never Postgres. |
| `cache/redis_client.py` | Hot orchestrator state + rate-limiting only — not a system of record (Postgres is). |
| `queue/broker.py` | Task-dispatch abstraction for `apps/worker/tasks/*` — backing implementation depends on the Celery/`arq` decision. |
| `secrets/vault_client.py` | The actual secret store backing `secret_resolver.py` — the only module that ever touches a real secret value. |

### `alembic/`

| File | Purpose |
|---|---|
| `env.py` | **Real.** Async migration runner wired to `infra/db/models.Base` + `DatabaseSettings`. |
| `versions/` | Generated migration files — empty until Milestone 1 adds real models. |

### `tests/`

| Path | Purpose |
|---|---|
| `unit/agents/`, `unit/orchestrator/`, `unit/policy_safety/` | Unit tests mirroring `core/`'s structure. |
| `integration/api/`, `integration/db/` | API + DB integration tests. |
| `e2e/sample_apps/` | Fixture apps used to validate Discovery/Execution end-to-end (a simple CRUD app first, then a second app with meaningful auth/RBAC per `IMPLEMENTATION_PLAN.md`). |
| `conftest.py` | Shared pytest fixtures. |

### `scripts/`, `deploy/`, `.github/workflows/`

| File | Purpose |
|---|---|
| `scripts/seed_dev_db.py` | Dev DB seeding. |
| `scripts/run_migrations.sh` | Shell wrapper for `alembic upgrade head`. |
| `deploy/docker/docker-compose.yml` | postgres, redis, minio, api, worker stack. |
| `deploy/docker/Dockerfile.api`, `Dockerfile.worker` | Container builds for the two entrypoints. |
| `deploy/k8s/` | Deliberately empty — Production-phase only, not MVP scope. |
| `.github/workflows/ci.yml` | ruff → mypy → `lint-imports` (architecture boundary check) → `pytest tests/unit tests/integration` on every PR. |
| `.github/workflows/deploy.yml` | Placeholder only — deploy automation is Production-phase scope. |

---

## 4. How a request actually flows through the files

Example: `POST /requirements` (Milestone 1's vertical slice).

```
1. apps/api/routers/v1/requirements.py
     validates the request body against schemas/requirement.py
       │
2. apps/api/dependencies.py
     injects a DB session (infra/db/session.py) + repository instances
       │
3. core/agents/requirement_understanding/agent.py
     called with schemas/envelope.py's AgentInputEnvelope
       │  agent.py calls...
4. infra/llm/anthropic_client.py
     the ONLY module allowed to call the Claude API directly
       │  parses the response into...
5. core/agents/requirement_understanding/schemas.py
     structured output extending AgentOutputEnvelope
       │
6. infra/db/repositories/requirement_repo.py
     persists via infra/db/models/requirement.py — versioned, append-only
       │
7. apps/api/routers/v1/requirements.py
     maps the DB row to schemas/requirement.py, returns JSON
```

`core/orchestrator/state_machine.py` sits above steps 3–6 once more than one
agent is involved — it decides "requirement understood → trigger Application
Discovery next," etc. `core/policy_safety/approval_gates.py` gets consulted
at each transition to decide whether a human must approve before continuing.

---

## 5. Worked example: adding a new model end-to-end

Concrete example using `Requirement` (the actual next thing to build, per
Milestone 1). The files already exist as stubs — this is the fill-in order:

**1. `domain/enums.py`** — any status/category values the model needs:
```python
class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
```

**2. `domain/entities/requirement.py`** — the pure entity (no ORM/pydantic):
```python
@dataclass
class Requirement:
    id: str
    req_id: str          # e.g. "REQ-EMP-001"
    project_id: str
    version: int
    text: str
    acceptance_criteria: list[str]
    status: ApprovalStatus
```

**3. `infra/db/models/requirement.py`** — SQLAlchemy table (+ a
`requirement_versions` table for append-only versioned writes):
```python
class RequirementModel(Base):
    __tablename__ = "requirements"
    id: Mapped[str] = mapped_column(primary_key=True)
    req_id: Mapped[str]
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"))
    version: Mapped[int]
    text: Mapped[str]
    status: Mapped[str]
```
Then register it in `infra/db/models/__init__.py` so Alembic sees it.

**4. Generate + apply the migration:**
```bash
alembic revision --autogenerate -m "add requirements table"
alembic upgrade head
```

**5. `infra/db/repositories/requirement_repo.py`** — the only place that
queries this table:
```python
class RequirementRepository:
    def __init__(self, session: AsyncSession): ...
    async def create_version(self, requirement: Requirement) -> Requirement: ...
    async def get_latest(self, req_id: str) -> Requirement: ...
```
No router and no agent should import `infra/db/models/requirement.py`
directly — always through this repo.

**6. `schemas/requirement.py`** — pydantic wire format:
```python
class RequirementCreateRequest(BaseModel):
    project_id: str
    raw_text: str

class RequirementResponse(BaseModel):
    req_id: str
    version: int
    acceptance_criteria: list[str]
    status: ApprovalStatus
```

**7. `core/agents/requirement_understanding/schemas.py`** — agent-specific
I/O, extending `schemas/envelope.py`.

**8. `core/agents/requirement_understanding/agent.py`** — the actual logic,
calling `infra/llm/anthropic_client.py` and returning the envelope from
step 7.

**9. `apps/api/routers/v1/requirements.py`** — wire it to HTTP:
```python
@router.post("/requirements", response_model=RequirementResponse)
async def create_requirement(req: RequirementCreateRequest, repo=Depends(get_requirement_repo)):
    result = await requirement_understanding_agent.run(...)
    return await repo.create_version(result)
```

**10. `apps/api/dependencies.py`** — add the DI provider:
```python
def get_requirement_repo(session: AsyncSession = Depends(get_db_session)) -> RequirementRepository:
    return RequirementRepository(session)
```

**11. Tests** — `tests/unit/agents/` (agent logic), `tests/integration/db/`
(repo/migration), `tests/integration/api/` (endpoint).

### Rule of thumb: where does a new thing go?

| You're adding... | Goes in |
|---|---|
| New agent behavior (LLM reasoning) | `core/agents/<name>/agent.py` — never in a router |
| New "is this action allowed" rule | `core/policy_safety/` — never inline in an agent |
| New external tool call (browser, API, secret) | Through `core/tool_gateway/gateway.py` — never a direct `mcp_clients` import from an agent |
| New DB table | `infra/db/models/` + a matching `infra/db/repositories/` — agents/routers never touch SQLAlchemy directly |
| New HTTP-facing shape | `schemas/`, extending `schemas/envelope.py` if it's agent I/O |
| New pure business concept, no storage/wire concern yet | `domain/entities/` |
| A new *agent* entirely | Default answer is **no** — 10 agents, not 17 (`CLAUDE.md`). Confirm against architecture doc Section 5 first. |

---

## 6. See also

- `CLAUDE.md` — project context, non-negotiable architectural principles, current phase
- `docs/PROJECT_STRUCTURE.md` — *why* this layout, not just *what's in it*
- `docs/IMPLEMENTATION_PLAN.md` — phased milestones, acceptance criteria, what's explicitly excluded per milestone
- `README.md` — quick-start pointer + status
