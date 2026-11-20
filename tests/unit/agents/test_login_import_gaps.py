from copy import deepcopy
from uuid import uuid4

from core.agents.application_discovery.parallel_crawler import ParallelCrawler, _is_login_form_chrome
from core.agents.automation_generation.suite import StateInput, _plan_case
from core.agents.test_design.scenarios import plan_scenarios
from tests.unit.agents.test_login_outcome_automation import inputs


def test_imported_login_visibility_checks_each_observed_control():
    case, states = inputs()
    state = states['LOGIN']
    state.elements.append({'element_code': 'FORGOT', 'role': 'button', 'name': 'Forgot password?', 'input_type': 'button', 'source': 'OBSERVED_DOM'})
    case.title = 'Imported test case 1'
    case.objective = 'Verify login controls'
    case.expected_result = 'Email ID, Password, Forgot Password link, and Sign In button should be displayed.'
    case.steps = [{'action': 'navigate', 'target': {'state_code': 'LOGIN'}}, {'action': 'assert', 'target': {'state_code': 'LOGIN'}, 'expected': case.expected_result}]
    draft = _plan_case(case, states, set(), uuid4())
    assert not draft.blocked, draft.blocked_reason
    assert sum(method.endswith('Visible') for _, method, _ in draft.calls) == 4
    assert not any(method.startswith('click') for _, method, _ in draft.calls)


def test_forgot_password_requires_observed_destination_and_checks_heading():
    case, states = inputs()
    states['LOGIN'].elements.append({'element_code': 'FORGOT', 'role': 'button', 'name': 'Forgot password?', 'input_type': 'button', 'source': 'OBSERVED_DOM'})
    case.title = 'Imported test case 7'
    case.objective = 'Verify password recovery'
    case.expected_result = 'Forgot Password page or reset flow should open successfully.'
    case.steps = [{'action': 'navigate', 'target': {'state_code': 'LOGIN'}}, {'action': 'assert', 'target': {'state_code': 'LOGIN'}, 'expected': case.expected_result}]
    assert _plan_case(deepcopy(case), states, set(), uuid4()).blocked
    states['RESET'] = StateInput('RESET', '/reset', [{'element_code': 'HEAD', 'role': 'heading', 'name': 'Reset password', 'source': 'OBSERVED_DOM'}], ["click(role=button,name='Forgot password?')"])
    draft = _plan_case(case, states, set(), uuid4())
    assert not draft.blocked, draft.blocked_reason
    assert any(method.startswith('clickForgot') for _, method, _ in draft.calls)
    assert any(state == 'RESET' and method.endswith('Visible') for state, method, _ in draft.calls)


def test_recovery_button_is_navigable_without_allowing_submit():
    nodes = [{'role': 'textbox', 'name': 'Password'}, {'role': 'button', 'name': 'Forgot password?', 'input_type': 'button'}]
    assert not _is_login_form_chrome(nodes, 'button', None, 'Forgot password?')
    assert _is_login_form_chrome(nodes[:1], 'button', None, 'Forgot password?')
    assert _is_login_form_chrome(nodes, 'button', None, 'Show password')
    assert ParallelCrawler._safe_navigation({'role': 'button', 'name': 'Forgot password?', 'input_type': 'button'})
    assert not ParallelCrawler._safe_navigation({'role': 'button', 'name': 'Forgot password?', 'input_type': 'submit'})


def test_invalid_password_is_not_linked_to_email_only_requirement():
    _, states = inputs()
    rows = [dict(state_code=s.state_code, url_pattern=s.url_pattern, elements=s.elements, reached_via=s.reached_via) for s in states.values()]
    criteria = [{'id': 'EMAIL', 'text': 'Email validates format and shows an error for invalid input'},
                {'id': 'LOGIN', 'text': 'Sign in validates email and password; on failure displays an error message'}]
    cases = plan_scenarios(criteria, rows, 'Login', 'Sign in validates password for invalid input and displays an error message').cases
    password_cases = [case for case in cases if case.title == 'Submit with invalid Password']
    assert password_cases
    assert all('EMAIL' not in case.traceability for case in password_cases)


def test_import_preserves_steps_and_final_expected_result():
    from apps.api.routers.v1.test_cases import ImportCaseRow, _manual_steps
    row = ImportCaseRow(row_number=2, title='Login', input='invalid email', scenario_name='Validation',
                        expected_result='An error is displayed', step_notes=['Enter invalid email', 'Click Sign in'], inferred=['Objective'])
    assert row.input == 'invalid email'
    assert row.scenario_name == 'Validation'
    steps = _manual_steps(state_code='LOGIN', expected_result=row.expected_result, step_notes=row.step_notes)
    assert [step['expected'] for step in steps[1:]] == ['Enter invalid email', 'Click Sign in', 'An error is displayed']


def test_imported_password_masking_checks_input_type_without_submitting():
    case, states = inputs()
    case.title = 'Imported test case 3'
    case.expected_result = 'Password characters should be masked by default.'
    case.steps = [{'action': 'navigate', 'target': {'state_code': 'LOGIN'}}, {'action': 'assert', 'target': {'state_code': 'LOGIN'}, 'expected': case.expected_result}]
    draft = _plan_case(case, states, set(), uuid4())
    assert not draft.blocked, draft.blocked_reason
    assert any(method.endswith('Attribute') and argument == '"type", "password"' for _, method, argument in draft.calls)
    assert not any(method.startswith('click') for _, method, _ in draft.calls)
