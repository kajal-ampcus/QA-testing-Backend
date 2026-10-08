"""A new discovery version classifies screens by structure, not by image similarity."""

from core.agents.application_discovery.evidence import collect_evidence_names
from core.agents.application_discovery.screen_delta import classify_screen


PREVIOUS = [
    {"fingerprint": "same", "page_key": "/home", "url_pattern": "/home", "evidence_ref": "/api/v1/application-maps/evidence/a.png"},
    {"fingerprint": "old-orders", "page_key": "/orders", "url_pattern": "/orders"},
]


def test_screen_delta_marks_new_changed_and_unchanged():
    assert classify_screen("same", "/home", PREVIOUS) == "unchanged"
    assert classify_screen("new-orders", "/orders", PREVIOUS) == "changed"
    assert classify_screen("fresh", "/reports", PREVIOUS) == "new"


def test_referenced_screenshots_are_identified_for_retention():
    names = collect_evidence_names(
        {
            "screenshot_ref": "/api/v1/application-maps/evidence/keep.png",
            "failed_actions": [{"screenshot_ref": "drop-me.txt"}],
            "evidence": {"screenshot": "/api/v1/application-maps/evidence/run.png"},
        }
    )
    assert names == {"keep.png", "run.png"}
