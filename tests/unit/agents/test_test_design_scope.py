"""Graph-scope selection for incremental test generation."""

from types import SimpleNamespace

from core.agents.test_design.agent import _states_for_generation


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
