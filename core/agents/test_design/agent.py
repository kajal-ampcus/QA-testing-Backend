"""
Agent 3 — Test Design Agent (architecture doc Section 6, 12).

Per-AC batching strategy
─────────────────────────
One LLM call per acceptance criterion. This is the fix for the token
truncation failure: sending all ACs + full app map in a single call
produces a response that is too large for small/free-tier models
(gpt-oss-120b, llama variants) — the JSON is cut mid-object at the token
limit and fails to parse. Per-AC calls are:

  • Bounded: each call generates 2–4 test cases (POSITIVE + NEGATIVE +
    optional EDGE_CASE), never the full batch at once.
  • Independent: a single AC failing does not cancel the others.
  • Auditable: token usage and model are accumulated across all calls
    and reported in the final envelope so cost is visible.

Validation
───────────
After all LLM calls, every generated test case is run through the
deterministic validator (validation.py) which checks:
  - steps reference only observed states and elements
  - step numbers are consecutive (renumbered if not)
  - assertions have non-empty expected values
  - test_data placeholders resolve

Cases that fail validation are collected and reported as WARNING decisions
rather than crashing the whole run — a bad case for AC-3 should not block
the perfectly valid cases for AC-1 and AC-2.
"""

import asyncio
import re
import uuid
from typing import Any

from core.agents.base import BaseAgent
from core.agents.test_design.prompts import SYSTEM_PROMPT, build_user_prompt
from core.agents.test_design.schemas import TestCaseBatch, TestCaseSpec, TestDesignResult
from core.agents.test_design.validation import validate_case
from domain.enums import EvidenceSource, confidence_band
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
    covered = {ac_id for tc in test_cases for ac_id in tc.traceability}
    return [ac["id"] for ac in acceptance_criteria if ac["id"] not in covered]


def _check_positive_negative_pairing(
    acceptance_criteria: list[dict[str, Any]],
    test_cases: list[TestCaseSpec],
) -> list[str]:
    categories_by_ac: dict[str, set[str]] = {}
    for tc in test_cases:
        for ac_id in tc.traceability:
            categories_by_ac.setdefault(ac_id, set()).add(tc.category)
    gaps = []
    for ac in acceptance_criteria:
        ac_id = ac["id"]
        if ac_id not in categories_by_ac:
            continue  # already in uncovered list
        missing = [c for c in ("POSITIVE", "NEGATIVE") if c not in categories_by_ac[ac_id]]
        if missing:
            gaps.append(f"{ac_id}: missing {'/'.join(missing)}")
    return gaps


def _low_confidence(test_cases: list[TestCaseSpec], threshold: float = 0.6) -> list[str]:
    return [
        f"{tc.title} (confidence={tc.confidence:.1f})"
        for tc in test_cases
        if tc.confidence < threshold
    ]


def _renumber_steps(tc: TestCaseSpec) -> TestCaseSpec:
    """
    Renumber steps so they are always consecutive starting at 1.
    The validator rejects non-consecutive step numbers — models sometimes
    emit gaps when they delete a step during generation.
    This runs before validation so the validator sees clean data.
    """
    for i, step in enumerate(tc.steps, 1):
        step.step_number = i
    return tc


def _states_for_generation(
    states: list[Any],
    generation_scope: str,
    previously_generated: set[str],
    selected_fingerprints: set[str] | None = None,
) -> list[Any]:
    scoped = [
        state
        for state in states
        if selected_fingerprints is None or state.fingerprint in selected_fingerprints
    ]
    if generation_scope == "ungenerated":
        scoped = [
            state
            for state in scoped
            if state.fingerprint not in previously_generated
        ]
    return scoped


def _map_context_for_ac(
    states: list[dict[str, Any]],
    ac: dict[str, Any],
    requirement_title: str,
    requirement_description: str,
    max_states: int = 8,
    max_elements_per_state: int = 8,
) -> list[dict[str, Any]]:
    """Keep the LLM prompt focused on states and controls relevant to this AC.

    The complete map is retained elsewhere for deterministic validation; only
    the provider prompt is reduced. This prevents large crawls from exceeding
    the model's combined input/output context limit on every per-AC request.
    """
    terms = {
        word.lower()
        for word in re.findall(r"[a-zA-Z0-9]{3,}", " ".join((
            requirement_title,
            requirement_description,
            str(ac.get("text", "")),
        )))
    }
    scored: list[tuple[int, int, dict[str, Any]]] = []
    for index, state in enumerate(states):
        state_text = " ".join((
            str(state.get("url_pattern", "")),
            " ".join(map(str, state.get("reached_via", []))),
        )).lower()
        elements = [
            element for element in state.get("elements", [])
            if element.get("source") == "OBSERVED_DOM"
        ]
        element_scores = [
            sum(term in " ".join(str(value) for value in element.values()).lower() for term in terms)
            for element in elements
        ]
        state_score = sum(term in state_text for term in terms) + sum(element_scores)
        # Retain useful actionable controls even when wording differs from the AC.
        ranked_elements = sorted(
            zip(element_scores, elements),
            key=lambda item: (
                item[0],
                item[1].get("role") in {"textbox", "searchbox", "button", "link", "checkbox", "radio"},
            ),
            reverse=True,
        )
        compact_state = {
            **state,
            "elements": [element for _, element in ranked_elements[:max_elements_per_state]],
        }
        scored.append((state_score, -index, compact_state))

    selected = sorted(scored, key=lambda item: (item[0], item[1]), reverse=True)[:max_states]
    # Restore the original navigation order after relevance selection.
    selected_states = [item[2] for item in sorted(selected, key=lambda item: -item[1])]
    return selected_states


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

    async def _generate_for_ac(
        self,
        ac: dict[str, Any],
        req_title: str,
        req_description: str,
        map_states: list[dict[str, Any]],
        base_url: str,
        required_categories: set[str] | None = None,
    ) -> tuple[list[TestCaseSpec], int, int, str, str | None]:
        """
        Call the LLM for a single AC and return:
          (test_cases, input_tokens, output_tokens, model, error_message)

        Returns an empty list + error string on failure so the caller can
        continue processing other ACs instead of crashing the whole run.
        """
        user_prompt = build_user_prompt(
            requirement_title=req_title,
            requirement_description=req_description,
            acceptance_criteria=[ac],
            app_map_states=_map_context_for_ac(
                map_states, ac, req_title, req_description
            ),
            base_url=base_url,
            required_categories=sorted(required_categories) if required_categories else None,
        )
        try:
            llm_result = await asyncio.wait_for(
                self._llm.call_structured(
                    system_prompt=SYSTEM_PROMPT,
                    user_prompt=user_prompt,
                    tool_schema=TestCaseBatch.model_json_schema(),
                    tool_name="generate_test_cases",
                    # 2500 tokens per AC call — enough for focused cases
                    # (POSITIVE + NEGATIVE + optional EDGE_CASE).
                    # Keep output headroom bounded for providers enforcing a
                    # combined prompt + completion context limit.
                    max_tokens=2500,
                ),
                timeout=120.0,
            )
        except TimeoutError:
            return (
                [], 0, 0, "",
                f"LLM call timed out after 120 s for AC '{ac.get('id', '?')}'. "
                "Check that the LLM provider is reachable.",
            )
        except Exception as exc:  # noqa: BLE001
            return (
                [], 0, 0, "",
                f"LLM call failed for AC '{ac.get('id', '?')}': {exc}",
            )

        try:
            batch = TestCaseBatch.model_validate(llm_result.data)
        except Exception as exc:  # noqa: BLE001
            return (
                [], llm_result.input_tokens, llm_result.output_tokens, llm_result.model,
                f"Schema validation failed for AC '{ac.get('id', '?')}': {exc}",
            )

        return (
            batch.test_cases,
            llm_result.input_tokens,
            llm_result.output_tokens,
            llm_result.model,
            None,
        )

    async def run(self, request: AgentInputEnvelope) -> TestDesignResult:
        requirement_id = uuid.UUID(request.payload["requirement_id"])
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

        if requirement.project_id != request.project_id:
            raise ValueError(
                f"Requirement {requirement.req_code} belongs to project "
                f"{requirement.project_id}, not {request.project_id}."
            )

        # ── Load application map ──────────────────────────────────────────
        if map_id_raw:
            app_map = await self._map_repo.get_with_states(uuid.UUID(map_id_raw))
        else:
            app_map = await self._map_repo.get_latest_for_project(request.project_id)

        if app_map is None:
            raise ValueError(
                "No application map found for this project. "
                "Run discovery first."
            )
        if app_map.status not in {"COMPLETE", "PARTIAL"}:
            raise ValueError(
                f"Application map {app_map.id} has status={app_map.status}. "
                "Only COMPLETE or PARTIAL maps can be used for test design."
            )
        if not app_map.states:
            raise ValueError(f"Application map {app_map.id} has no observed states.")

        # ── Per-AC LLM calls ──────────────────────────────────────────────
        # One call per AC keeps each response bounded to 2–4 test cases.
        # This is the core fix: a single call for all ACs produces JSON that
        # is too large for small models and gets truncated mid-object.
        generation_scope = request.payload.get("generation_scope", "all")
        selected_area_ids = set(request.payload.get("selected_area_ids", []))
        selected_module_ids = set(request.payload.get("selected_module_ids", []))
        selected_fingerprints: set[str] | None = None
        graph = (app_map.coverage or {}).get("app_flow_graph", {})
        graph_nodes = graph.get("nodes", [])
        if selected_module_ids:
            selected_fingerprints = {
                node["fingerprint"]
                for node in graph_nodes
                if node.get("module_id") in selected_module_ids
                or node.get("area_id") in selected_area_ids
            }
            dashboard_roots = {
                fingerprint
                for area in graph.get("subgraphs", [])
                if area.get("id") == "dashboard"
                for fingerprint in area.get("state_fingerprints", [])[:1]
            }
            selected_fingerprints.update(dashboard_roots)
        elif selected_area_ids:
            selected_fingerprints = {
                node["fingerprint"]
                for node in graph_nodes
                if node.get("area_id") in selected_area_ids
            }
        previously_generated: set[str] = set()
        if generation_scope == "ungenerated":
            previously_generated = await self._map_repo.generated_fingerprints(
                request.project_id, requirement_id
            )
        scoped_states = _states_for_generation(
            app_map.states,
            generation_scope,
            previously_generated,
            selected_fingerprints,
        )
        if not scoped_states:
            raise ValueError(
                "No application states match the selected generation scope."
            )
        map_states = [
            {
                "state_code": state.state_code,
                "url_pattern": state.url_pattern,
                "reached_via": state.reached_via,
                "elements": state.elements,
            }
            for state in scoped_states
        ]
        requested_categories: dict[str, set[str]] = {
            ac_id: set(categories)
            for ac_id, categories in request.payload.get("target_categories", {}).items()
        }
        all_test_cases: list[TestCaseSpec] = []
        llm_errors: list[str] = []
        total_input_tokens = total_output_tokens = 0
        model_name = ""

        for ac in req_version.acceptance_criteria:
            if requested_categories and ac["id"] not in requested_categories:
                continue
            tcs, inp, out, model, err = await self._generate_for_ac(
                ac=ac,
                req_title=req_version.title,
                req_description=req_version.description,
                map_states=map_states,
                base_url=app_map.base_url,
                required_categories=requested_categories.get(ac["id"]),
            )
            if err:
                llm_errors.append(err)
            required = requested_categories.get(ac["id"])
            all_test_cases.extend(tc for tc in tcs if not required or tc.category in required)
            total_input_tokens += inp
            total_output_tokens += out
            if model:
                model_name = model

        # If EVERY AC failed, raise — there is nothing to persist
        if not all_test_cases and llm_errors:
            raise RuntimeError(
                f"All {len(req_version.acceptance_criteria)} AC generation calls failed.\n"
                + "\n".join(f"  • {e}" for e in llm_errors)
            )

        # ── Renumber + validate ───────────────────────────────────────────
        ac_ids = {ac["id"] for ac in req_version.acceptance_criteria}
        valid_cases: list[TestCaseSpec] = []
        validation_errors: list[str] = []

        for tc in all_test_cases:
            # Renumber steps before validation so gaps don't fail the check
            tc = _renumber_steps(tc)
            issues = validate_case(tc, map_states, ac_ids)
            if issues:
                validation_errors.append(
                    f"'{tc.title}': {' | '.join(issues)}"
                )
            else:
                valid_cases.append(tc)

        # If validation wiped everything, raise
        if not valid_cases:
            raise RuntimeError(
                f"All generated test cases failed validation and were not saved.\n"
                + "\n".join(f"  • {e}" for e in validation_errors[:10])
            )

        # ── AC coverage checks ────────────────────────────────────────────
        coverage_cases = list(valid_cases)
        if requested_categories:
            for _, version in await self._test_case_repo.list_for_requirement(requirement_id):
                coverage_cases.append(
                    TestCaseSpec.model_validate(
                        {
                            "title": version.title,
                            "objective": version.objective,
                            "category": version.category,
                            "preconditions": version.preconditions,
                            "steps": version.steps,
                            "expected_result": version.expected_result,
                            "test_data": version.test_data,
                            "traceability": version.traceability,
                            "confidence": version.confidence,
                        }
                    )
                )
        uncovered = _check_ac_coverage(req_version.acceptance_criteria, coverage_cases)
        partial_pairing = _check_positive_negative_pairing(req_version.acceptance_criteria, coverage_cases)
        needs_review = _low_confidence(coverage_cases)

        # ── Persist valid test cases ──────────────────────────────────────
        artifacts: list[AgentArtifactRef] = []
        for tc_spec in valid_cases:
            tc_code = await self._test_case_repo.next_tc_code(request.project_id)
            tc, _ = await self._test_case_repo.create(
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
        await self._map_repo.mark_generated_fingerprints(
            app_map.id,
            requirement_id,
            {state.fingerprint for state in scoped_states},
        )
        await self._test_case_repo.session.commit()

        # ── Build decisions ───────────────────────────────────────────────
        avg_confidence = (
            sum(tc.confidence for tc in valid_cases) / len(valid_cases)
            if valid_cases else 0.0
        )
        band = confidence_band(avg_confidence)

        decisions: list[AgentDecision] = [
            AgentDecision(
                decision=(
                    f"Generated {len(valid_cases)} test cases for {requirement.req_code} "
                    f"(confidence={avg_confidence:.2f}, band={band})"
                ),
                reason=(
                    f"Per-AC LLM calls ({len(req_version.acceptance_criteria)} ACs, "
                    f"{len(req_version.acceptance_criteria)} calls) mapped to "
                    f"{len(app_map.states)} observed application states"
                ),
                evidence=[
                    f"Requirement: {requirement.req_code} @ v{requirement.current_version}",
                    f"Application map: {app_map.id} v{app_map.version} ({len(app_map.states)} states)",
                    f"ACs covered: {len(req_version.acceptance_criteria) - len(uncovered)}/{len(req_version.acceptance_criteria)}",
                    f"Test cases: {len(valid_cases)} total "
                    f"({sum(1 for tc in valid_cases if tc.category == 'POSITIVE')} positive, "
                    f"{sum(1 for tc in valid_cases if tc.category == 'NEGATIVE')} negative, "
                    f"{sum(1 for tc in valid_cases if tc.category == 'EDGE_CASE')} edge case)",
                ],
                confidence=avg_confidence,
                source=EvidenceSource.REQUIREMENT,
            )
        ]

        # LLM errors (some ACs failed but others succeeded)
        if llm_errors:
            decisions.append(AgentDecision(
                decision=f"WARNING: {len(llm_errors)} AC(s) failed LLM generation and produced no test cases",
                reason="LLM call failed or timed out for these ACs",
                evidence=llm_errors[:5],
                confidence=0.0,
                source=EvidenceSource.INFERENCE,
            ))

        # Validation errors (cases generated but rejected)
        if validation_errors:
            decisions.append(AgentDecision(
                decision=f"WARNING: {len(validation_errors)} generated test case(s) failed validation and were not saved",
                reason="Deterministic validation rejected these cases (invented elements, missing assertions, etc.)",
                evidence=validation_errors[:5],
                confidence=0.2,
                source=EvidenceSource.INFERENCE,
            ))

        if uncovered:
            decisions.append(AgentDecision(
                decision=f"WARNING: {len(uncovered)} AC(s) not covered: {uncovered}",
                reason="No valid test case referenced these AC ids in its traceability list",
                evidence=[f"Uncovered ACs: {uncovered}", "Extend the application map or clarify the requirement"],
                confidence=0.3,
                source=EvidenceSource.INFERENCE,
            ))

        if partial_pairing:
            decisions.append(AgentDecision(
                decision=f"WARNING: {len(partial_pairing)} AC(s) missing POSITIVE/NEGATIVE pairing",
                reason="Rule 2 requires both POSITIVE and NEGATIVE for each AC",
                evidence=[f"Partially covered: {partial_pairing}"],
                confidence=0.3,
                source=EvidenceSource.INFERENCE,
            ))

        if needs_review:
            decisions.append(AgentDecision(
                decision=f"WARNING: {len(needs_review)} low-confidence test case(s) need review",
                reason="confidence < 0.6 — AC could not be fully mapped to observed states",
                evidence=needs_review[:5],
                confidence=0.3,
                source=EvidenceSource.INFERENCE,
            ))

        has_warnings = bool(llm_errors or validation_errors or uncovered or partial_pairing or needs_review)
        run_status = AgentRunStatus.PARTIAL if has_warnings else AgentRunStatus.SUCCESS

        envelope = AgentOutputEnvelope(
            agent_run_id=request.agent_run_id,
            status=run_status,
            artifacts=artifacts,
            decisions=decisions,
            requires_human_approval=True,
            errors=[],
            token_usage={
                "model": model_name,
                "input_tokens": total_input_tokens,
                "output_tokens": total_output_tokens,
                "total_tokens": total_input_tokens + total_output_tokens,
            },
        )

        return TestDesignResult(
            envelope=envelope,
            test_cases=valid_cases,
            uncovered_acs=uncovered,
            partial_pairing_acs=partial_pairing,
            needs_review_test_cases=needs_review,
        )
