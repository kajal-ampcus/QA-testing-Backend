"""Each generated spec starts in the state its case actually needs."""

from uuid import uuid4

from core.agents.automation_generation.suite import CaseInput, StateInput, generate_suite


def _ids():
    return {
        "project_id": uuid4(),
        "requirement_id": uuid4(),
        "case_id": uuid4(),
        "map_id": uuid4(),
    }


def _case(ids, **overrides) -> CaseInput:
    data = dict(
        test_case_id=ids["case_id"],
        tc_code="TC-100",
        version=1,
        title="Sign in",
        requirement_id=ids["requirement_id"],
        requirement_version=1,
        application_map_id=ids["map_id"],
        application_map_version=1,
        project_id=ids["project_id"],
        steps=[],
        test_data={},
        expected_result="",
        credential_ref=None,
        preconditions=[],
        category="POSITIVE",
        objective="",
    )
    data.update(overrides)
    return CaseInput(**data)


def _render(tmp_path, case: CaseInput, states: dict[str, StateInput]):
    plan = generate_suite(
        suite_dir=tmp_path / "suite",
        generation_id=uuid4(),
        project_id=case.project_id,
        application_url="https://app.example",
        cases=[case],
        states=states,
    )
    specs = list(plan.suite_dir.rglob("*.spec.ts"))
    assert len(specs) == 1
    auth = (plan.suite_dir / "fixtures" / "auth.ts").read_text(encoding="utf-8")
    return plan, specs[0].read_text(encoding="utf-8"), auth


def _login_states() -> dict[str, StateInput]:
    return {
        "STATE-001": StateInput(
            state_code="STATE-001",
            url_pattern="https://app.example/login",
            elements=[
                {"element_code": "EL-001", "role": "textbox", "name": "Email", "risk": "SAFE", "source": "OBSERVED_DOM"},
                {"element_code": "EL-002", "role": "textbox", "name": "Password", "risk": "SAFE", "source": "OBSERVED_DOM"},
                {"element_code": "EL-003", "role": "textbox", "name": "Captcha", "risk": "SAFE", "source": "OBSERVED_DOM"},
                {"element_code": "EL-004", "role": "button", "name": "Login", "risk": "SAFE", "source": "OBSERVED_DOM"},
                {"element_code": "EL-005", "role": "link", "name": "Forgot password", "risk": "SAFE", "source": "OBSERVED_DOM"},
                {"element_code": "EL-006", "role": "button", "name": "Refresh captcha", "risk": "SAFE", "source": "OBSERVED_DOM"},
            ],
        ),
        "STATE-002": StateInput(
            state_code="STATE-002",
            url_pattern="https://app.example/home",
            elements=[
                {"element_code": "EL-010", "role": "heading", "name": "Welcome", "risk": "SAFE", "source": "OBSERVED_DOM"},
            ],
        ),
        "STATE-003": StateInput(
            state_code="STATE-003",
            url_pattern="https://app.example/reset",
            elements=[
                {"element_code": "EL-011", "role": "heading", "name": "Reset password", "risk": "SAFE", "source": "OBSERVED_DOM"},
            ],
        ),
    }


def test_login_success_uses_a_public_page_and_submits(tmp_path) -> None:
    ids = _ids()
    case = _case(
        ids,
        title="Sign in",
        objective="Authentication succeeds",
        expected_result="Welcome back",
        test_data={"email": "person@example.test", "password": "ValidPass1!"},
        steps=[
            {"step_number": 1, "action": "navigate", "target": {"state_code": "STATE-001"}},
            {
                "step_number": 2,
                "action": "fill",
                "target": {"state_code": "STATE-001", "element_code": "EL-001", "element_name": "Email"},
                "value": "{email}",
            },
            {
                "step_number": 3,
                "action": "fill",
                "target": {"state_code": "STATE-001", "element_code": "EL-002", "element_name": "Password"},
                "value": "{password}",
            },
            {
                "step_number": 4,
                "action": "assert",
                "target": {"state_code": "STATE-002", "element_code": "EL-010", "element_name": "Welcome"},
                "expected": "Welcome back",
            },
        ],
    )
    plan, spec, auth = _render(tmp_path, case, _login_states())
    assert plan.scripts[0].blocked is False
    assert "publicPage: page" in spec
    assert "sessionPage: page" not in spec
    assert "enterApplication" not in spec
    assert ".fill" in spec
    assert ".click" in spec
    assert "Welcome back" in spec
    assert "expectedState = \"LOGIN_PAGE\"" in spec
    public = auth.split("publicPage:", 1)[1].split("sessionPage:", 1)[0]
    assert "enterApplication" not in public
    assert "test.beforeEach" not in auth


def test_invalid_login_submits_a_negative_value(tmp_path) -> None:
    ids = _ids()
    case = _case(
        ids,
        tc_code="TC-101",
        title="Invalid login is rejected",
        category="NEGATIVE",
        objective="Invalid credentials are rejected",
        expected_result="The sign-in is rejected",
        test_data={"email": "person@example.test", "password": "ValidPass1!"},
        steps=[
            {"step_number": 1, "action": "navigate", "target": {"state_code": "STATE-001"}},
            {
                "step_number": 2,
                "action": "fill",
                "target": {"state_code": "STATE-001", "element_code": "EL-001", "element_name": "Email"},
                "value": "{email}",
            },
            {
                "step_number": 3,
                "action": "fill",
                "target": {"state_code": "STATE-001", "element_code": "EL-002", "element_name": "Password"},
                "value": "{password}",
            },
            {
                "step_number": 4,
                "action": "assert",
                "target": {"state_code": "STATE-001"},
                "expected": "The sign-in is rejected",
            },
        ],
    )
    plan, spec, _auth = _render(tmp_path, case, _login_states())
    assert plan.scripts[0].blocked is False
    assert "publicPage: page" in spec
    assert "enterApplication" not in spec
    assert '"wrong"' in spec
    assert ".click" in spec
    assert "rejected" in spec


def test_forgot_password_does_not_fill_credentials(tmp_path) -> None:
    ids = _ids()
    case = _case(
        ids,
        tc_code="TC-102",
        title="Open the forgot password page",
        objective="The forgot password link opens the reset page",
        expected_result="The reset page is displayed",
        test_data={"email": "person@example.test", "password": "ValidPass1!", "captcha": "1234"},
        steps=[
            {"step_number": 1, "action": "navigate", "target": {"state_code": "STATE-001"}},
            {
                "step_number": 2,
                "action": "fill",
                "target": {"state_code": "STATE-001", "element_code": "EL-001", "element_name": "Email"},
                "value": "{email}",
            },
            {
                "step_number": 3,
                "action": "fill",
                "target": {"state_code": "STATE-001", "element_code": "EL-002", "element_name": "Password"},
                "value": "{password}",
            },
            {
                "step_number": 4,
                "action": "fill",
                "target": {"state_code": "STATE-001", "element_code": "EL-003", "element_name": "Captcha"},
                "value": "{captcha}",
            },
            {
                "step_number": 5,
                "action": "click",
                "target": {"state_code": "STATE-001", "element_code": "EL-005", "element_name": "Forgot password"},
            },
            {
                "step_number": 6,
                "action": "assert",
                "target": {"state_code": "STATE-003", "element_code": "EL-011", "element_name": "Reset password"},
                "expected": "Reset password",
            },
        ],
    )
    plan, spec, _auth = _render(tmp_path, case, _login_states())
    assert plan.scripts[0].blocked is False
    assert "publicPage: page" in spec
    assert "enterApplication" not in spec
    assert ".fill" not in spec
    assert ".click" in spec
    assert "Reset password" in spec


def test_captcha_refresh_stays_on_the_public_page(tmp_path) -> None:
    ids = _ids()
    case = _case(
        ids,
        tc_code="TC-103",
        title="Refresh the CAPTCHA image",
        objective="The CAPTCHA refresh control loads a new image",
        expected_result="A new CAPTCHA image is shown",
        test_data={"email": "person@example.test", "password": "ValidPass1!", "captcha": ""},
        steps=[
            {"step_number": 1, "action": "navigate", "target": {"state_code": "STATE-001"}},
            {
                "step_number": 2,
                "action": "fill",
                "target": {"state_code": "STATE-001", "element_code": "EL-001", "element_name": "Email"},
                "value": "{email}",
            },
            {
                "step_number": 3,
                "action": "fill",
                "target": {"state_code": "STATE-001", "element_code": "EL-002", "element_name": "Password"},
                "value": "{password}",
            },
            {
                "step_number": 4,
                "action": "click",
                "target": {"state_code": "STATE-001", "element_code": "EL-006", "element_name": "Refresh captcha"},
            },
            {
                "step_number": 5,
                "action": "assert",
                "target": {"state_code": "STATE-001"},
                "expected": "A new CAPTCHA image is shown",
            },
        ],
    )
    plan, spec, auth = _render(tmp_path, case, _login_states())
    assert plan.scripts[0].blocked is False
    assert "publicPage: page" in spec
    assert "enterApplication" not in spec
    assert ".fill" not in spec
    assert ".click" in spec
    assert "await enterApplication(page)" in auth
    public = auth.split("publicPage:", 1)[1].split("sessionPage:", 1)[0]
    assert "enterApplication" not in public


def test_protected_page_signs_in_for_that_test_only(tmp_path) -> None:
    ids = _ids()
    case = _case(
        ids,
        tc_code="TC-104",
        title="View the orders list",
        objective="A signed-in user can open orders",
        preconditions=["The user is signed in"],
        expected_result="The orders list is visible",
        steps=[
            {"step_number": 1, "action": "navigate", "target": {"state_code": "STATE-010"}},
            {
                "step_number": 2,
                "action": "click",
                "target": {"state_code": "STATE-010", "element_code": "EL-020", "element_name": "Orders"},
            },
            {
                "step_number": 3,
                "action": "assert",
                "target": {"state_code": "STATE-010", "element_code": "EL-021", "element_name": "Orders"},
                "expected": "Orders",
            },
        ],
    )
    states = {
        "STATE-010": StateInput(
            state_code="STATE-010",
            url_pattern="https://app.example/orders",
            elements=[
                {"element_code": "EL-020", "role": "link", "name": "Orders", "risk": "SAFE", "source": "OBSERVED_DOM"},
                {"element_code": "EL-021", "role": "heading", "name": "Orders", "risk": "SAFE", "source": "OBSERVED_DOM"},
            ],
        )
    }
    plan, spec, auth = _render(tmp_path, case, states)
    assert plan.scripts[0].blocked is False
    assert "sessionPage: page" in spec
    assert "publicPage: page" not in spec
    assert "expectedState = \"AUTHENTICATED\"" in spec
    assert "openContext(browser, true)" in auth
    assert "await enterApplication(page)" in auth
    assert "keepSignedInSession(page)" in auth
    assert "test.beforeEach" not in auth


def test_unconfigured_captcha_answer_blocks_the_case(tmp_path) -> None:
    ids = _ids()
    case = _case(
        ids,
        tc_code="TC-105",
        title="Sign in",
        objective="Authentication succeeds",
        expected_result="Welcome back",
        test_data={"email": "person@example.test", "password": "ValidPass1!", "captcha": ""},
        steps=[
            {"step_number": 1, "action": "navigate", "target": {"state_code": "STATE-001"}},
            {
                "step_number": 2,
                "action": "fill",
                "target": {"state_code": "STATE-001", "element_code": "EL-003", "element_name": "Captcha"},
                "value": "{captcha}",
            },
            {
                "step_number": 3,
                "action": "click",
                "target": {"state_code": "STATE-001", "element_code": "EL-004", "element_name": "Login"},
            },
            {
                "step_number": 4,
                "action": "assert",
                "target": {"state_code": "STATE-002", "element_code": "EL-010", "element_name": "Welcome"},
                "expected": "Welcome back",
            },
        ],
    )
    plan, spec, _auth = _render(tmp_path, case, _login_states())
    assert plan.scripts[0].blocked is True
    assert "CAPTCHA" in (plan.scripts[0].blocked_reason or "")
    assert "test.fixme" in spec
    assert "enterApplication" not in spec
