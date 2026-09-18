"""
Per-transition rules from Section 8's table: trigger, artifact passed, where
it's stored, what happens on validation failure vs. agent failure, retry
counts, and hard-stop conditions. Kept separate from state_machine.py so the
state graph itself (linear, easy to reason about) isn't cluttered with the
bookkeeping of each edge.

Phase 0 stub.
"""
