"""
The orchestrator state machine (architecture doc Section 25):

CREATED -> REQUIREMENTS_ANALYZED -> APPLICATION_DISCOVERED ->
TEST_CASES_GENERATED -> TEST_CASES_VALIDATED -> AUTOMATION_GENERATED ->
AUTOMATION_VALIDATED -> EXECUTION -> FAILURE_ANALYSIS -> REPORT -> COMPLETED

Plus BLOCKED (reached from TEST_CASES_VALIDATED or AUTOMATION_VALIDATED after
3 rejection cycles exhausted) and re-entry from COMPLETED back to
APPLICATION_DISCOVERED (regression trigger).

Agents never call each other directly except the narrow whitelisted
exceptions (Failure Analysis invoking Execution for reproduction, Test
Design/Execution invoking Test Data) — everything else routes through here.

Phase 0 stub.
"""

# TODO (Phase 0/1): from enum import StrEnum; class OrchestratorState(StrEnum): ...
# TODO (Phase 1): class Orchestrator: async def transition(self, project_id, event): ...
