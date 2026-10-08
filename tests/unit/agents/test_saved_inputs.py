"""Saved answers stay on the project and account that produced them."""

import uuid

from core.agents.application_discovery.form_inputs import (
    InvalidCredentialsError,
    blocking_form,
    challenges_ready,
    is_ephemeral_field,
    should_retry_login,
    split_saved_values,
)
from infra.db.repositories.form_answer_repo import row_matches, select_answer_scope


def test_ephemeral_challenges_are_not_reused():
    values = {"textbox:password": "secret", "textbox:one-time code": "123456"}
    fields = [
        {"key": "textbox:password", "name": "Password"},
        {"key": "textbox:one-time code", "name": "One-time code"},
    ]
    durable, session = split_saved_values(values, fields)
    assert durable == {"textbox:password": "secret"}
    assert session == {"textbox:one-time code": "123456"}
    assert is_ephemeral_field("session token")
    assert challenges_ready(fields, durable, {}) is False
    assert challenges_ready(fields, durable, session) is True


def test_saved_answers_do_not_cross_projects_or_accounts():
    project_a = uuid.uuid4()
    project_b = uuid.uuid4()
    row = {
        "project_id": project_a,
        "page_key": "/login",
        "form_key": "abc",
        "credential_scope": "cred:1",
    }
    assert row_matches(row, project_a, "/login", "abc", "cred:1", login=True)
    assert not row_matches(row, project_b, "/login", "abc", "cred:1", login=True)
    assert not row_matches(row, project_a, "/login", "abc", "cred:2", login=True)
    assert select_answer_scope(["cred:1", ""], "cred:2", login=False) == ""
    assert select_answer_scope(["cred:1", ""], "cred:2", login=True) is None


def test_invalid_credentials_stop_without_another_attempt():
    attempts = 0
    try:
        for attempt in range(3):
            attempts += 1
            if not should_retry_login("Invalid email or password", attempt):
                raise InvalidCredentialsError("rejected")
    except InvalidCredentialsError:
        pass
    assert attempts == 1
    assert should_retry_login("incorrect captcha", 0)


def test_dropdown_with_options_is_not_an_input_request():
    form = blocking_form(
        [
            {"role": "combobox", "name": "Country", "required": True, "options": ["India", "Japan"]},
            {"role": "button", "name": "Search"},
        ]
    )
    assert form is None
