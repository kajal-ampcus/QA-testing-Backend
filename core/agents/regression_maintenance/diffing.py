"""Deterministic map version diff. Re-entry uses OrchestratorState values."""

from typing import Any


def diff_application_maps(
    previous: dict[str, Any], current: dict[str, Any]
) -> dict[str, Any]:
    """Compare fingerprint sets. Added/removed states are a FUNCTIONAL_CHANGE
    (re-enter at APPLICATION_DISCOVERED). Shared fingerprints with different
    element sets are UI_DRIFT (re-enter at AUTOMATION_GENERATED)."""
    previous_nodes = {
        node["fingerprint"]: node for node in previous.get("nodes", []) if node.get("fingerprint")
    }
    current_nodes = {
        node["fingerprint"]: node for node in current.get("nodes", []) if node.get("fingerprint")
    }
    added = sorted(set(current_nodes) - set(previous_nodes))
    removed = sorted(set(previous_nodes) - set(current_nodes))
    shared = set(previous_nodes) & set(current_nodes)
    drifted = sorted(
        fingerprint
        for fingerprint in shared
        if previous_nodes[fingerprint].get("url_pattern") != current_nodes[fingerprint].get("url_pattern")
        or previous_nodes[fingerprint].get("area_id") != current_nodes[fingerprint].get("area_id")
    )
    if added or removed:
        change = "FUNCTIONAL_CHANGE"
        re_entry_state = "APPLICATION_DISCOVERED"
    elif drifted:
        change = "UI_DRIFT"
        re_entry_state = "AUTOMATION_GENERATED"
    else:
        change = "UNCHANGED"
        re_entry_state = "COMPLETED"
    return {
        "change": change,
        "re_entry_state": re_entry_state,
        "added_fingerprints": added,
        "removed_fingerprints": removed,
        "drifted_fingerprints": drifted,
    }
