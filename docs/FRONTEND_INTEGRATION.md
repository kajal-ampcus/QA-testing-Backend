# Frontend integration and implementation review

## Intended application

Testers provide an application URL, a test login when needed, and requirements
without needing the target application's source. The intended lifecycle is
requirements → human approval → discovery → test design → validation → automation
→ execution → failure triage → reporting. Execution is intended to be deterministic
Playwright, with no LLM deciding pass/fail.

## Architecture observed in this checkout

- `apps/api`: FastAPI transport, request validation, and endpoint orchestration.
- `apps/worker`: arq workers consume Redis jobs for application discovery.
- `core/agents`: requirement understanding, discovery, and test design logic.
- `core/tool_gateway`: restricted browser/tool access and credential resolution.
- `infra/db`: async SQLAlchemy repositories and PostgreSQL persistence; Alembic
  migrations include versioned requirements, maps, encrypted credentials, and test cases.
- `infra/llm`: provider adapters used by agents; provider keys remain server-side.
- `domain` and `schemas`: domain enums/entities and validated wire envelopes.
- S3/MinIO, orchestration, and later lifecycle agents have substantial scaffolding;
  their presence alone does not mean the workflows are operational.
- Frontend: React/TypeScript/Vite with TanStack Query, React Flow, and feature-based lazy routes. The typed client is `../frontend/src/api/client.ts`. See [frontend documentation](../../frontend/README.md).

The architecture files referenced under `docs/architecture/` are absent from
this checkout. Some phase/status statements in existing documentation are stale:
arq has been selected, migrations exist, and test-design routes are mounted.
This review uses the implementation and available structure guide as evidence.

## Connected capabilities

| User action | Backend contract |
| --- | --- |
| List/create/select application | `GET/POST /api/v1/projects` |
| Save encrypted target login | `POST /api/v1/projects/{id}/credentials` |
| Extract/list requirements | `POST/GET /api/v1/requirements/projects/{id}` |
| Revise requirement | `POST /api/v1/requirements/{id}/revisions` |
| Resolve ambiguities with version check | `POST /api/v1/requirements/{id}/clarifications` |
| List pending approvals | `GET /api/v1/approvals?project_id={id}` |
| Approve/reject with reviewer | `POST /api/v1/approvals/{id}/approve` or `/reject` |
| Queue discovery | `POST /api/v1/application-maps/projects/{id}/discover` |
| Monitor discovery | `GET /api/v1/application-maps/jobs/{id}` |
| Read saved map and coverage | `GET /api/v1/application-maps/projects/{id}` |
| Generate/list cases | `POST /api/v1/test-cases/projects/{id}/generate`, `GET /api/v1/test-cases/projects/{id}` |

The backend remains the authority for approval and version gates. Discovery
uses approved requirement IDs; test generation pins the displayed COMPLETE map.
The frontend displays real statuses and coverage, including PARTIAL results.
The graph labels connections inferred from unique matching navigation action paths; ambiguous parents stay disconnected. It does not substitute
mock records when a request fails.

Polling runs every 2.5 seconds with cleanup on project changes/unmount. Project
and pending job IDs survive reloads; credentials remain transient in the browser.
JSON errors and FastAPI validation details are surfaced to the user. Writes are
not automatically retried, since LLM and queue operations are not idempotent.

Local development uses Vite's same-origin proxy. Separate-origin deployments
use the configured API base URL and FastAPI's explicit CORS allowlist. CORS is
not authentication; platform auth/tenant enforcement remains future work.

## Remaining implementation

1. Test-case validation and its persisted approval gate.
2. Automation generation/review, deterministic execution, and their API contracts.
3. Evidence-backed failure triage, reporting, and traceability across later artifacts.
4. Real authentication, reviewer identity, and tenant scoping.
5. Background jobs for long LLM requests, idempotent submission, cancellation,
   and persisted job history beyond the current Redis result lifetime.
6. Live agent events and richer graph views derived from actual saved evidence.

The new frontend has no production demo mode. Contract fixtures exist only in frontend browser tests.
They are explicitly labeled as simulated and are not presented as live functionality.

## Verification performed

- 8 backend unit tests passed, including allowed/disallowed CORS origins.
- 6 frontend API-client tests passed.
- Strict connected-workspace TypeScript check and Vite production build passed.
- Playwright browser contract test passed against Vite with controlled HTTP
  responses: clarification/approval, credential submission, discovery reload
  recovery, and test generation. This does not replace a live LLM/crawl test.
- Vite `/health` reached the running FastAPI server and returned `status: ok`.
- The live projects endpoint could not access its database; Docker was not
  running. Database-backed integration tests and a real worker/LLM workflow
  were not verified. Temporary API/frontend smoke-test servers were stopped.
- Full frontend `lint` still reports pre-existing context/type mismatches in
  legacy demo pages; `lint:connected` passes without suppressing those errors.

The unit run exposed a crawler regression: when the authenticated phase began
on an already-recorded public page, deduplication discarded its navigation
controls. The fix preserves those controls while avoiding duplicate persistence
and page counts. The existing destructive/external navigation test now passes,
with an additional assertion on the recorded coverage count.

To repeat the browser check (requires Vite running and an installed browser):

```powershell
$env:FRONTEND_URL='http://localhost:3000'
$env:PLAYWRIGHT_CHANNEL='msedge'
.venv/Scripts/python.exe -m pytest tests/e2e/test_connected_frontend.py -q
```
