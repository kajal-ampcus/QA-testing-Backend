"""
Agent 1 — Requirement Understanding Agent (architecture doc Section 6).

LLM only — no browser, no MCP access (giving it zero tools is what
guarantees it can't hallucinate a browsing action; companion doc Part 2 #1).
Ambiguous requirements come back with a non-empty `ambiguities` list rather
than a confident guess.
"""

from core.agents.base import BaseAgent
from core.agents.requirement_understanding.prompts import SYSTEM_PROMPT, build_user_prompt
from core.agents.requirement_understanding.schemas import (
    RequirementExtraction,
    RequirementUnderstandingResult,
)
from domain.enums import EvidenceSource
from infra.llm.base import LLMClient
from infra.llm.factory import get_llm_client
from schemas.envelope import (
    AgentDecision,
    AgentInputEnvelope,
    AgentOutputEnvelope,
    AgentRunStatus,
)


class RequirementUnderstandingAgent(BaseAgent[RequirementUnderstandingResult]):
    name = "requirement_understanding"

    def __init__(self, llm_client: LLMClient | None = None) -> None:
        self.llm_client = llm_client or get_llm_client()

    async def run(self, request: AgentInputEnvelope) -> RequirementUnderstandingResult:
        raw_text: str = request.payload["raw_text"]
        project_glossary: dict[str, str] = request.payload.get("project_glossary", {})

        llm_result = await self.llm_client.call_structured(
            system_prompt=SYSTEM_PROMPT,
            user_prompt=build_user_prompt(raw_text, project_glossary),
            tool_schema=RequirementExtraction.model_json_schema(),
            tool_name="extract_requirement",
        )
        extraction = RequirementExtraction.model_validate(llm_result.data)
        # Criterion ids are used as edit and traceability keys, so they must be
        # unique; models occasionally repeat or skip numbers.
        for index, criterion in enumerate(extraction.acceptance_criteria, start=1):
            criterion.id = f"AC-{index}"

        blocking = [a for a in extraction.ambiguities if a.requires_clarification]
        needs_clarification = len(blocking) > 0
        # Confidence here is a simple, honest proxy for Milestone 1 — Section 22's
        # full evidence-agreement-based scoring (core/confidence/scoring.py) isn't
        # wired in yet; this is not meant to be the final confidence model.
        confidence = 0.6 if needs_clarification else 0.9

        evidence = [f"raw_text[:200]={raw_text[:200]!r}"]
        if needs_clarification:
            evidence.append(f"{len(blocking)} blocking ambiguity(ies) flagged by the model")

        decision = AgentDecision(
            decision=(
                f"Extracted {len(extraction.acceptance_criteria)} acceptance criteria"
                + (", flagged for clarification" if needs_clarification else "")
            ),
            reason="Structured extraction from tester-provided requirement text via LLM tool use",
            evidence=evidence,
            confidence=confidence,
            source=EvidenceSource.REQUIREMENT,
        )

        envelope = AgentOutputEnvelope(
            agent_run_id=request.agent_run_id,
            status=AgentRunStatus.PARTIAL if needs_clarification else AgentRunStatus.SUCCESS,
            artifacts=[],  # populated by the caller after persisting (router owns the transaction)
            decisions=[decision],
            requires_human_approval=True,  # Section 21 — always required for this agent's output
            errors=[],
            token_usage={
                "model": llm_result.model,
                "input_tokens": llm_result.input_tokens,
                "output_tokens": llm_result.output_tokens,
                "total_tokens": llm_result.total_tokens,
            },
        )

        return RequirementUnderstandingResult(envelope=envelope, extraction=extraction)
