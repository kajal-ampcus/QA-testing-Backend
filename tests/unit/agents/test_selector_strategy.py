from core.agents.automation_generation.selector_strategy import resolve_in_state


def test_role_and_name_win_over_weaker_observed_selectors() -> None:
    decision = resolve_in_state(
        "STATE-001",
        "EL-001",
        [
            {
                "element_code": "EL-001",
                "role": "button",
                "name": "Save",
                "data-testid": "save",
                "dom_id": "save-button",
                "xpath": "//button[@id='save']",
                "uid": "mcp-uid-99",
                "risk": "REVIEW",
                "source": "OBSERVED_DOM",
            }
        ],
    )
    assert decision.status == "resolved"
    assert decision.strategy == "getByRole"
    assert decision.expression == 'getByRole("button", { name: "Save", exact: true })'
    assert decision.expression is not None
    assert "mcp-uid-99" not in decision.expression
    assert decision.evidence["state_code"] == "STATE-001"
    assert decision.evidence["element_code"] == "EL-001"
    assert decision.xpath_fallback is False


def test_placeholder_precedes_test_id_and_css() -> None:
    decision = resolve_in_state(
        "STATE-001",
        "EL-002",
        [{"element_code": "EL-002", "placeholder": "Email", "data-testid": "email", "dom_id": "email"}],
    )
    assert decision.strategy == "getByPlaceholder"


def test_test_id_precedes_stable_id() -> None:
    decision = resolve_in_state(
        "STATE-001",
        "EL-003",
        [{"element_code": "EL-003", "data-testid": "email", "dom_id": "email"}],
    )
    assert decision.strategy == "getByTestId"


def test_stable_id_is_used_when_nothing_stronger_was_observed() -> None:
    decision = resolve_in_state(
        "STATE-001",
        "EL-004",
        [{"element_code": "EL-004", "dom_id": "email"}],
    )
    assert decision.strategy == "css"
    assert decision.expression == 'locator("#email")'


def test_xpath_is_only_the_fallback_and_is_flagged() -> None:
    decision = resolve_in_state(
        "STATE-001",
        "EL-005",
        [{"element_code": "EL-005", "dom_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6", "xpath": "//input[@name='q']"}],
    )
    assert decision.strategy == "xpath"
    assert decision.xpath_fallback is True
    assert decision.expression is not None and decision.expression.startswith('locator("xpath=')


def test_missing_metadata_does_not_invent_a_selector() -> None:
    decision = resolve_in_state(
        "STATE-001",
        "EL-009",
        [{"element_code": "EL-009", "uid": "1_2"}],
    )
    assert decision.status == "blocked"
    assert decision.expression is None
    assert decision.strategy is None


def test_ambiguous_role_name_blocks_instead_of_guessing() -> None:
    elements = [
        {"element_code": "EL-001", "role": "button", "name": "Save", "dom_id": "save-a"},
        {"element_code": "EL-002", "role": "button", "name": "Save", "dom_id": "save-b"},
    ]
    decision = resolve_in_state("STATE-001", "EL-001", elements)
    assert decision.status == "blocked"
    assert "ambiguous" in (decision.reason or "").lower() or "matches" in (decision.reason or "")
    assert decision.expression is None


def test_element_code_is_resolved_inside_its_state() -> None:
    state_one = [{"element_code": "EL-001", "role": "textbox", "name": "Email"}]
    state_two = [{"element_code": "EL-001", "role": "button", "name": "Continue"}]
    first = resolve_in_state("STATE-001", "EL-001", state_one)
    second = resolve_in_state("STATE-002", "EL-001", state_two)
    assert first.expression is not None and "Email" in first.expression
    assert second.expression is not None and "Continue" in second.expression
    assert "Email" not in second.expression
