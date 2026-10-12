"""Keep independent account frontiers within one cumulative application map."""

from copy import deepcopy
from typing import Any


_FRONTIER = (
    "jobs", "completed_nodes", "input_requests", "module_inventory_flows",
    "reveal_probed", "collection_follow_queued",
)


def select_account_checkpoint(checkpoint: dict[str, Any], credential_ref: str | None) -> dict[str, Any]:
    result = deepcopy(checkpoint)
    configuration = result.setdefault("configuration", {})
    previous = configuration.get("credential_ref")
    if previous == credential_ref:
        return result
    contexts = result.setdefault("account_contexts", {})
    contexts[previous or "public"] = {
        key: deepcopy(result[key]) for key in _FRONTIER if key in result
    }
    selected = contexts.get(credential_ref or "public", {})
    for key in _FRONTIER:
        result.pop(key, None)
        if key in selected:
            result[key] = deepcopy(selected[key])
    configuration["credential_ref"] = credential_ref
    result["credential_ref"] = credential_ref
    result.pop("credential_update_required", None)
    return result
