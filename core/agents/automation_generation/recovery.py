"""Case-local recovery orchestration; no writes to approved test definitions."""

from copy import deepcopy
from dataclasses import replace

from core.agents.automation_generation.suite import _forbidden_literals, _plan_case


async def recover_cases(cases, states, generation_id, rediscover=None):
    recovered = dict(states)
    diagnostics = {}
    refreshed = {}
    attempted = set()
    for case in cases:
        attempts = ["Exact mapping and unique observed-name matching"]
        draft = _plan_case(deepcopy(case), recovered, _forbidden_literals([case]), generation_id)
        mapping_failure = draft.blocked and any(word in (draft.blocked_reason or "") for word in (
            "observed", "match", "element", "control", "mapped assertion",
        ))
        if mapping_failure and rediscover:
            codes = list(dict.fromkeys(
                step.get("target", {}).get("state_code") for step in case.steps
            ))
            for code in codes:
                if code not in recovered or code in attempted or len(attempted) >= 4:
                    continue
                if code not in (draft.blocked_reason or ""):
                    continue
                attempted.add(code)
                attempts.append(f"Targeted rediscovery: {code} (one attempt)")
                try:
                    elements = await rediscover(recovered[code], case)
                    recovered[code] = replace(recovered[code], elements=elements)
                    refreshed[code] = elements
                except Exception as exc:
                    attempts.append(f"Rediscovery unavailable: {type(exc).__name__}")
            draft = _plan_case(deepcopy(case), recovered, _forbidden_literals([case]), generation_id)
        remapped = any(step.get("mapping", {}).get("mapping_status") == "REMAPPED" for step in draft.case.steps)
        diagnostics[str(case.test_case_id)] = {
            "status": "BLOCKED" if draft.blocked else "RECOVERED" if remapped or len(attempts) > 1 else "MAPPED",
            "attempts": attempts,
            "reason": draft.blocked_reason,
            "suggested_fix": "Inspect the named step and page; configure missing test data or discover its exact UI state." if draft.blocked else "",
        }
    # A shared state's fresh evidence can invalidate an earlier case. Report
    # the same final map that the writer will consume, without another retry.
    for case in cases:
        draft = _plan_case(deepcopy(case), recovered, _forbidden_literals([case]), generation_id)
        diagnostic = diagnostics[str(case.test_case_id)]
        if draft.blocked:
            diagnostic.update(status="BLOCKED", reason=draft.blocked_reason,
                              suggested_fix="Inspect the named step and page; configure missing test data or discover its exact UI state.")
    return recovered, diagnostics, refreshed
