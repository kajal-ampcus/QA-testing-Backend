"""Map-grounded drafts must validate without an LLM round trip."""

from core.agents.test_design.fast_generate import generate_cases_from_map
from core.agents.test_design.validation import validate_case

STATES = [
    {
        "state_code": "STATE-001",
        "url_pattern": "/login",
        "reached_via": ["open /login"],
        "elements": [
            {
                "element_code": "EL-001",
                "role": "textbox",
                "name": "Email",
                "source": "OBSERVED_DOM",
            },
            {
                "element_code": "EL-002",
                "role": "textbox",
                "name": "Password",
                "source": "OBSERVED_DOM",
            },
            {
                "element_code": "EL-003",
                "role": "button",
                "name": "LOGIN",
                "source": "OBSERVED_DOM",
            },
        ],
    },
    {
        "state_code": "STATE-002",
        "url_pattern": "/dashboard",
        "reached_via": ["click LOGIN"],
        "elements": [
            {
                "element_code": "EL-010",
                "role": "button",
                "name": "Logout",
                "source": "OBSERVED_DOM",
            },
        ],
    },
]


def test_fast_generate_covers_each_ac_with_valid_positive_and_negative() -> None:
    acs = [{"id": "AC-1", "text": "User can log in with valid credentials"}]
    cases = generate_cases_from_map(acs, STATES, "Login")
    assert {case.category for case in cases} == {"POSITIVE", "NEGATIVE"}
    for case in cases:
        assert validate_case(case, STATES, {"AC-1"}) == []
        assert case.steps[-1].action == "assert"
        assert case.traceability == ["AC-1"]
