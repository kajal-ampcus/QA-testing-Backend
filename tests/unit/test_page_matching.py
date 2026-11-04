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
