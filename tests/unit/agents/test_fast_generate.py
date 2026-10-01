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


def test_positive_case_asserts_the_state_its_click_leads_to() -> None:
    acs = [{"id": "AC-1", "text": "User can log in with valid credentials"}]
    positive = next(
        case for case in generate_cases_from_map(acs, STATES, "Login") if case.category == "POSITIVE"
    )
    assert positive.steps[-1].target.state_code == "STATE-002"
    assert positive.confidence == 0.8


def test_positive_case_without_observed_result_stays_on_start_state() -> None:
    states = [
        STATES[0],
        {**STATES[1], "reached_via": ["navigate(url='/dashboard',observed_link='Home')"]},
    ]
    acs = [{"id": "AC-1", "text": "User can log in with valid credentials"}]
    positive = next(
        case for case in generate_cases_from_map(acs, states, "Login") if case.category == "POSITIVE"
    )
    assert positive.steps[-1].target.state_code == "STATE-001"
    assert positive.confidence < 0.6  # flagged for review


def test_destructive_controls_are_never_used() -> None:
    states = [
        {
            "state_code": "STATE-001",
            "url_pattern": "/settings",
            "reached_via": [],
            "elements": [
                {"element_code": "EL-001", "role": "button", "name": "Delete account",
                 "risk": "DESTRUCTIVE", "source": "OBSERVED_DOM"},
            ],
        }
    ]
    cases = generate_cases_from_map([{"id": "AC-1", "text": "Settings"}], states, "Settings")
    assert all(step.action != "click" for case in cases for step in case.steps)


def test_edge_case_uses_boundary_values_not_negative_copy() -> None:
    acs = [{"id": "AC-1", "text": "User can log in"}]
    cases = generate_cases_from_map(acs, STATES, "Login", {"AC-1": {"NEGATIVE", "EDGE_CASE"}})
    by_category = {case.category: case for case in cases}
    assert set(by_category) == {"NEGATIVE", "EDGE_CASE"}
    assert by_category["EDGE_CASE"].test_data != by_category["NEGATIVE"].test_data
    assert validate_case(by_category["EDGE_CASE"], STATES, {"AC-1"}) == []
