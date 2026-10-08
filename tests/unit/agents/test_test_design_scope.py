"""Graph-scope selection for incremental test generation."""

from types import SimpleNamespace

from core.agents.test_design.agent import _states_for_generation
from core.agents.test_design.agent import _branch_fingerprints
import pytest


def test_completed_branch_selects_only_its_result():
    checkpoint = {"jobs": [
        {"key": "login", "status": "completed", "result_fingerprint": "dashboard"},
        {"key": "menu", "status": "available"},
    ]}
    assert _branch_fingerprints(checkpoint, ["login"]) == {"dashboard"}
    for key in ["menu", "unknown"]:
        with pytest.raises(ValueError, match="completed"):
            _branch_fingerprints(checkpoint, [key])


def test_legacy_branch_resolves_recorded_edge_without_including_siblings():
    checkpoint = {"jobs": [{"key": "menu", "status": "completed", "parent_fingerprint": "dashboard",
        "path": [{"role": "link", "name": "Menu", "url": "https://example.test/menu"}]}],
        "graph": {"edges": [
            {"parent_fingerprint": "dashboard", "child_fingerprint": "menu-state", "action": "navigate(url='https://example.test/menu',observed_link='Menu')"},
            {"parent_fingerprint": "dashboard", "child_fingerprint": "orders-state", "action": "Orders"},
        ]}}
    assert _branch_fingerprints(checkpoint, ["menu"]) == {"menu-state"}


def test_whole_graph_generation_keeps_every_state() -> None:
    states = [SimpleNamespace(fingerprint="a"), SimpleNamespace(fingerprint="b")]

    assert _states_for_generation(states, "all", {"a"}) == states


def test_incremental_generation_excludes_fingerprints_from_prior_map_versions() -> None:
    old = SimpleNamespace(fingerprint="stable-login")
    new = SimpleNamespace(fingerprint="new-report")

    assert _states_for_generation(
        [old, new], "ungenerated", {"stable-login"}
    ) == [new]


def test_selected_area_uses_only_that_portion_of_combined_graph() -> None:
    login = SimpleNamespace(fingerprint="login")
    dashboard = SimpleNamespace(fingerprint="dashboard")

    assert _states_for_generation(
        [login, dashboard], "all", set(), {"login"}
    ) == [login]
