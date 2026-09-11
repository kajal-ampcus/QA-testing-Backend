"""
State fingerprinting (Section 9): (URL pattern, params normalized) +
(structural DOM hash, ignoring dynamic content) + (visible heading/title).
Two pages with the same fingerprint collapse to one Application Map state
even if URLs differ (e.g. /user/123 vs /user/456).

This is also the merge key when multiple parallel Discovery shards
(crawler.py) reach the same state from different navigation paths.

Phase 0 stub.
"""

# TODO (Phase 1): def compute_fingerprint(dom_snapshot, url) -> str: ...
