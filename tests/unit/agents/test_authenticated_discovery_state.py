from copy import deepcopy
from uuid import uuid4

from core.agents.automation_generation.suite import StateInput, _plan_case
from tests.unit.agents.test_login_outcome_automation import inputs


def dashboard_case():
    case, states = inputs()
    states['DASH'].elements = [{'element_code': 'OUTCOME', 'role': 'heading', 'name': 'Outcome', 'source': 'OBSERVED_DOM'}]
    states['DASH'].account_context = 'authenticated::cred:test'
    case.title = 'Outcome heading is visible'
    case.objective = 'Verify the observed Outcome heading'
    case.expected_result = 'Outcome is visible'
    case.preconditions = ['Browser is on /']
    case.steps = [{'action': 'navigate', 'target': {'state_code': 'DASH'}},
        {'action': 'assert', 'target': {'state_code': 'DASH', 'element_code': 'OUTCOME'}, 'expected': 'Outcome is visible',
         'assertion': {'kind': 'visible', 'target': 'OUTCOME'}}]
    return case, states


def test_dashboard_discovered_after_login_uses_authenticated_fixture_even_at_root():
    case, states = dashboard_case()
    draft = _plan_case(case, states, set(), uuid4())
    assert not draft.blocked, draft.blocked_reason
    assert 'sessionPage: page' in draft.source
    assert 'publicPage: page' not in draft.source


def test_discovered_account_must_match_and_be_available():
    case, states = dashboard_case()
    case.credential_ref = None
    assert 'saved test account' in _plan_case(deepcopy(case), states, set(), uuid4()).blocked_reason
    case.credential_ref = 'cred:other'
    assert 'different account' in _plan_case(case, states, set(), uuid4()).blocked_reason


def test_public_page_at_same_url_does_not_inherit_authenticated_session():
    case, states = dashboard_case()
    states['PUBLIC'] = StateInput('PUBLIC', '/', [{'element_code': 'TITLE', 'role': 'heading', 'name': 'Welcome', 'source': 'OBSERVED_DOM'}])
    case.title = 'Welcome is visible'
    case.objective = case.title
    case.expected_result = case.title
    case.steps = [{'action': 'navigate', 'target': {'state_code': 'PUBLIC'}},
                  {'action': 'assert', 'target': {'state_code': 'PUBLIC', 'element_code': 'TITLE'}, 'expected': 'Welcome is visible', 'assertion': {'kind': 'visible', 'target': 'TITLE'}}]
    draft = _plan_case(case, states, set(), uuid4())
    assert not draft.blocked, draft.blocked_reason
    assert 'publicPage: page' in draft.source
