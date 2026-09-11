"""
Selector priority: getByRole/getByLabel (accessibility-first) -> data-testid
-> stable CSS -> XPath (last resort, flagged for human review). Reads
application_map.elements (role/name from Discovery's take_snapshot output)
to pick the highest-priority stable selector for each test step.

Phase 0 stub.
"""
