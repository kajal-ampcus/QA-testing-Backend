"""
State fingerprinting (architecture doc Section 9): (URL pattern, params
normalized) + (structural hash of the page's snapshot, ignoring dynamic
content like record IDs). Two pages with the same fingerprint collapse to
one Application Map state even if URLs differ (e.g. /employees/123 vs
/employees/456).

Deliberately simple — regex-based ID stripping, not a full route-template
engine or DOM-diff — good enough to catch the common "same page, different
record" case, which is the overwhelming majority of what real dedup needs to
catch. This is the fingerprint value ApplicationMapRepository.fingerprint_exists()
checks against its unique index.
"""

import hashlib
import re
from urllib.parse import urlparse

_NUMERIC_ID_SEGMENT = re.compile(r"/\d+(?=/|$)")
_UUID_SEGMENT = re.compile(r"/[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}(?=/|$)")
_ANY_DIGIT_RUN = re.compile(r"\d+")
_TIMESTAMP = re.compile(r"\b\d{1,4}[-/:]\d{1,2}[-/:]\d{1,4}(?:[ T]\d{1,2}:\d{2}(?::\d{2})?)?\b")
_TOKEN = re.compile(r"\b(?:token|csrf|nonce|session|captcha)\s*[:=]\s*[^\s\"]+", re.I)
_LOADING_LINE = re.compile(r"^.*\b(?:loading|please wait|starting service)\b.*$", re.I | re.M)
_CAPTCHA_LINE = re.compile(r"^.*\b(?:captcha|your answer)\b.*$", re.I | re.M)


def normalize_url_pattern(url: str) -> str:
    """/employees/123 -> /employees/{id}; /users/<uuid> -> /users/{id}."""
    path = urlparse(url).path or "/"
    path = _UUID_SEGMENT.sub("/{id}", path)
    path = _NUMERIC_ID_SEGMENT.sub("/{id}", path)
    return path


def structural_hash(snapshot_text: str) -> str:
    """Hashes the STRUCTURE of a chrome-devtools-mcp take_snapshot result
    (roles/names/tags), not its literal text — so a page whose only
    difference is a timestamp, a record ID, or a counter still hashes the
    same, while a genuinely different layout does not. Every digit run is
    normalized to a single placeholder before hashing."""
    normalized = _LOADING_LINE.sub("", snapshot_text)
    normalized = _CAPTCHA_LINE.sub("", normalized)
    normalized = _TOKEN.sub("<dynamic>", normalized)
    normalized = _TIMESTAMP.sub("<time>", normalized)
    # Math CAPTCHA prompts are intentionally excluded: a refresh must not
    # create another /login state.
    normalized = re.sub(r"\b\d+\s*[+\-*/x×]\s*\d+\b", "<captcha>", normalized)
    normalized = _ANY_DIGIT_RUN.sub("#", normalized)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:32]


def compute_fingerprint(url: str, snapshot_text: str) -> str:
    url_part = normalize_url_pattern(url)
    structure_part = structural_hash(snapshot_text)
    combined = f"{url_part}::{structure_part}"
    return hashlib.sha256(combined.encode("utf-8")).hexdigest()
