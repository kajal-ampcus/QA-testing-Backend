# Implementation Plan

Reference document for whoever (or whichever model/session) is implementing
this codebase next. Read `CLAUDE.md` and both files in `docs/architecture/`
first — this document assumes that context and doesn't repeat it.

**Rule for using this doc:** work one milestone at a time, in order. Don't
start a milestone's tasks until the previous one's acceptance criteria are
met. If a milestone's scope turns out to be wrong once you're in it, stop and
flag it rather than quietly expanding scope — update this file with the
correction, don't just drift.

---

## Status

| Phase | Status |
|---|---|
| Phase 0 — Repo & Foundations | **Mostly complete** — see below |
| Phase 1 — MVP | Not started |
| Phase 2 — V2 | Not started |
| Phase 3 — Production | Not started |

**Outstanding decisions blocking further work** (see `CLAUDE.md` "Where things
go" and `docs/PROJECT_STRUCTURE.md`'s "concrete choices to lock in"):
- Worker/queue library: Celery+Redis vs. `arq` — **blocks Milestone 3 and
  Milestone 8** (both need real background task dispatch, not just a stub).
- Package manager: `uv` vs. Poetry — doesn't block functional work, but
  should be settled before the dependency list grows much further.

---

## Phase 0 — Repo & Foundations

**Done:**
- Full directory structure per `docs/PROJECT_STRUCTURE.md`, every file present
  with a docstring citing the architecture section it will implement.
- `pyproject.toml` with working `import-linter` contracts (`core` never
  imports `apps`; `test_execution` never imports `infra/llm`) wired into CI
  (`.github/workflows/ci.yml`).
- Docker Compose stack (postgres, redis, minio, api, worker) — builds and
  runs; `api` container reaches `postgres` over the Compose network.
- A minimal *real* `infra/db/session.py` (async engine + session factory) and
  `/health`, `/health/db` endpoints, proving Postgres connectivity
  end-to-end — this was a deliberate small exception to "stubs only," scoped
  narrowly to prove the database connection works before Milestone 1 builds
  real models on top of it.
- `.env` configured for local Postgres (native or Docker).

**Not yet done (still Phase 0, do before Milestone 1):**
- Lock the two outstanding decisions above.
- `alembic/env.py` still raises `NotImplementedError` in
  `run_migrations_online()` — needs the real async engine wired in once
  `infra/db/models/` has real model definitions (this naturally happens as
  part of Milestone 1, not before).

---

## Phase 1 — MVP

Ordered by the agent sequence in architecture doc Section 8. Each milestone
is a **vertical slice** — something demonstrably working end-to-end, not "all
agents scaffolded in parallel." Do not start milestone *N+1* until milestone
*N*'s acceptance criteria pass.

### Milestone 1 — Data layer + Project/Requirement, first approval gate

**Why this is first:** it's the smallest slice that proves the
human-approval-gate pattern (Section 21) actually works end-to-end —
every later milestone reuses this exact pattern (agent proposes → stored
pending → tester approves via API → status flips), so getting it right once
here matters more than it looks.

**Scope:**
- Real SQLAlchemy models: `project`, `requirement` + `requirement_versions`,
  `approval`, `agent_run`, `audit_log` (the rest of `infra/db/models/` stays
  stubbed until the milestone that needs it).
- First real Alembic migration; `alembic/env.py` wired to the real engine.
- `infra/db/repositories/requirement_repo.py`, `base_repo.py` implemented for
  real (versioned writes per Section 23).
- `schemas/envelope.py` implemented for real — the shared agent I/O envelope
  (companion doc Part 1), including the schema-level rule that a `decisions[]`
  entry without `evidence`/`source` fails validation.
- `core/agents/requirement_understanding/` implemented for real: calls
  `infra/llm/anthropic_client.py` (also implemented for real here, for the
  first time), takes raw requirement text, returns structured
  `requirement` with `REQ-ID`, acceptance criteria, ambiguity flags.
- `apps/api/routers/v1/projects.py` and `requirements.py` implemented:
  create project, submit requirement, trigger the agent, view result.
- `apps/api/routers/v1/approvals.py` implemented: the one consistent
  approve/reject path, backed by the real `approvals` table.
- `apps/api/dependencies.py` gets a real `get_db_session` provider.

**Acceptance criteria:**
- `POST /projects` → `POST /requirements` with raw text → agent runs → `GET`
  shows a `PENDING_APPROVAL` requirement with `REQ-ID`, version 1, and at
  least one acceptance criterion tagged `source: REQUIREMENT`.
- `POST /approvals/{id}/approve` flips it to approved and is recorded in
  `approvals` with who/when.
- A requirement with genuinely ambiguous input comes back flagged
  `NEEDS_CLARIFICATION` rather than the agent guessing — test this
  deliberately with an underspecified requirement.
- `agent_runs` has a row for the run with the full input/output envelope.

**Explicitly excluded:** Application Discovery, anything browser-related,
anything past this one agent.

### Milestone 2 — Application Discovery (single sequential crawl)

**Scope:**
- `core/tool_gateway/mcp_clients/chrome_devtools_client.py` implemented for
  real, wired to the actual `chrome-devtools-mcp` server with `--isolated`
  and `--allowedUrlPattern` scoped to the target app.
- `core/agents/application_discovery/` implemented for real: `crawler.py`
  (priority-queue crawl, MVP = single shard, no sharding yet),
  `fingerprint.py` (state dedup hashing).
- `infra/db/models/application_map.py` + repository.
- `apps/api/routers/v1/application_maps.py` implemented.
- One fixture app added under `tests/e2e/sample_apps/` (simple CRUD app per
  Section 37's recommendation) to crawl against.

**Acceptance criteria:**
- Triggering Discovery against the fixture app produces a real
  `application_map` with multiple `states`, each with a `fingerprint`,
  `reached_via`, and at least one element tagged `source: OBSERVED_DOM`.
- Re-running Discovery against an unchanged app produces the same
  fingerprints (dedup actually works, not just "runs without erroring").
- A destructive-looking element (if the fixture app has one) is tagged
  `risk: DESTRUCTIVE` and was not auto-clicked during the crawl.

**Explicitly excluded:** sharding/parallel crawl (Production scope, Section
35), large-app budget tuning beyond the defaults in `.env`.

### Milestone 3 — Test Design + Test Data (service)

**Blocked on:** worker/queue library decision (Test Design itself doesn't
need it, but establishing the pattern here before Execution needs it in
Milestone 6 is the point — don't let two different ad hoc approaches appear).

**Scope:**
- `core/agents/test_design/` implemented for real: generates `test_cases`
  from `requirements` + `application_map`, steps reference
  `target_state`/`target_element` (map IDs), never raw selectors.
- `core/agents/test_data/` implemented for real as a callable service (not a
  pipeline stage) — valid/invalid/boundary data generation against
  discovered field constraints.
- `infra/db/models/test_case.py` + repository.
- `apps/api/routers/v1/test_cases.py` implemented.

**Acceptance criteria:**
- Given the Milestone 1 requirement and Milestone 2 map, produces test cases
  covering at minimum positive + negative cases, each linked to the
  `REQ-ID`.
- At least one category is explicitly marked `not_applicable` with a reason
  (not silently omitted) if the fixture app doesn't trigger it.
- Test Data Agent, called mid-generation, returns data satisfying a real
  discovered field constraint (e.g., an email format constraint actually
  observed in the map).

### Milestone 4 — Test Case Validation (+ first rejection-loop test)

**Scope:**
- `core/agents/test_case_validation/` implemented for real: deterministic
  `element_exists_in_map` check + LLM judgment for duplicates, verdicts with
  `checks{}` kept structurally separate from `confidence`.
- Rejection loop wired into the orchestrator (`core/orchestrator/`
  implemented for real for the first time, at least the
  `TEST_CASES_GENERATED ⇄ TEST_CASES_VALIDATED` portion of the state
  machine), capped at 3 cycles, escalates to human after.
- Human-approval gate on the final validated set (reuses the Milestone 1
  approval pattern).

**Acceptance criteria:**
- A deliberately broken test case (referencing a non-existent element)
  is rejected with a `checks.element_exists_in_map: false` and the
  regeneration loop actually re-invokes Test Design.
- After 3 failed cycles, it escalates rather than looping forever — test
  this deliberately.
- Approving the final set is a distinct, auditable action in `approvals`.

### Milestone 5 — Automation Generation + Review

**Scope:**
- `core/agents/automation_generation/`: `selector_strategy.py` (real
  priority order) + real Playwright code generation for the approved cases.
- `core/agents/automation_review/`: `lint_rules.py` real (no hardcoded
  waits/secrets, XPath-fallback counts).
- `apps/api/routers/v1/automation.py` implemented.
- Human-approval gate wired for any script the Policy/Safety layer
  (`core/policy_safety/destructive_action_lexicon.py`, implemented for real
  here) flags as touching a `DESTRUCTIVE` element.

**Acceptance criteria:**
- Generated scripts actually run under `playwright test` locally without
  syntax errors (a real smoke test, not just "the LLM produced text").
- A script touching a `DESTRUCTIVE`-tagged element is blocked pending human
  approval — test this deliberately if the fixture app has one, or add a
  second fixture app that does.
- Review's lint counts are non-zero and accurate on a deliberately bad
  script (inject a hardcoded `sleep()` and confirm it's caught).

### Milestone 6 — Test Execution (zero LLM-in-the-loop, for real)

**Blocked on:** worker/queue library decision — this is the milestone that
actually needs `apps/worker/tasks/run_execution.py` to be real.

**Scope:**
- `core/tool_gateway/playwright_client.py` implemented for real.
- `core/agents/test_execution/playwright_runner.py` implemented for real —
  runs the compiled suite, captures all five evidence channels concurrently.
- `infra/object_storage/s3_client.py` implemented for real (evidence goes to
  MinIO/S3, not Postgres).
- `infra/db/models/execution.py` + repository.
- `apps/api/routers/v1/executions.py` implemented.
- **CI check**: confirm the `import-linter` contract actually fires if you
  temporarily add an `infra.llm` import to `test_execution/agent.py` — prove
  the guardrail works, then remove the test import.

**Acceptance criteria:**
- Running the Milestone 5 scripts against the fixture app produces real
  `test_results` with `status` derived purely from `assertion.expected` vs.
  `actual` — no LLM call anywhere in this path, verify by checking there's
  no `agent_run` entry with an LLM cost for this step.
- All five evidence channels (screenshot, video, trace, console_log,
  network_log) are present in object storage and referenced correctly from
  `test_results`.
- Deliberately break the fixture app (or use a case designed to fail) and
  confirm a real `FAILED` result with real evidence.

### Milestone 7 — Failure Analysis & Defect (classification + reproduction)

**Scope:**
- `core/agents/failure_analysis/classification_taxonomy.py`: the 11-value
  enum + deterministic pre-checks (HTTP status, accessibility-tree match for
  selector drift) implemented for real.
- `reproduction.py`: real `chrome-devtools-mcp` reproduction (clean session,
  1-3 attempts, `DETERMINISTIC` vs. flagged for `FLAKY_TEST`).
- `infra/db/models/failure.py`, `defect.py` + repositories.
- `apps/api/routers/v1/failures.py`, `defects.py` implemented.
- Defect filing is **manual-only** at MVP (Section 34) — agent proposes
  text, no Jira MCP integration yet.

**Acceptance criteria:**
- The Milestone 6 failure gets classified with a cited evidence pointer
  (e.g., `network_log: POST ... -> 201 (expected 4xx)`), never an
  unsupported classification — verify the schema actually rejects a
  classification with an empty `evidence_cited`.
- Reproduction runs against a clean session and produces a real
  `DETERMINISTIC` or `FLAKY_TEST` verdict, not a hardcoded value.
- A deliberately ambiguous failure (contradictory evidence, if you can
  construct one) comes back `UNKNOWN` rather than a forced guess.
- Defect proposal requires explicit approval before being marked `FILED`
  (still just a status flip at MVP, no real Jira call).

### Milestone 8 — Reporting + full traceability

**Scope:**
- `core/agents/reporting/`: `metrics` (deterministic) kept structurally
  separate from `narrative` (LLM-authored).
- `core/traceability/graph.py` implemented for real: the
  `REQ → TC → AUTO → RUN → FAIL → DEF` join chain.
- `apps/api/routers/v1/reports.py`, `agent_activity.py` implemented.
- `apps/api/websockets/agent_events.py`: live orchestrator transition stream
  (this is the first genuinely "nice to have" piece — acceptable to do a
  simplified polling version first and upgrade to real WebSocket push later
  if time-constrained).

**Acceptance criteria:**
- A single API call against the Milestone 7 requirement/failure returns the
  full `REQ-EMP-001 → TC-EMP-003 → AUTO-EMP-003 → RUN-... → FAIL-... →
  DEF-...` chain in one response — this is the Section 31 end-to-end example
  from the architecture doc, actually working, not just documented.
- Every number in a generated report's `narrative` traces back to a value in
  its own `metrics` block — spot-check this manually on the first report.

**Phase 1 is done when:** Milestones 1–8 all pass their acceptance criteria
against at least one real fixture app, and a second fixture app with
meaningful auth/RBAC complexity (Section 37's recommendation) has been run
through the same pipeline at least once without needing pipeline code
changes — if it does need changes, that's a sign a milestone's "done" was
premature, not a reason to skip the second app.

---

## Phase 2 — V2 Additions

Not milestone-broken in detail yet — do that once Phase 1 is actually
complete and real usage has shown which of these matters most. In rough
priority order per the architecture doc:

- Selector Healing automation (Section 19) — validated dry-run + proposed
  diff, human approval by default.
- Flaky-test statistics (Section 18) — rolling result-history, not
  single-run judgments.
- Jira/GitHub MCP-integrated defect filing (replaces Milestone 7's
  manual-only flow).
- Regression & Maintenance Agent auto-triggering (Section 6 Agent 11) —
  webhook/schedule-driven re-entry, replacing MVP's manual re-run.
- Parallel/sharded Application Discovery for large applications (Section 9's
  scaling subsection) — only worth building once a real target app is
  actually too large for Milestone 2's single-sequential-crawl approach.

## Phase 3 — Production Hardening

Also not milestone-broken yet — this is explicitly post-MVP, post-V2 scope
(Section 35): durable orchestrator state machine (Temporal or equivalent),
parallel sharded Playwright execution, multi-tenant isolation enforced at
the Tool Gateway and row level, full CI/CD auto-deploy
(`.github/workflows/deploy.yml` is currently a stub for exactly this),
complete audit/compliance tooling, horizontal scaling of API/worker
independently.

---

## What "done" means at every milestone

Borrowed from the working agreement in `CLAUDE.md` — repeated here because
it's the rule most likely to get skipped under time pressure: a milestone
isn't done when the code runs without erroring, it's done when its specific
acceptance criteria above are verified, including the deliberately-broken-
input tests (ambiguous requirement, non-existent element reference, 3
exhausted rejection cycles, destructive-action block, empty evidence_cited).
Those negative-path tests are not optional polish — they're the actual proof
that "accuracy over autonomy" is real in this codebase, not just a line in
the architecture doc.

### Alembic setup update

The async migration environment, shared declarative Base, revision template,
and shared DATABASE_URL loading are now implemented. This supersedes the
Phase 0 note about `run_migrations_online()` raising `NotImplementedError`.
Business models and the first schema migration remain Milestone 1 work.
Offline SQL generation was verified; live database verification currently
fails because PostgreSQL rejects the configured qa_platform password.
