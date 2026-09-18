"""
The 11-value failure classification taxonomy (architecture doc Section 16):

APPLICATION_DEFECT | AUTOMATION_DEFECT | TEST_DATA_DEFECT | ENVIRONMENT_DEFECT
NETWORK_DEFECT | AUTHENTICATION_DEFECT | DEPENDENCY_FAILURE | TIMEOUT
FLAKY_TEST | REQUIREMENT_MISMATCH | UNKNOWN

Plus the deterministic pre-checks that run BEFORE any LLM reasoning: HTTP
5xx on a dependent call -> strong prior toward APPLICATION_DEFECT; locator
not found but an equivalent element exists in the accessibility tree ->
strong prior toward AUTOMATION_DEFECT (route to Selector Healing); DNS/
connection-refused -> ENVIRONMENT_DEFECT. The LLM only reasons over the
residual ambiguous cases, and must cite which evidence items support its
conclusion.

Phase 0 stub.
"""

# TODO (Phase 0/1): from enum import StrEnum; class FailureClassification(StrEnum): ...
