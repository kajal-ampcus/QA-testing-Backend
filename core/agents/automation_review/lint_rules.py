"""
Deterministic lint checks (architecture doc Section 6/13): no hard-coded
sleep(), no hard-coded credentials (must reference secrets.get(...) not a
literal), no bare XPath where a role/label selector exists in the
application_map. Returns counts per script, not a single pass/fail boolean —
see companion doc Part 2 #6 for why counts matter (one XPath fallback vs. an
all-XPath script are very different risk profiles).

This is exactly the kind of check also enforced at the CI level via
import-linter (pyproject.toml) — lint_rules.py is the agent-facing version
of the same "make the rule structurally hard to violate" philosophy.

Phase 0 stub.
"""

# TODO (Phase 1): def lint(script_source: str) -> LintResult: ...
