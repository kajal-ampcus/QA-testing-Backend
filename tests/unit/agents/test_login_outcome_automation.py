from copy import deepcopy
from uuid import uuid4

from core.agents.automation_generation.suite import CaseInput, StateInput, _plan_case
from core.agents.test_design.form_behaviors import login_success_case


def inputs():
    login = StateInput('LOGIN', '/', [
        {'element_code': 'EMAIL', 'role': 'textbox', 'name': 'Work email', 'input_type': 'text', 'source': 'OBSERVED_DOM'},
        {'element_code': 'PWD', 'role': 'textbox', 'name': 'Password', 'input_type': 'password', 'css_selector': '#password', 'source': 'OBSERVED_DOM'},
        {'element_code': 'SUBMIT', 'role': 'button', 'name': 'Sign in', 'source': 'OBSERVED_DOM'},
    ])
    dashboard = StateInput('DASH', '/', [{'element_code': 'HEAD', 'role': 'heading', 'name': 'Dashboard', 'source': 'OBSERVED_DOM'}],
                           ["click(role=authentication,name='Sign in with provided values')"])
    case = CaseInput(test_case_id=uuid4(), tc_code='TC-100', version=1, title='Sign in with valid credentials',
        requirement_id=uuid4(), requirement_version=1, application_map_id=uuid4(), application_map_version=1,
        project_id=uuid4(), category='POSITIVE', objective='on success it redirects the user to the dashboard',
        expected_result='on success it redirects the user to the dashboard', credential_ref='cred:test',
        preconditions=['Manual verification required', 'Replace INVALID_TEST_FIXTURE values'], test_data={'email': 'VALID_TEST_FIXTURE:email', 'password': 'VALID_TEST_FIXTURE:password'},
        steps=[{'action': 'navigate', 'target': {'state_code': 'LOGIN'}},
               {'action': 'fill', 'target': {'state_code': 'LOGIN', 'element_code': 'EMAIL'}, 'value': '{email}'},
               {'action': 'fill', 'target': {'state_code': 'LOGIN', 'element_code': 'PWD'}, 'value': '{password}'},
               {'action': 'click', 'target': {'state_code': 'LOGIN', 'element_code': 'SUBMIT'}},
               {'action': 'assert', 'target': {'state_code': 'LOGIN', 'element_code': 'SUBMIT'}, 'assertion': {'kind': 'manual', 'basis': 'valid'}, 'expected': 'on success it redirects the user to the dashboard'}])
    return case, {'LOGIN': login, 'DASH': dashboard}


def test_existing_manual_login_uses_observed_dashboard_and_account():
    case, states = inputs()
    draft = _plan_case(case, states, set(), uuid4())
    assert not draft.blocked, draft.blocked_reason
    assert 'envCredential("TEST_USERNAME")' in draft.source
    assert 'envCredential("TEST_PASSWORD")' in draft.source
    assert 'expectDashboardHeadVisible' in draft.source
    assert 'clickSignInSubmit' in draft.source
    assert 'waitForReady' not in draft.source


def test_ambiguous_destination_or_missing_account_stays_blocked():
    case, states = inputs()
    other = deepcopy(states['DASH'])
    other.state_code = 'OTHER'
    states['OTHER'] = other
    assert _plan_case(deepcopy(case), states, set(), uuid4()).blocked
    del states['OTHER']
    case.credential_ref = None
    assert 'saved test account' in _plan_case(case, states, set(), uuid4()).blocked_reason


def test_login_cannot_use_another_accounts_discovery_evidence():
    case, states = inputs()
    states['DASH'].account_context = 'authenticated::cred:other'
    assert _plan_case(case, states, set(), uuid4()).blocked


def test_new_login_case_uses_observed_dashboard():
    _, states = inputs()
    rows = [dict(state_code=s.state_code, url_pattern=s.url_pattern, elements=s.elements, reached_via=s.reached_via) for s in states.values()]
    case = login_success_case('AC-4', 'Sign in validates credentials; on success it redirects to the dashboard', rows[0], rows)
    assert case.steps[-1].target.state_code == 'DASH'
    assert case.steps[-1].assertion['kind'] == 'visible'
