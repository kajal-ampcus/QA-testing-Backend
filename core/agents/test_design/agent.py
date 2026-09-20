"""
Agent 3 — Test Design Agent (architecture doc Section 6, 12).

Reads: approved requirement (with ACs) + latest COMPLETE application map.
Writes: one TestCase + TestCaseVersion row per generated test case.
Calls: LLM via structured tool use (AnthropicClient).
No browser access — reads the already-captured Application Map only.

AC coverage rules, all validated after LLM output (WARNING decisions rather
than silently passing):
  1. Every AC must appear in at least one test case's traceability list.
  2. Every AC that IS covered must have both a POSITIVE and a NEGATIVE test
     case (RULE 2 in prompts.py) — rule 1 alone would pass an AC that only
     ever got a happy-path test.
  3. Any test case below the confidence threshold is surfaced by name —
     these are usually cases where the LLM correctly declined to invent UI
     it never observed, and need the application map extended before they're
     safely executable.
Any of the three sets run_status=PARTIAL instead of SUCCESS.

Output requires human approval (requires_human_approval=True) — the generated
test cases land in DRAFT status and must be approved before Automation
Generation (Agent 5) can proceed.
"""

import uuid
from typing import Any

from core.agents.base import BaseAgent
from core.agents.test_design.prompts import SYSTEM_PROMPT, build_user_prompt
from core.agents.test_design.schemas import TestCaseBatch, TestCaseSpec, TestDesignResult
from core.agents.test_design.validation import validate_case
from domain.enums import EvidenceSource, confidence_band
from infra.db.models.application_map import ApplicationMap
from infra.db.repositories.application_map_repo import ApplicationMapRepository
from infra.db.repositories.requirement_repo import RequirementRepository
from infra.db.repositories.test_case_repo import TestCaseRepository
from infra.llm.base import LLMClient
from infra.llm.factory import get_llm_client
from schemas.envelope import (
    AgentArtifactRef,
    AgentDecision,
    AgentInputEnvelope,
    AgentOutputEnvelope,
    AgentRunStatus,
)


def _check_ac_coverage(
    acceptance_criteria: list[dict[str, Any]],
    test_cases: list[TestCaseSpec],
) -> list[str]:
    """Return AC ids not covered by any generated test case."""
    covered = set()
    for tc in test_cases:
        covered.update(tc.traceability)
    return [ac["id"] for ac in acceptance_criteria if ac["id"] not in covered]


def _check_positive_negative_pairing(
    acceptance_criteria: list[dict[str, Any]],
    test_cases: list[TestCaseSpec],
) -> list[str]:
    """RULE 2 in prompts.py asks for at least one POSITIVE and one NEGATIVE
    test case per AC. _check_ac_coverage only checks an AC appears SOMEWHERE
    in traceability — it would pass an AC covered by a single POSITIVE test
    case with no negative counterpart. Return AC ids missing either category
    (as 'AC-1: missing NEGATIVE' style strings) so that gap is visible
    instead of silently passing."""
    categories_by_ac: dict[str, set[str]] = {}
    for tc in test_cases:
        for ac_id in tc.traceability:
            categories_by_ac.setdefault(ac_id, set()).add(tc.category)

    gaps: list[str] = []
    for ac in acceptance_criteria:
        ac_id = ac["id"]
        present = categories_by_ac.get(ac_id, set())
        if ac_id not in categories_by_ac:
            continue  # already reported by _check_ac_coverage — don't double-report
        missing = [c for c in ("POSITIVE", "NEGATIVE") if c not in present]
        if missing:
            gaps.append(f"{ac_id}: missing {'/'.join(missing)}")
    return gaps


def _low_confidence_test_cases(test_cases: list[TestCaseSpec], threshold: float = 0.6) -> list[str]:
    """Test cases below the confidence threshold are usually ones where the
    LLM correctly declined to invent UI it never observed (see RULE 1) —
    real, but not safely executable as written until the application map
    covers that page. Surface them by title so a human reviewer knows
    exactly which ones need the map extended before approval, rather than
    this being buried in a per-test-case confidence number nobody scans."""
    return [
        f"{tc.title} (confidence={tc.confidence:.1f})"
        for tc in test_cases
        if tc.confidence < threshold
    ]


def _states_to_dict(app_map: ApplicationMap) -> list[dict[str, Any]]:
    return [
        {
            "state_code": s.state_code,
            "url_pattern": s.url_pattern,
            "reached_via": s.reached_via,
            "elements": s.elements,
        }
        for s in app_map.states
    ]


class TestDesignAgent(BaseAgent[TestDesignResult]):
    name = "test_design"

    def __init__(
        self,
        requirement_repo: RequirementRepository,
        map_repo: ApplicationMapRepository,
        test_case_repo: TestCaseRepository,
        llm_client: LLMClient | None = None,
    ) -> None:
        self._requirement_repo = requirement_repo
        self._map_repo = map_repo
        self._test_case_repo = test_case_repo
        self._llm = llm_client or get_llm_client()

    async def run(self, request: AgentInputEnvelope) -> TestDesignResult:
        requirement_id = uuid.UUID(request.payload["requirement_id"])
        # Optional: caller can pin a specific application_map_id; otherwise use latest
        map_id_raw = request.payload.get("application_map_id")

        # ── Load requirement ──────────────────────────────────────────────
        pair = await self._requirement_repo.get_with_current_version(requirement_id)
        if pair is None:
            raise ValueError(f"Requirement not found: {requirement_id}")
        requirement, req_version = pair

        if requirement.status != "APPROVED":
            raise ValueError(
                f"Requirement {requirement.req_code} is not APPROVED "
                f"(status={requirement.status}). Approve it before generating test cases."
            )

        # Guard against a URL project_id that doesn't match the requirement's
        # actual project. Without this check, a wrong/stale project_id in the
        # request sails through requirement lookup (requirement_id alone is
        # enough to find it) and only fails later as an opaque Postgres FK
        # violation on the test_cases insert.
        if requirement.project_id != request.project_id:
            raise ValueError(
                f"Requirement {requirement.req_code} belongs to project "
                f"{requirement.project_id}, not {request.project_id}. "
                "Check the project_id in the URL."
            )

        # ── Load application map ──────────────────────────────────────────
        if map_id_raw:
            app_map = await self._map_repo.get_with_states(uuid.UUID(map_id_raw))
        else:
            app_map = await self._map_repo.get_latest_for_project(request.project_id)

        if app_map is None:
            raise ValueError(
                "No application map found for this project. "
                "Run discovery (POST /api/v1/application-maps/projects/{id}/discover) first."
            )
        if app_map.status not in {"COMPLETE", "PARTIAL"}:
            raise ValueError(
                f"Application map {app_map.id} has status={app_map.status}. "
                "Only COMPLETE or PARTIAL maps with observed states can be used for test design."
            )
        if not app_map.states:
            raise ValueError(f"Application map {app_map.id} has no observed states.")

        # ── Call LLM ─────────────────────────────────────────────────────
        user_prompt = build_user_prompt(
            requirement_title=req_version.title,
            requirement_description=req_version.description,
            acceptance_criteria=req_version.acceptance_criteria,
            app_map_states=_states_to_dict(app_map),
            base_url=app_map.base_url,
        )

        llm_result = await self._llm.call_structured(
            system_prompt=SYSTEM_PROMPT,
            user_prompt=user_prompt,
            tool_schema=TestCaseBatch.model_json_schema(),
            tool_name="generate_test_cases",
            max_tokens=4000,
        )

        batch = TestCaseBatch.model_validate(llm_result.data)
        test_cases = batch.test_cases
        map_states = _states_to_dict(app_map)
        ac_ids = {ac["id"] for ac in req_version.acceptance_criteria}
        invalid = [(case.title, validate_case(case, map_states, ac_ids)) for case in test_cases]
        invalid = [(title, issues) for title, issues in invalid if issues]
        if invalid:
            detail = "; ".join(f"{title}: {' | '.join(issues)}" for title, issues in invalid[:10])
            raise RuntimeError(
                f"Generated test cases failed deterministic validation and were not saved. {detail}"
            )

        # ── AC coverage checks ───────────────────────────────────────────
        uncovered = _check_ac_coverage(req_version.acceptance_criteria, test_cases)
        partial_pairing = _check_positive_negative_pairing(
            req_version.acceptance_criteria, test_cases
        )
        needs_review = _low_confidence_test_cases(test_cases)

        # ── Persist test cases ────────────────────────────────────────────
        artifacts: list[AgentArtifactRef] = []
        for tc_spec in test_cases:
            tc_code = await self._test_case_repo.next_tc_code(request.project_id)
            tc, version = await self._test_case_repo.create(
                project_id=request.project_id,
                tc_code=tc_code,
                requirement_id=requirement_id,
                requirement_version=requirement.current_version,
                application_map_id=app_map.id,
                version_data={
                    "title": tc_spec.title,
                    "objective": tc_spec.objective,
                    "preconditions": tc_spec.preconditions,
                    "steps": [s.model_dump(mode="json") for s in tc_spec.steps],
                    "expected_result": tc_spec.expected_result,
                    "test_data": tc_spec.test_data,
                    "traceability": tc_spec.traceability,
                    "category": tc_spec.category,
                    "confidence": tc_spec.confidence,
                },
            )
            artifacts.append(AgentArtifactRef(type="test_case", id=str(tc.id), version=1))
        await self._test_case_repo.session.commit()

        # ── Build decisions ───────────────────────────────────────────────
        avg_confidence = (
            sum(tc.confidence for tc in test_cases) / len(test_cases) if test_cases else 0.0
        )
        band = confidence_band(avg_confidence)

        decisions: list[AgentDecision] = [
            AgentDecision(
                decision=(
                    f"Generated {len(test_cases)} test cases for {requirement.req_code} "
                    f"(confidence={avg_confidence:.2f}, band={band})"
                ),
                reason=(
                    "LLM structured extraction from approved requirement ACs "
                    "mapped to observed application states via tool use"
                ),
                evidence=[
                    f"Requirement: {requirement.req_code} @ v{requirement.current_version}",
                    f"Application map: {app_map.id} v{app_map.version} ({len(app_map.states)} states)",
                    f"ACs covered: {len(req_version.acceptance_criteria) - len(uncovered)}"
                    f"/{len(req_version.acceptance_criteria)}",
                    f"Test cases: {len(test_cases)} ({sum(1 for tc in test_cases if tc.category == 'POSITIVE')} positive, "
                    f"{sum(1 for tc in test_cases if tc.category == 'NEGATIVE')} negative, "
                    f"{sum(1 for tc in test_cases if tc.category == 'EDGE_CASE')} edge case)",
                ],
                confidence=avg_confidence,
                source=EvidenceSource.REQUIREMENT,
            )
        ]

        if uncovered:
            decisions.append(
                AgentDecision(
                    decision=f"WARNING: {len(uncovered)} AC(s) not covered: {uncovered}",
                    reason="No generated test case referenced these AC ids in its traceability list",
                    evidence=[
                        f"Uncovered ACs: {uncovered}",
                        "Check application map — these ACs may reference UI not yet discovered",
                    ],
                    confidence=0.3,
                    source=EvidenceSource.INFERENCE,
                )
            )

        if partial_pairing:
            decisions.append(
                AgentDecision(
                    decision=f"WARNING: {len(partial_pairing)} AC(s) missing POSITIVE/NEGATIVE pairing: {partial_pairing}",
                    reason="RULE 2 requires at least one POSITIVE and one NEGATIVE test case per AC; these ACs have coverage but not both categories",
                    evidence=[
                        f"Partially covered ACs: {partial_pairing}",
                        "Usually means the missing side needs UI not yet in the application map, or the LLM simply skipped it — review before approval",
                    ],
                    confidence=0.3,
                    source=EvidenceSource.INFERENCE,
                )
            )

        if needs_review:
            decisions.append(
                AgentDecision(
                    decision=f"WARNING: {len(needs_review)} test case(s) below confidence threshold, likely referencing UI not in the application map: {needs_review}",
                    reason="confidence < 0.6 per RULE 6 in prompts.py — the AC could not be fully mapped to observed states",
                    evidence=[
                        f"Low-confidence test cases: {needs_review}",
                        "Extend Discovery to crawl the missing page(s), then regenerate or manually fill in element codes before approving",
                    ],
                    confidence=0.3,
                    source=EvidenceSource.INFERENCE,
                )
            )

        run_status = (
            AgentRunStatus.PARTIAL
            if (uncovered or partial_pairing or needs_review)
            else AgentRunStatus.SUCCESS
        )

        envelope = AgentOutputEnvelope(
            agent_run_id=request.agent_run_id,
            status=run_status,
            artifacts=artifacts,
            decisions=decisions,
            requires_human_approval=True,
            errors=[],
            token_usage={
                "model": llm_result.model,
                "input_tokens": llm_result.input_tokens,
                "output_tokens": llm_result.output_tokens,
                "total_tokens": llm_result.total_tokens,
            },
        )

        return TestDesignResult(
            envelope=envelope,
            test_cases=test_cases,
            uncovered_acs=uncovered,
            partial_pairing_acs=partial_pairing,
            needs_review_test_cases=needs_review,
        )
