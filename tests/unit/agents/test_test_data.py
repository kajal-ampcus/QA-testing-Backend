from core.agents.test_data.agent import value_for_element


def test_email_field_gets_valid_and_invalid_values() -> None:
    element = {"name": "Email", "role": "textbox", "element_code": "EL-001"}
    assert "@" in value_for_element(element, "POSITIVE").value
    assert "@" not in value_for_element(element, "NEGATIVE").value
    assert value_for_element(element, "EDGE_CASE").value != value_for_element(element, "POSITIVE").value
