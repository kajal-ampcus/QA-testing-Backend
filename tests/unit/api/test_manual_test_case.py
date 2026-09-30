from apps.api.routers.v1.test_cases import _manual_steps


def test_empty_notes_adds_navigate_and_expected_assert():
    steps = _manual_steps(
        state_code="STATE-001",
        expected_result="Validation error is shown",
        step_notes=["", "  "],
    )
    assert [step["action"] for step in steps] == ["navigate", "assert"]
    assert steps[0]["target"]["state_code"] == "STATE-001"
    assert steps[1]["expected"] == "Validation error is shown"


def test_step_notes_become_asserts():
    steps = _manual_steps(
        state_code="STATE-002",
        expected_result="unused",
        step_notes=["Enter quantity 0", "Submit the order"],
    )
    assert [step["expected"] for step in steps] == [
        None,
        "Enter quantity 0",
        "Submit the order",
    ]
    assert [step["action"] for step in steps] == ["navigate", "assert", "assert"]
