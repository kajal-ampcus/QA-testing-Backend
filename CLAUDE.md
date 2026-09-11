# Agentic AI Automation Testing Platform — Project Context

This file is read automatically at the start of every Claude Code session in this repo. Keep it short and durable — detailed design lives in `/docs/architecture/`, not here.

## What this project is

A multi-agent platform that automates the QA testing lifecycle (requirements → test design → automation → execution → failure triage → reporting) for testers who do **not** have source-code access to the application under test. Full design: `/docs/architecture/agentic-qa-platform-architecture.md` and `/docs/architecture/agentic-qa-platform-io-contracts-and-devtools.md`. **Read both in full before proposing or changing any implementation.**

## Non-negotiable architectural principles

- **Accuracy over autonomy.** Deterministic checks (HTTP status, DOM lookups, assertion results) run before any LLM judgment call. An LLM only reasons over evidence it's handed — never regenerates facts from memory.
- **Every agent decision needs a cited source.** `REQUIREMENT | OBSERVED_DOM | OBSERVED_NETWORK | OBSERVED_CONSOLE | TEST_EXECUTION | INFERENCE | UNKNOWN`. `UNKNOWN` is a valid, expected output — never force a guess into a named category.
- **Test Execution has zero LLM-in-the-loop.** It runs compiled Playwright and compares machine-readable values. Pass/fail is never an LLM judgment.
- **Discovery and Failure-Reproduction use `chrome-devtools-mcp`** (agent picks actions turn-by-turn). **Execution uses Playwright directly** (deterministic, compiled, no agent). Do not blur this line.
- **Human approval gates are structural, not optional UI polish**: requirement interpretation, validated test cases, destructive/production automation, defect filing. See architecture doc Section 21.
- **10 agents, not 17** — see architecture doc Section 5 before proposing a new agent; the default answer to "should this be a new agent" is no unless it has independent decision-making value.
- Current phase: **Phase 0 nearly complete (repo scaffolding + Postgres connectivity verified). Next up: Phase 1, Milestone 1 (data layer + Requirement Understanding Agent + first approval gate) — see `docs/IMPLEMENTATION_PLAN.md`.**

## Where things go

- `/docs/architecture/` — source-of-truth design docs (do not restate their content elsewhere; link to them)
- `/docs/PROJECT_STRUCTURE.md` — the FastAPI folder layout and the rationale behind each directory boundary. Read this before creating any new file or folder — most placement decisions are already made there.
- `/docs/IMPLEMENTATION_PLAN.md` — the phased build plan, broken into concrete milestones with acceptance criteria. **Read this before writing any agent logic** — it says exactly what's in scope for the current milestone and what isn't.
- Everything else — standard structure per `docs/PROJECT_STRUCTURE.md`:
  - Backend: **FastAPI** (`apps/api`) + a separate worker process (`apps/worker`) for long-running agent jobs
  - `core/` — framework-agnostic domain logic (orchestrator, agents, tool_gateway, policy_safety); never imports from `apps/`
  - `core/agents/test_execution/` must never import anything from `infra/llm/` — this is the structural enforcement of "zero LLM-in-the-loop" above
  - Worker/queue library: `[fill in once decided — Celery+Redis vs. arq]`
  - Package manager: `[fill in once decided — uv vs. Poetry]`

## Working agreement

- Work **one phase at a time** from `/docs/IMPLEMENTATION_PLAN.md`. Don't scaffold Phase 3 work while on Phase 1.
- Before writing code for a new phase, restate the phase's scope and acceptance criteria and confirm before proceeding.
- Never invent agent behavior not in the architecture doc — if something's ambiguous, flag it rather than deciding silently.
