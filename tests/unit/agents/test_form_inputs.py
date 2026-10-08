"""Form snapshots become input requests only when required fields block a safe submit."""

from core.agents.application_discovery.form_inputs import (
    answers_cover,
    blocking_form,
    form_key_for,
    is_otp_field,
    missing_fields,
    saved_field_values,
    value_for_field,
)


def test_required_form_asks_for_its_fields():
    form = blocking_form(
        [
            {"role": "textbox", "name": "Start date", "required": True, "input_type": "date"},
            {"role": "button", "name": "Search"},
        ]
    )
    assert form is not None
    assert form["kind"] == "form"
    assert form["fields"][0]["name"] == "Start date"
    assert form["fields"][0]["key"] == "textbox:start date"
    assert form["submit"] == {"role": "button", "name": "Search"}
    assert "value" not in form["fields"][0]


def test_optional_fields_and_destructive_submits_are_not_requests():
    assert blocking_form(
        [
            {"role": "textbox", "name": "Notes"},
            {"role": "button", "name": "Save"},
        ]
    ) is None
    assert blocking_form(
        [
            {"role": "textbox", "name": "Reason", "required": True},
            {"role": "button", "name": "Delete record"},
        ]
    ) is None


def test_saved_login_fields_match_by_accessible_name():
    secret = {
        "username": "ada@example.com",
        "password": "secret",
        "fields": [{"name": "Tenant", "value": "north"}],
    }
    saved = saved_field_values(secret)
    tenant = {"key": "textbox:tenant", "role": "textbox", "name": "Tenant", "required": True}
    email = {"key": "textbox:email address", "role": "textbox", "name": "Email address", "required": True}
    assert value_for_field(tenant, saved, secret) == "north"
    assert value_for_field(email, saved, secret) == "ada@example.com"
    assert missing_fields([tenant, email], saved, secret) == []
    assert answers_cover([tenant], {"textbox:tenant": "north"})
    assert not answers_cover([tenant], {})


def test_form_key_depends_on_field_identity_not_order():
    first = form_key_for([{"key": "textbox:b"}, {"key": "textbox:a"}])
    second = form_key_for([{"key": "textbox:a"}, {"key": "textbox:b"}])
    assert first == second
    assert is_otp_field("One-time code")
    assert not is_otp_field("CAPTCHA answer")
