"""Map-grounded drafts must validate without an LLM round trip."""

from core.agents.test_design.fast_generate import generate_cases_from_map
from core.agents.test_design.validation import validate_case
from infra.db.repositories.test_case_repo import case_signature


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


def test_login_matches_discovery_authentication_action():
    from copy import deepcopy
    states = deepcopy(STATES)
    states[1]["reached_via"] = [
        "click(role=button,name='Employee')",
        "click(role=authentication,name='Sign in with selected account')",
    ]
    states[0]["reached_via"] = []
    cases = generate_cases_from_map(
        [{"id": "AC-1", "text": "User can log in with valid credentials"}], states, "Login"
    )
    assert cases
    assert any(case.steps[-1].target.state_code == "STATE-002" for case in cases)
    assert all(not validate_case(case, states, {"AC-1"}) for case in cases)


def test_authentication_criterion_selects_login_not_employee_role():
    from copy import deepcopy
    states = deepcopy(STATES)
    states[0]["reached_via"] = []
    states[0]["elements"].insert(0, {
        "element_code": "EL-ROLE", "role": "button", "name": "Employee", "source": "OBSERVED_DOM",
    })
    states[1]["reached_via"] = ["click(role=button,name='Employee')",
        "click(role=authentication,name='Sign in with selected account')"]
    cases = generate_cases_from_map([{
        "id": "AC-2", "text": "The system validates the credentials and CAPTCHA and redirects the employee to the Dashboard upon successful authentication",
    }], states, "Employee Login, Food Ordering, Order Tracking, Notifications, and Logout")
    assert cases
    assert any(step.action == "click" and step.target.element_name == "LOGIN" for step in cases[0].steps)
    assert all(not validate_case(case, states, {"AC-2"}) for case in cases)


def test_required_field_negative_and_boundary_cases_are_distinct_and_grounded():
    from copy import deepcopy
    states = deepcopy(STATES)
    states[0]["elements"][0]["validation"] = {"required": True}
    acs = [{"id": "AC-1", "text": "Email is mandatory"}]
    cases = generate_cases_from_map(acs, states, "Login")
    negative = next(case for case in cases if case.category == "NEGATIVE")
    edge = next(case for case in cases if case.category == "EDGE_CASE")
    assert negative.test_data == {"field_value": ""}
    assert edge.test_data == {"field_value": "a"}
    assert negative.expected_result != edge.expected_result
    assert all(not validate_case(case, states, {"AC-1"}) for case in [negative, edge])
    assert all(step.action != "click" for case in [negative, edge] for step in case.steps)
    targeted = generate_cases_from_map(acs, states, "Login", {"AC-1": {"NEGATIVE"}})
    assert all(case.category == "NEGATIVE" for case in targeted)


def test_required_login_field_does_not_generate_cases_on_forgot_password_page() -> None:
    states = [
        {
            "state_code": "STATE-LOGIN",
            "url_pattern": "/login",
            "reached_via": [],
            "elements": [{
                "element_code": "EL-LOGIN-ID", "role": "textbox", "name": "Employee ID",
                "source": "OBSERVED_DOM", "validation": {"required": True},
            }],
        },
        {
            "state_code": "STATE-FORGOT",
            "url_pattern": "/forgot-password",
            "reached_via": ["click(role=link,name='Forgot Password')"],
            "elements": [{
                "element_code": "EL-FORGOT-ID", "role": "textbox", "name": "Employee / staff ID",
                "source": "OBSERVED_DOM", "validation": {"required": True},
            }],
        },
        {
            "state_code": "STATE-DASHBOARD",
            "url_pattern": "/dashboard",
            "reached_via": ["click(role=authentication,name='Sign in')"],
            "elements": [],
        },
    ]
    cases = generate_cases_from_map(
        [{"id": "AC-LOGIN-ID", "text": "Employee ID is required on the login page"}],
        states,
        "Login, Forgot Password, and Dashboard",
    )
    required_cases = [case for case in cases if "required input" in case.title]
    assert required_cases
    assert {case.steps[0].target.state_code for case in required_cases} == {"STATE-LOGIN"}


def test_required_forgot_password_field_uses_forgot_password_page() -> None:
    states = [
        {
            "state_code": "STATE-LOGIN", "url_pattern": "/login", "reached_via": [],
            "elements": [{"element_code": "EL-LOGIN-ID", "role": "textbox", "name": "Employee ID",
                          "source": "OBSERVED_DOM", "validation": {"required": True}}],
        },
        {
            "state_code": "STATE-FORGOT", "url_pattern": "/forgot-password",
            "reached_via": ["click(role=link,name='Forgot Password')"],
            "elements": [{"element_code": "EL-FORGOT-ID", "role": "textbox", "name": "Employee / staff ID",
                          "source": "OBSERVED_DOM", "validation": {"required": True}}],
        },
    ]
    cases = generate_cases_from_map(
        [{"id": "AC-FORGOT-ID", "text": "Employee or staff ID is required on Forgot Password"}],
        states,
        "Login, Forgot Password, and Dashboard",
    )
    required_cases = [case for case in cases if "required input" in case.title]
    assert required_cases
    assert {case.steps[0].target.state_code for case in required_cases} == {"STATE-FORGOT"}


def test_fast_generate_does_not_manufacture_positive_negative_pairs() -> None:
    acs = [{"id": "AC-1", "text": "User can log in with valid credentials"}]
    cases = generate_cases_from_map(acs, STATES, "Login")
    assert {case.category for case in cases} == {"POSITIVE"}
    for case in cases:
        assert validate_case(case, STATES, {"AC-1"}) == []
        assert case.steps[-1].action == "assert"
        assert case.traceability == ["AC-1"]


def test_observed_controls_only_add_coverage_when_the_requirement_mentions_them() -> None:
    states = [
        {
            "state_code": "STATE-LOGIN", "url_pattern": "/login", "reached_via": [],
            "elements": [
                {"element_code": "EL-ID", "role": "textbox", "name": "Employee ID", "source": "OBSERVED_DOM", "validation": {"required": True}},
                {"element_code": "EL-FORGOT", "role": "link", "name": "Forgot Password", "url": "/forgot-password", "source": "OBSERVED_DOM"},
                {"element_code": "EL-REMEMBER", "role": "checkbox", "name": "Remember me", "source": "OBSERVED_DOM"},
            ],
        },
        {"state_code": "STATE-FORGOT", "url_pattern": "/forgot-password",
         "reached_via": ["click(role=link,name='Forgot Password')"], "elements": []},
    ]
    cases = generate_cases_from_map(
        [{"id": "AC-1", "text": "Click Forgot Password opens the recovery page"},
         {"id": "AC-2", "text": "Employee ID is mandatory"},
         {"id": "AC-3", "text": "Remember me is available"}], states, "Authentication",
        include_discovery_coverage=True,
    )
    titles = {case.title for case in cases}
    recovery_navigation = [
        case for case in cases
        if any(step.action == "click" and step.target.element_code == "EL-FORGOT" for step in case.steps)
        and case.steps[-1].target.state_code == "STATE-FORGOT"
    ]
    assert len(recovery_navigation) == 1
    assert any("Remember me checkbox is visible" in title for title in titles)
    assert any("Employee ID: empty required input" in title for title in titles)
    assert any(case.category == "EDGE_CASE" and "Employee ID" in case.title for case in cases)
    assert all(not validate_case(case, states, {"AC-1", "AC-2", "AC-3"}) for case in cases)


def test_duplicate_signature_ignores_category_relabelling() -> None:
    content = {
        "traceability": ["AC-1"],
        "steps": [{
            "action": "assert",
            "target": {"state_code": "STATE-001", "element_code": "EL-001"},
            "value": None,
            "expected": "Logo is visible",
        }],
    }
    assert case_signature({**content, "category": "POSITIVE"}) == case_signature(
        {**content, "category": "NEGATIVE"}
    )


def test_positive_case_asserts_the_state_its_click_leads_to() -> None:
    acs = [{"id": "AC-1", "text": "User can log in with valid credentials"}]
    positive = next(
        case for case in generate_cases_from_map(acs, STATES, "Login") if case.category == "POSITIVE"
    )
    assert positive.steps[-1].target.state_code == "STATE-002"
    assert positive.confidence == 0.8


def test_link_destination_is_not_taken_from_a_word_inside_another_url() -> None:
    states = [
        {
            "state_code": "STATE-015",
            "url_pattern": "/en/admin",
            "reached_via": ["click(role=authentication,name='Log in')"],
            "elements": [
                {
                    "element_code": "EL-006",
                    "role": "link",
                    "name": "Dashboard",
                    "url": "https://app.test/en/admin",
                    "source": "OBSERVED_DOM",
                }
            ],
        },
        {
            "state_code": "STATE-023",
            "url_pattern": "/en/dic/dashboard/documents",
            "reached_via": [
                "navigate(url='https://app.test/en/dic/dashboard/documents',observed_link='Documents')"
            ],
            "elements": [],
        },
    ]
    cases = generate_cases_from_map(
        [{"id": "AC-1", "text": "After successful authentication, the user is redirected to the Super Admin Dashboard."}],
        states,
        "Super Admin Login",
    )
    for case in cases:
        assert validate_case(case, states, {"AC-1"}) == []
        assert case.steps[-1].target.state_code == "STATE-015"


def test_positive_case_without_observed_result_is_manual_not_an_invented_dashboard_assertion() -> None:
    states = [
        STATES[0],
        {**STATES[1], "reached_via": ["navigate(url='/dashboard',observed_link='Home')"]},
    ]
    acs = [{"id": "AC-1", "text": "User can log in with valid credentials"}]
    cases = generate_cases_from_map(acs, states, "Login")
    assert len(cases) == 1
    assert cases[0].steps[-1].assertion['kind'] == 'manual'
    assert cases[0].steps[-1].target.state_code == 'STATE-001'
    assert cases[0].expected_result == acs[0]['text']


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


def test_theme_toggle_is_not_used_when_the_case_names_another_control() -> None:
    states = [
        {
            "state_code": "STATE-003",
            "url_pattern": "/dashboard",
            "reached_via": [],
            "elements": [
                {"element_code": "EL-008", "role": "button", "name": "Toggle theme", "source": "OBSERVED_DOM"},
                {"element_code": "EL-020", "role": "link", "name": "Orders", "source": "OBSERVED_DOM"},
            ],
        }
    ]
    cases = generate_cases_from_map(
        [{"id": "AC-1", "text": "The employee can view a list of orders"}],
        states,
        "Orders",
    )
    assert cases == []  # no observed destination or result to assert


def test_theme_toggle_alone_is_not_clicked_for_an_unrelated_case() -> None:
    states = [
        {
            "state_code": "STATE-003",
            "url_pattern": "/dashboard",
            "reached_via": [],
            "elements": [
                {"element_code": "EL-008", "role": "button", "name": "Toggle theme", "source": "OBSERVED_DOM"},
            ],
        }
    ]
    cases = generate_cases_from_map(
        [{"id": "AC-1", "text": "The employee can view orders"}],
        states,
        "Orders",
    )
    assert all(step.action != "click" for case in cases for step in case.steps)
    for case in cases:
        assert validate_case(case, states, {"AC-1"}) == []


def test_visible_ui_case_asserts_the_matching_observed_element() -> None:
    states = [{
        "state_code": "STATE-003",
        "url_pattern": "/dashboard",
        "reached_via": [],
        "elements": [{
            "element_code": "EL-LOGO",
            "role": "image",
            "name": "Cafinity logo",
            "source": "OBSERVED_DOM",
        }],
    }]
    cases = generate_cases_from_map(
        [{"id": "AC-1", "text": "Cafinity logo is displayed"}], states, "Dashboard"
    )
    assert len(cases) == 1
    assertion = cases[0].steps[-1]
    assert assertion.target.element_code == "EL-LOGO"
    assert assertion.expected == "Cafinity logo is visible"


def test_observed_error_does_not_prove_arbitrary_credential_combinations() -> None:
    states = [
        STATES[0],
        {
            "state_code": "STATE-ERROR",
            "url_pattern": "/login/error",
            "reached_via": ["click LOGIN"],
            "elements": [{
                "element_code": "EL-ERR",
                "role": "alert",
                "name": "Invalid credentials",
                "source": "OBSERVED_DOM",
            }],
        },
    ]
    cases = generate_cases_from_map(
        [{"id": "AC-1", "text": "Invalid credentials are rejected"}], states, "Login"
    )
    assert len(cases) == 3  # invalid identifier, password, and both
    assert all(case.category == "NEGATIVE" for case in cases)
    assert all(case.steps[-1].assertion['kind'] == 'manual' for case in cases)
    assert len({case.steps[-1].assertion['fields'] for case in cases}) == 3


def test_unmapped_result_does_not_create_a_placeholder_case() -> None:
    states = [
        {
            "state_code": "STATE-010",
            "url_pattern": "/menu",
            "reached_via": ["navigate(url='https://cafinity.example/menu',observed_link='Menu')"],
            "elements": [
                {
                    "element_code": "EL-001",
                    "role": "button",
                    "name": "Add to Cart",
                    "source": "OBSERVED_DOM",
                }
            ],
        },
        {
            "state_code": "STATE-011",
            "url_pattern": "/cart",
            "reached_via": ["click(role=button,name='')"],
            "elements": [
                {
                    "element_code": "EL-002",
                    "role": "button",
                    "name": "Continue ordering",
                    "source": "OBSERVED_DOM",
                }
            ],
        },
    ]
    cases = generate_cases_from_map(
        [{"id": "AC-1", "text": "The employee can add an item from the menu"}],
        states,
        "Menu",
    )
    assert cases == []


def test_targeted_categories_without_observed_results_are_not_copied() -> None:
    acs = [{"id": "AC-1", "text": "User can log in"}]
    cases = generate_cases_from_map(acs, STATES, "Login", {"AC-1": {"NEGATIVE", "EDGE_CASE"}})
    assert cases == []


def test_dropdown_metadata_creates_one_case_unless_behavior_changes() -> None:
    options = [f"Option {index}" for index in range(10)]
    state = {
        "state_code": "STATE-010",
        "url_pattern": "/form",
        "reached_via": [],
        "elements": [
            {
                "element_code": "EL-001",
                "role": "combobox",
                "name": "Country",
                "source": "OBSERVED_DOM",
                "validation": {"options": options},
            },
            {
                "element_code": "EL-002",
                "role": "button",
                "name": "Continue",
                "source": "OBSERVED_DOM",
            },
        ],
    }
    cases = generate_cases_from_map([{"id": "AC-9", "text": "Choose a country"}], [state], "Form")
    selects = [
        step
        for case in cases
        if case.category == "POSITIVE"
        for step in case.steps
        if step.action == "select"
    ]
    assert len([case for case in cases if case.category == "POSITIVE"]) == 0
    assert len(selects) == 0
    state["elements"][0]["dropdown_dependencies"] = [
        {"option": "India", "reveals": ["textbox:state"], "navigates_to": None},
        {"option": "Japan", "reveals": ["textbox:prefecture"], "navigates_to": None},
    ]
    varied = generate_cases_from_map([{"id": "AC-9", "text": "Choose a country"}], [state], "Form")
    positive = [case for case in varied if case.category == "POSITIVE"]
    assert positive == []
