"""
AutomationScript entity (architecture doc Section 13/26).

Identity and traceability for one generated Playwright script. Persistence
lives in infra; this type stays free of frameworks.
"""

from dataclasses import dataclass, field
from uuid import UUID

from domain.enums import RiskLevel


@dataclass
class AutomationScript:
    id: UUID
    script_code: str
    version: int
    project_id: UUID
    test_case_id: UUID
    test_case_version: int
    application_map_id: UUID
    application_map_version: int
    framework: str
    file_path: str
    selector_strategy: list[dict[str, object]] = field(default_factory=list)
    risk: RiskLevel = RiskLevel.SAFE
