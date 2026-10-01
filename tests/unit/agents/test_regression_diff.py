from core.agents.regression_maintenance.diffing import diff_application_maps


def test_added_state_is_a_functional_change() -> None:
    previous = {"nodes": [{"fingerprint": "a", "url_pattern": "/login"}]}
    current = {
        "nodes": [
            {"fingerprint": "a", "url_pattern": "/login"},
            {"fingerprint": "b", "url_pattern": "/home"},
        ]
    }
    diff = diff_application_maps(previous, current)
    assert diff["change"] == "FUNCTIONAL_CHANGE"
    assert diff["re_entry_state"] == "APPLICATION_DISCOVERED"
    assert diff["added_fingerprints"] == ["b"]


def test_same_fingerprints_are_unchanged() -> None:
    graph = {"nodes": [{"fingerprint": "a", "url_pattern": "/login", "area_id": "public"}]}
    diff = diff_application_maps(graph, graph)
    assert diff["change"] == "UNCHANGED"
    assert diff["re_entry_state"] == "COMPLETED"
