# Test-case generation: walkthrough and file map

This guide follows the current backend implementation from the Generate action
to saved draft test cases. It is intended as a code-navigation guide; the
[generation design notes](./TEST_CASE_GENERATION.md) describe design decisions
and known limits in more detail.

## What this pipeline does

Test-case generation turns an **approved requirement** and an observed
**application map** into evidence-backed test-case drafts. It does not run the
tests. Cases that are saved still require review and approval before later
automation or execution.

The main backend entry point is:

```text
POST /api/v1/test-cases/projects/{project_id}/generate
```

The request includes a `requirement_id`. It can also pin an
`application_map_id`, select a generation scope or categories, and request
regeneration. If no map is supplied, the backend uses the latest map that is
usable for test design.

## Step-by-step flow

1. **Discovery records evidence.** The discovery crawler visits the application
   and stores observed states, URLs, navigation paths, DOM controls, and
   available element constraints in an application map. Generation requires a
   `COMPLETE` or `PARTIAL` map with at least one state. It does not invent
   controls that discovery did not observe.

2. **The UI sends the request.** The Test Cases screen calls the frontend API
   client, which posts the requirement ID, selected map, generation scope, and
   any selected states/areas/modules or requested categories to the backend
   route above.

3. **The API prepares the agent input.** The route creates the requirement,
   application-map, and test-case repositories, builds an `AgentInputEnvelope`,
   and invokes `TestDesignAgent.run()`. Invalid project, requirement, or map
   state is rejected before cases are written.

4. **The agent checks its inputs.** It verifies that the requirement exists, is
   `APPROVED`, belongs to the requested project, and has a usable application
   map. It loads the requirement's current version and its acceptance criteria
   (ACs).

5. **The selected scope is applied.** The request may scope generation to all
   states, states not previously generated for that requirement, or selected
   areas/modules/branches/state codes. The selected states constrain where a
   case may start; other observed map states remain available as evidence for
   observed transitions and results.

6. **Requirements are converted to scenario obligations.** The scenario
   planner splits AC text into independently testable clauses and matches those
   clauses against relevant observed pages and controls. Explicit rules in the
   requirement description supplement an AC only when an observed named field
   supports the relationship. Unsupported outcomes remain visible as coverage
   gaps instead of being turned into guessed tests.

7. **Cases are drafted from observed behavior.** The planner combines
   `scenarios.py` with helper logic in `fast_generate.py` and
   `form_behaviors.py`. Depending on the requirement and map, it can create
   navigation/action/result cases, visible-control checks, and supported form
   checks such as required-input or native constraint boundaries. Cases use
   `STATE-*` and `EL-*` map references, structured actions/assertions, test
   data, preconditions, and AC traceability.

8. **An optional LLM may fill eligible gaps.** Deterministic generation is the
   default. Setting `TEST_DESIGN_USE_LLM=true` enables structured LLM requests
   for missing scenario obligations. The prompt and Pydantic schema constrain
   the response; each model candidate is checked for evidence, requested
   scenario relevance, and scope before it can be saved. LLM failure or an
   unsupported scenario is reported as a gap/warning, not a passing case.

9. **Every candidate is validated.** The Test Design agent calls
   `TestCaseValidationAgent.validate(..., strict=True)` for both deterministic
   and LLM candidates. The validator delegates detailed checks to
   `test_design/validation.py` and then returns issues, summary checks, and a
   deterministic confidence score. Invalid candidates are reported and not
   persisted.

10. **Coverage and duplicates are measured.** The agent checks saved cases
    against the planned scenario behaviors, not just AC ID tags. Duplicate
    behavior is identified using normalized state/control/action/data/assertion
    identity. An identical case can be skipped while its traceability is
    merged into the existing case.

11. **Valid new cases are saved as drafts.** The repository creates a stable
    `TestCase` row and an append-only `TestCaseVersion`, associated with the
    requirement version and application map. Newly generated cases start in
    `DRAFT`. The response includes created cases, measured design coverage,
    gaps, validation issues, duplicate counts, and token usage where relevant.
    A covered design scenario does not mean that the application has passed
    execution.

## The tagged validation agent, analyzed

The tagged file is
[`core/agents/test_case_validation/agent.py`](../core/agents/test_case_validation/agent.py).
It is the auditor used by Test Design; it is **not** the test-case generator.

- `TestCaseValidationAgent` is a `BaseAgent[ValidationVerdict]` named
  `test_case_validation`.
- `issues_for(...)` is a convenience wrapper that returns only the issue list
  from `validate(...)`.
- `validate(...)` calls `test_design.validation.validate_case(...)` for the
  detailed checks. It then independently summarizes whether the case has
  steps, ends with an assertion, references states/elements present in the map,
  uses known AC IDs, and has no manual assertion oracle.
- `passed` is determined by whether the detailed validator returned issues.
  The `checks` dictionary is useful diagnostic context, but it does not replace
  the detailed issue list.
- Confidence is calculated locally with `compute_confidence`; the case's
  self-reported confidence is not used. Inputs include the number of steps and
  traceability references, the fraction of summary checks that passed, and the
  share of manual assertions. Confidence is a design-evidence score, not a
  statement that the test ran or passed.
- `run(...)` adapts an `AgentInputEnvelope`: it reads `test_case`, optional
  `states`, `ac_ids`, and `ac_texts` from its payload, validates the case
  schema, and returns a `ValidationVerdict`.

**Strictness detail:** the normal Test Design flow explicitly calls
`validate(..., strict=True)` and supplies requirement assertion sources. The
standalone `run(...)` method calls `validate(...)` without overriding its
defaults, so it uses `strict=False` and does not supply assertion sources.
Callers should not assume those two entry paths apply identical strict checks.

## Files involved

The following is the practical file inventory for tracing generation. It
includes the direct UI/API path, core generation and validation logic, evidence
and persistence dependencies, and focused regression tests. Generic framework
and database-driver internals are intentionally excluded.

### UI and API entry points

| File | Role |
|---|---|
| [`QA-testing-Frontend/src/features/test-cases/TestCases.tsx`](../../QA-testing-Frontend/src/features/test-cases/TestCases.tsx) | Generation controls, scope selection, and display of the generation result. |
| [`QA-testing-Frontend/src/api/client.ts`](../../QA-testing-Frontend/src/api/client.ts) | Sends the generation request to the backend. |
| [`apps/api/routers/v1/test_cases.py`](../apps/api/routers/v1/test_cases.py) | Defines the generation request/response and `POST .../generate` route; creates the agent and records the run. |

### Core generation and validation

| File | Role |
|---|---|
| [`core/agents/test_design/agent.py`](../core/agents/test_design/agent.py) | Orchestrates input checks, scope, scenario planning, optional LLM work, validation, coverage, duplicate handling, persistence, and response construction. |
| [`core/agents/test_design/scenarios.py`](../core/agents/test_design/scenarios.py) | Derives scenario obligations from ACs and observed states; binds saved cases and measures design coverage. |
| [`core/agents/test_design/fast_generate.py`](../core/agents/test_design/fast_generate.py) | Map-grounded case-building helpers, observed element selection, actions, assertions, and test-data values used by the scenario planner. |
| [`core/agents/test_design/form_behaviors.py`](../core/agents/test_design/form_behaviors.py) | Derives evidence-backed form interaction drafts, including explicitly marked manual checks where automation evidence is absent. |
| [`core/agents/test_design/prompts.py`](../core/agents/test_design/prompts.py) | System and user prompts for optional LLM-generated candidates. |
| [`core/agents/test_design/schemas.py`](../core/agents/test_design/schemas.py) | Pydantic contracts for test cases, steps, targets, batches, and generation results. |
| [`core/agents/test_design/validation.py`](../core/agents/test_design/validation.py) | Deterministic case checks, including map references, step order, data placeholders, assertions, and strict evidence rules. |
| [`core/agents/test_case_validation/agent.py`](../core/agents/test_case_validation/agent.py) | Tagged validation agent: wraps detailed validation, summarizes checks, and computes confidence. |
| [`core/agents/test_case_validation/schemas.py`](../core/agents/test_case_validation/schemas.py) | `ValidationVerdict` output contract. |

### Evidence, data, and persistence dependencies

| File | Role |
|---|---|
| [`core/agents/application_discovery/agent.py`](../core/agents/application_discovery/agent.py) | Orchestrates discovery and persists observed application-map evidence. |
| [`core/agents/application_discovery/parallel_crawler.py`](../core/agents/application_discovery/parallel_crawler.py) | Crawls application paths and records transitions used by generation. |
| [`core/agents/application_discovery/crawler.py`](../core/agents/application_discovery/crawler.py) | Browser crawl behavior that discovers states and controls. |
| [`core/agents/application_discovery/dropdowns.py`](../core/agents/application_discovery/dropdowns.py) | Observed dropdown options and behavior helpers consumed by case generation. |
| [`core/agents/test_data/agent.py`](../core/agents/test_data/agent.py) | Supplies element-aware test-data values used by generation helpers. |
| [`infra/db/repositories/requirement_repo.py`](../infra/db/repositories/requirement_repo.py) | Loads the requirement and current approved version. |
| [`infra/db/repositories/application_map_repo.py`](../infra/db/repositories/application_map_repo.py) | Loads map states and tracks generation coverage by discovered fingerprint. |
| [`infra/db/repositories/test_case_repo.py`](../infra/db/repositories/test_case_repo.py) | Creates cases/versions, checks duplicates, locks generation, and handles regeneration. |
| [`infra/db/models/application_map.py`](../infra/db/models/application_map.py) | Database models for the map and its discovered states. |
| [`infra/db/models/test_case.py`](../infra/db/models/test_case.py) | Database models for stable test cases and append-only case versions. |
| [`domain/test_case_identity.py`](../domain/test_case_identity.py) | Shared behavior signature used for semantic duplicate checks and cross-map identity. |
| [`core/confidence/scoring.py`](../core/confidence/scoring.py) | Deterministic confidence calculation used by the validation agent. |
| [`schemas/envelope.py`](../schemas/envelope.py) | Common input/output envelope and artifact contracts for agent runs. |

### Optional model and screenshot path

| File | Role |
|---|---|
| [`infra/llm/base.py`](../infra/llm/base.py) | Structured LLM client interface and result contract. |
| [`infra/llm/factory.py`](../infra/llm/factory.py) | Selects the configured provider and model. |
| [`infra/llm/anthropic_client.py`](../infra/llm/anthropic_client.py) | Anthropic structured-output implementation. |
| [`infra/llm/openai_compatible_client.py`](../infra/llm/openai_compatible_client.py) | OpenAI-compatible provider structured-output implementation. |
| [`infra/llm/evidence_images.py`](../infra/llm/evidence_images.py) | Loads screenshot evidence for optional vision analysis. |

The primary runtime flag is `TEST_DESIGN_USE_LLM`; it is off unless enabled.
Provider configuration is handled by the shared `LLM_PROVIDER`, `LLM_MODEL`,
and provider-specific environment variables. Screenshot analysis is
supplementary evidence, not a replacement for observed DOM controls.

### Focused tests

| File | What it exercises |
|---|---|
| [`tests/unit/agents/test_case_validation_agent.py`](../tests/unit/agents/test_case_validation_agent.py) | Validation verdict and map-reference checks. |
| [`tests/unit/agents/test_design_validation.py`](../tests/unit/agents/test_design_validation.py) | Detailed case validation rules. |
| [`tests/unit/agents/test_fast_generate.py`](../tests/unit/agents/test_fast_generate.py) | Map-grounded case-generation behavior and evidence constraints. |
| [`tests/unit/agents/test_partial_generation.py`](../tests/unit/agents/test_partial_generation.py) | Partial generation and unsupported scenarios. |
| [`tests/unit/agents/test_test_design_batching.py`](../tests/unit/agents/test_test_design_batching.py) | Optional LLM task batching and missing-scenario targeting. |
| [`tests/unit/agents/test_test_design_scope.py`](../tests/unit/agents/test_test_design_scope.py) | Generation scope selection and selected-state behavior. |

## Important distinction

This pipeline creates **test designs**. A saved case is not evidence that the
application behaves as expected: it may be a manual-verification draft, and
even an automated-ready case has not been run merely because it appears as
covered in the design-coverage response. Review and execution are separate
stages.
