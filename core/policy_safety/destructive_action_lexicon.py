"""
Destructive-action lexicon (architecture doc Section 29): matches an
element's accessible name against terms associated with destructive/
irreversible actions. Deliberately deterministic pattern matching, not an
LLM call — safety classification must be non-bypassable and can't be talked
out of its job by a manipulated prompt (this is the implementation-level
version of Section 5's reasoning for demoting Safety/Policy from "agent" to
infrastructure).

Used by core/agents/application_discovery/crawler.py to decide which
elements are safe to auto-click during exploration; will be reused by
core/agents/automation_generation/ once that milestone needs to flag scripts
touching destructive elements — one lexicon, not one per consumer.
"""

import re

from domain.enums import RiskLevel

_DESTRUCTIVE_TERMS = [
    "delete", "remove", "cancel subscription", "unsubscribe", "deactivate",
    "close account", "terminate", "destroy", "erase", "purge", "wipe", "discard",
    "trash", "revoke", "suspend", "ban", "block user", "void", "refund",
    "cancel order", "place order", "order now", "book now",
    "pay", "pay now", "submit payment", "checkout", "purchase", "buy now", "charge",
    "withdraw", "transfer", "transfer funds", "send money",
    "reject", "decline", "approve", "publish", "execute",
    "permanently", "irreversible",
]

_REVIEW_TERMS = [
    "edit", "update", "change", "modify", "archive", "disable", "reset", "clear",
    "save", "submit", "send", "confirm", "restore", "import", "upload",
]

# Controls a user acts on. With no accessible name (icon-only trash/close
# buttons) the action cannot be classified, so it is never auto-clicked.
_ACTIONABLE_ROLES = {"button", "menuitem", "menuitemcheckbox", "menuitemradio", "switch"}

_DESTRUCTIVE_PATTERN = re.compile(
    r"\b(" + "|".join(re.escape(term) for term in _DESTRUCTIVE_TERMS) + r")\b", re.IGNORECASE
)
_REVIEW_PATTERN = re.compile(
    r"\b(" + "|".join(re.escape(term) for term in _REVIEW_TERMS) + r")\b", re.IGNORECASE
)


def classify_risk(role: str, name: str) -> RiskLevel:
    if not name.strip() and role in _ACTIONABLE_ROLES:
        return RiskLevel.REVIEW
    if _DESTRUCTIVE_PATTERN.search(name):
        return RiskLevel.DESTRUCTIVE
    if _REVIEW_PATTERN.search(name):
        return RiskLevel.REVIEW
    return RiskLevel.SAFE
