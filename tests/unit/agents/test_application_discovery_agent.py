from core.agents.application_discovery.agent import _select_auth_flow


def test_auth_flow_uses_login_url_fallback_when_catalog_is_empty() -> None:
    selected_id, selected_flow = _select_auth_flow(None, None)

    assert selected_id is None
    assert selected_flow is None


def test_auth_flow_auto_selects_the_only_discovered_flow() -> None:
    flow = {"id": "employee-login", "url_pattern": "/login"}

    selected_id, selected_flow = _select_auth_flow(
        {"authentication_flows": [flow]}, None
    )

    assert selected_id == "employee-login"
    assert selected_flow == flow


def test_auth_flow_does_not_guess_between_multiple_flows() -> None:
    checkpoint = {
        "authentication_flows": [
            {"id": "employee-login", "url_pattern": "/login"},
            {"id": "admin-login", "url_pattern": "/login"},
        ]
    }

    selected_id, selected_flow = _select_auth_flow(checkpoint, None)

    assert selected_id is None
    assert selected_flow is None


def test_auth_flow_honors_an_explicit_selection() -> None:
    admin = {"id": "admin-login", "url_pattern": "/login"}
    checkpoint = {
        "authentication_flows": [
            {"id": "employee-login", "url_pattern": "/login"},
            admin,
        ]
    }

    selected_id, selected_flow = _select_auth_flow(checkpoint, "admin-login")

    assert selected_id == "admin-login"
    assert selected_flow == admin
