# Project Folder Structure — FastAPI

This is a **clean/hexagonal-style layout**, not a flat `routers/models/schemas` dump. The reason is structural, not stylistic: this system has business logic (agents, orchestrator, policy) that must be callable from three different places — the HTTP API, a background worker, and tests — without dragging FastAPI along with it. A flat layout couples business logic to the web framework and makes exactly the kind of "no LLM in Test Execution's decision path" guarantee (architecture doc, Section 14) hard to enforce, because there's nothing stopping a route handler from reaching in and doing agent work inline. Every directory below exists to make one architectural rule structurally hard to violate, not just documented.

```
qa-platform/
├── apps/
│   ├── api/                              # FastAPI HTTP layer — thin, no business logic
│   │   ├── main.py                       # app factory, router mounting, startup/shutdown
│   │   ├── dependencies.py               # DI: db session, current_user, tool_gateway handle
│   │   ├── middleware/
│   │   │   ├── tenant_scope.py           # injects project_id scope on every request (Sec 28)
│   │   │   ├── request_logging.py
│   │   │   └── error_handling.py
│   │   ├── routers/v1/
│   │   │   ├── projects.py
│   │   │   ├── requirements.py
│   │   │   ├── application_maps.py
│   │   │   ├── test_cases.py
│   │   │   ├── automation.py
│   │   │   ├── executions.py
│   │   │   ├── failures.py
│   │   │   ├── defects.py
│   │   │   ├── reports.py
│   │   │   ├── approvals.py              # human-approval-gate endpoints (Sec 21)
│   │   │   └── agent_activity.py         # feeds the "Agent Activity" transparency screen (Sec 27)
│   │   └── websockets/
│   │       └── agent_events.py           # live orchestrator state-transition stream (Sec 27/28)
│   │
│   └── worker/                           # background job runner — same core/, different entrypoint
│       ├── celery_app.py                 # or arq_app.py — pick one, see note below
│       └── tasks/
│           ├── run_discovery.py
│           ├── run_execution.py
│           └── run_regression_trigger.py
│
├── core/                                 # domain logic — imports nothing from apps/, framework-agnostic
│   ├── orchestrator/
│   │   ├── state_machine.py              # the CREATED → ... → COMPLETED states (Sec 25)
│   │   ├── transitions.py
│   │   └── retry_policy.py               # infra retries vs. content-retry-loop, kept separate (Sec 25)
│   │
│   ├── agents/                           # one folder per agent — never merge two agents' code
│   │   ├── base.py                       # BaseAgent: enforces the shared envelope in/out (I/O doc Part 1)
│   │   ├── requirement_understanding/{agent.py, prompts.py, schemas.py}
│   │   ├── application_discovery/
│   │   │   ├── agent.py
│   │   │   ├── crawler.py                # priority-queue + sharding logic (Sec 9 scaling subsection)
│   │   │   ├── fingerprint.py            # state-dedup hashing — the merge key across shards
│   │   │   ├── prompts.py
│   │   │   └── schemas.py
│   │   ├── test_design/{agent.py, prompts.py, schemas.py}
│   │   ├── test_case_validation/{agent.py, schemas.py}
│   │   ├── automation_generation/
│   │   │   ├── agent.py
│   │   │   ├── page_object_templates/
│   │   │   └── selector_strategy.py      # role/label → data-testid → CSS → XPath priority (Sec 13)
│   │   ├── automation_review/{agent.py, lint_rules.py}
│   │   ├── test_execution/
│   │   │   ├── agent.py                  # NO llm/ import allowed here — enforced by lint rule, see below
│   │   │   └── playwright_runner.py
│   │   ├── failure_analysis/
│   │   │   ├── agent.py
│   │   │   ├── classification_taxonomy.py  # the 11-value enum + deterministic pre-checks (Sec 16)
│   │   │   └── reproduction.py
│   │   ├── reporting/{agent.py, schemas.py}
│   │   ├── test_data/{agent.py, schemas.py}          # cross-cutting service, not a pipeline stage
│   │   └── regression_maintenance/{agent.py, diffing.py}
│   │
│   ├── tool_gateway/                     # THE single choke point for every external tool call
│   │   ├── gateway.py                    # least-privilege routing — Sec 11's table lives here as code
│   │   ├── secret_resolver.py            # credential_ref → real secret, resolved only here (Sec 20/28)
│   │   ├── mcp_clients/
│   │   │   ├── chrome_devtools_client.py # wraps chrome-devtools-mcp, applies --isolated etc.
│   │   │   ├── jira_client.py
│   │   │   └── github_client.py
│   │   └── playwright_client.py
│   │
│   ├── policy_safety/                    # deliberately NOT under agents/ — it's infrastructure (Sec 5, 29)
│   │   ├── destructive_action_lexicon.py
│   │   ├── approval_gates.py
│   │   └── environment_policy.py
│   │
│   ├── confidence/
│   │   └── scoring.py                    # computed from evidence agreement, never self-reported (Sec 22)
│   │
│   └── traceability/
│       └── graph.py                      # REQ→TC→AUTO→RUN→FAIL→DEF joins, one place (Sec 8/26)
│
├── domain/                               # pure entities + enums, no FastAPI/SQLAlchemy imports
│   ├── entities/
│   │   ├── requirement.py
│   │   ├── application_map.py
│   │   ├── test_case.py
│   │   ├── automation_script.py
│   │   ├── test_run.py
│   │   ├── failure.py
│   │   └── defect.py
│   └── enums.py                          # FailureClassification, EvidenceSource, ConfidenceBand...
│
├── schemas/                              # pydantic — the wire format, shared by API and agent envelope
│   ├── envelope.py                       # the common input/output envelope (I/O doc Part 1) — ONE definition
│   ├── requirement.py
│   ├── test_case.py
│   ├── automation.py
│   ├── execution.py
│   ├── failure.py
│   └── report.py
│
├── infra/                                # adapters — swappable, isolated from core/ behind interfaces
│   ├── db/
│   │   ├── session.py
│   │   ├── models/                       # SQLAlchemy ORM, mirrors Sec 26 exactly
│   │   │   ├── project.py, requirement.py, application_map.py, test_case.py,
│   │   │   └── automation.py, execution.py, failure.py, defect.py, agent_run.py, approval.py, audit_log.py
│   │   └── repositories/                 # one repo per aggregate root — no generic CRUD-everything base
│   │       ├── requirement_repo.py
│   │       ├── test_case_repo.py
│   │       └── ...
│   ├── object_storage/
│   │   └── s3_client.py                  # screenshots/video/trace/HAR (Sec 23) — never in Postgres
│   ├── cache/redis_client.py
│   ├── queue/broker.py
│   ├── secrets/vault_client.py
│   └── llm/anthropic_client.py           # the ONLY place that calls the Claude API directly
│
├── alembic/
│   ├── env.py
│   └── versions/
│
├── tests/
│   ├── unit/{agents/, orchestrator/, policy_safety/}
│   ├── integration/{api/, db/}
│   └── e2e/
│       └── sample_apps/                  # fixture apps to validate Discovery/Execution end-to-end (Sec 37 #1)
│
├── scripts/
│   ├── seed_dev_db.py
│   └── run_migrations.sh
│
├── docs/
│   ├── architecture/
│   │   ├── agentic-qa-platform-architecture.md
│   │   └── agentic-qa-platform-io-contracts-and-devtools.md
│   └── IMPLEMENTATION_PLAN.md
│
├── deploy/
│   ├── docker/{Dockerfile.api, Dockerfile.worker, docker-compose.yml}
│   └── k8s/                              # empty/stubbed at MVP — Production-phase only (Sec 35)
│
├── .github/workflows/{ci.yml, deploy.yml}
├── CLAUDE.md
├── pyproject.toml
├── alembic.ini
├── .env.example
├── Makefile
└── README.md
```

## The points worth remembering — why this shape and not a simpler one

1. **`core/` never imports from `apps/`.** This is the single rule that makes everything else work. Agent logic, the orchestrator, and the policy layer are plain Python callable from a route handler, a Celery task, or a pytest test with equal ease. If you ever find yourself importing `fastapi` inside `core/agents/`, that's the signal something's been placed wrong.

2. **`apps/api` and `apps/worker` are two entrypoints into the same `core/`, not two codebases.** The API enqueues work and serves state; the worker actually runs Discovery crawls and Execution runs. This isn't optional plumbing — a Discovery crawl or a full test run can take minutes, and nothing that long belongs inside an HTTP request/response cycle. This is also where the Section 9 sharded-crawl scaling lives operationally: each shard is a worker task, fanned out by the orchestrator, not a loop inside one request handler.

3. **One folder per agent under `core/agents/`, and never two agents' logic in one file.** This physically enforces the Section 5 boundary decisions — e.g., Test Execution's folder has no business reaching into `infra/llm/`, because Execution is supposed to have zero LLM-in-the-loop. Worth actually wiring a lint rule (ruff/import-linter) that fails CI if `agents/test_execution/` imports anything from `infra/llm/` — turn the architectural rule into something the build breaks on, not just something a docstring asks nicely for.

4. **`tool_gateway/` is the only place that talks to `chrome-devtools-mcp`, Playwright, or resolves a secret.** No agent module should import `mcp_clients/` or `secrets/` directly — it goes through `gateway.py`. This is Section 11's least-privilege table made real: the gateway is where you'd add a check like "does `test_design` have browser access" and have it actually mean something, because there's exactly one door.

5. **`policy_safety/` sits next to `agents/`, not inside it — on purpose.** We decided in the architecture (Section 5) that Safety/Policy is infrastructure, not an autonomous agent, specifically so it can't be prompt-injected around. Keeping it in its own top-level folder under `core/` (not nested under `agents/`) is a small thing that prevents someone six months from now from "helpfully" refactoring it into "just another agent."

6. **`domain/entities/` has no ORM or pydantic coupling.** `infra/db/models/` (SQLAlchemy) and `schemas/` (pydantic/API) are both *representations* of a domain entity, not the entity itself. This is what keeps a database migration from forcing an API contract change, or an API versioning need from forcing a schema migration — the two evolve independently.

7. **`schemas/envelope.py` is the one and only definition of the shared agent I/O envelope** (companion doc, Part 1). Every per-agent `schemas.py` extends it rather than redefining `agent_run_id`/`status`/`decisions[].evidence` from scratch — this is what makes the orchestrator's routing logic generic instead of needing an `if` branch per agent.

8. **`infra/` holds every adapter to the outside world, each swappable.** Object storage, cache, queue, secrets, and the LLM client are each one file/module with a narrow interface — this is what lets you swap S3 for MinIO locally, or Vault for a cloud KMS in production, without touching `core/`.

9. **`tests/e2e/sample_apps/`** exists because Section 37's first recommended next step is validating the architecture against real target applications before writing more code — having a place for fixture apps from day one means that validation is a normal part of the test suite, not a one-off manual exercise that never gets repeated.

10. **`deploy/k8s/` is present but deliberately empty at MVP.** Section 34 vs. 35 draws a hard line between MVP and Production scope — having the folder exist as a stub is a reminder of where multi-tenant/parallel-execution deployment work eventually goes, without letting anyone start filling it in before Phase 1 is actually done.

## A few concrete choices to lock in (Claude Code will otherwise ask, or worse, assume)

- **Worker/queue library:** Celery+Redis is the safe, boring, well-documented choice; `arq` (async-native, pairs naturally with FastAPI's async style) is the leaner alternative if you want everything async end-to-end. Pick one before Phase 0 — don't let both show up in the codebase.
- **Package manager:** `uv` or Poetry — either is fine, but pick one and put it in `pyproject.toml` before anyone runs `pip install` ad hoc.
- **Lint enforcement of the agent boundaries:** worth setting up `import-linter` (or a ruff rule) in CI from Phase 0, not retrofitted later — it's cheap now and is exactly the kind of "architecture decays silently" problem that's expensive to fix after 10 agents exist.
- **`alembic/` at repo root**, not nested under `infra/db/` — this is the standard Alembic convention and saves fighting `alembic.ini`'s path resolution for no benefit.

This structure is meant to go into `CLAUDE.md` under "Where things go" once you confirm it — right now that section still has `[fill in once decided]` placeholders from the kickoff prompt.
