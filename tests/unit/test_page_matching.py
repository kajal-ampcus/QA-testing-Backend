from types import SimpleNamespace

from core.services.page_matching import PageMatchingService


def state(code, url, controls=()):
    return SimpleNamespace(state_code=code, url_pattern=url, reached_via=[], elements=[{"name": name} for name in controls])


def test_url_and_control_match_selects_observed_page():
    result = PageMatchingService().match({"title": "Filter dashboard reports", "url": "/dashboard", "steps": "Click Filter"}, [
        state("LOGIN", "/login", ["Email", "Password"]), state("DASH", "/dashboard", ["Filter", "Reports"]),
    ])
    assert result.state_code == "DASH"
    assert result.confidence >= 80


def test_unknown_case_is_unmapped_not_first_state():
    result = PageMatchingService().match({"title": "Print invoice"}, [state("LOGIN", "/login", ["Email"])])
    assert result.state_code is None


def test_starting_login_form_beats_dashboard_in_expected_result():
    login = SimpleNamespace(state_code='LOGIN', url_pattern='/', reached_via=[], elements=[
        {'role': 'textbox', 'name': 'Work email'}, {'role': 'textbox', 'name': 'Password', 'input_type': 'password'},
        {'role': 'button', 'name': 'Sign in'}])
    dashboard = SimpleNamespace(state_code='DASH', url_pattern='/', reached_via=['authentication'], elements=[{'role': 'heading', 'name': 'Dashboard'}])
    result = PageMatchingService().match({'title': 'Sign in', 'preconditions': ['Login page is open'],
        'step_notes': ['Enter credentials', 'Click Sign in'], 'expected_result': 'Dashboard is visible'}, [dashboard, login])
    assert result.state_code == 'LOGIN'
    assert result.page_name == 'Login Page'


def test_equal_low_confidence_matches_are_ambiguous():
    result = PageMatchingService().match({'title': 'Check password'}, [state('A', '/a', ['Password']), state('B', '/b', ['Password'])])
    assert result.ambiguous


def test_only_observed_option_values_contribute_without_exposing_input():
    matching = SimpleNamespace(state_code='DASH', url_pattern='/dashboard', reached_via=[], elements=[{'name': 'Status', 'options': ['Approved', 'Returned']}])
    other = state('OTHER', '/other', ['Status'])
    result = PageMatchingService().match({'title': 'Filter by status', 'input': 'Approved'}, [other, matching])
    assert result.state_code is None  # Not enough evidence for an automatic selection.
    assert result.alternatives[0]['state_code'] == 'DASH'
    assert result.alternatives[0]['confidence'] > result.alternatives[1]['confidence']
    assert 'Approved' not in str(result)
