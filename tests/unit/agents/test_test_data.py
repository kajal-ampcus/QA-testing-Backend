from core.agents.test_data.agent import value_for_element


def test_email_field_gets_valid_and_invalid_values() -> None:
    element = {"name": "Email", "role": "textbox", "element_code": "EL-001"}
    assert "@" in value_for_element(element, "POSITIVE").value
    assert "@" not in value_for_element(element, "NEGATIVE").value
    assert value_for_element(element, "EDGE_CASE").value != value_for_element(element, "POSITIVE").value


def test_login_specific_fields_get_safe_scenario_values() -> None:
    employee_id = {"name": "Employee ID", "role": "textbox", "element_code": "EL-EMP"}
    captcha = {"name": "CAPTCHA answer", "role": "textbox", "element_code": "EL-CAP"}

    assert value_for_element(employee_id, "POSITIVE").value == "EMP001"
    assert value_for_element(employee_id, "NEGATIVE").value == "' OR '1'='1"
    assert value_for_element(employee_id, "EDGE_CASE").value == "A" * 256
    assert value_for_element(captcha, "POSITIVE").value.isdigit()
    assert value_for_element(captcha, "NEGATIVE").value == "not-a-number"
