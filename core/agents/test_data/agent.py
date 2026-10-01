"""
Agent 10 — Test Data Agent. A callable service, not a pipeline stage.

Generates valid / invalid / boundary values from observed field names and
roles. Credentials are never produced here.
"""

import re
from typing import Any, Literal

from core.agents.base import BaseAgent
from core.agents.test_data.schemas import FieldDatum
from schemas.envelope import AgentInputEnvelope

Category = Literal["POSITIVE", "NEGATIVE", "EDGE_CASE"]


def value_for_element(element: dict[str, Any], category: Category) -> FieldDatum:
    name = str(element.get("name") or element.get("element_code") or "field")
    key = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_") or "field"
    role_name = f"{name} {element.get('type') or ''} {element.get('role') or ''}".lower()
    required = bool((element.get("validation") or {}).get("required"))
    if category == "EDGE_CASE":
        if "email" in role_name:
            value = "  first.last+tag@sub.example.test  "
        elif "phone" in role_name or "mobile" in role_name:
            value = "+1 (999) 999-9999"
        else:
            value = "  <b>O'Brien & Co</b> " + ("x" * (64 if required else 256))
    elif category == "POSITIVE":
        if "email" in role_name:
            value = "person@example.test"
        elif "pass" in role_name:
            value = "ValidPass1!"
        elif "phone" in role_name or "mobile" in role_name:
            value = "9999999999"
        else:
            value = "valid-input"
    elif "email" in role_name:
        value = "not-an-email"
    elif "pass" in role_name:
        value = "wrong"
    elif "phone" in role_name or "mobile" in role_name:
        value = "abc"
    else:
        value = ""
    return FieldDatum(key=key, name=name, value=value, category=category)


class TestDataAgent(BaseAgent[list[FieldDatum]]):
    name = "test_data"

    def for_elements(
        self, elements: list[dict[str, Any]], category: Category
    ) -> list[FieldDatum]:
        return [value_for_element(element, category) for element in elements]

    async def run(self, request: AgentInputEnvelope) -> list[FieldDatum]:
        category = request.payload.get("category", "POSITIVE")
        elements = list(request.payload.get("elements") or [])
        return self.for_elements(elements, category)
