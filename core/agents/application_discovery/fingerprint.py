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
from urllib.parse import parse_qsl, urlencode, urlparse

_NUMERIC_ID_SEGMENT = re.compile(r"/\d+(?=/|$)")
_UUID_SEGMENT = re.compile(r"/[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}(?=/|$)")
_ANY_DIGIT_RUN = re.compile(r"\d+")
_QUERY_ID_VALUE = re.compile(
    r"\d+|[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}|[0-9a-fA-F]{24}"
)
_TIMESTAMP = re.compile(r"\b\d{1,4}[-/:]\d{1,2}[-/:]\d{1,4}(?:[ T]\d{1,2}:\d{2}(?::\d{2})?)?\b")
_TOKEN = re.compile(r"\b(?:token|csrf|nonce|session|captcha)\s*[:=]\s*[^\s\"]+", re.I)
_LOADING_LINE = re.compile(r"^.*\b(?:loading|please wait|starting service)\b.*$", re.I | re.M)
_CAPTCHA_LINE = re.compile(r"^.*\b(?:captcha|your answer)\b.*$", re.I | re.M)


_CHROME_LABEL = re.compile(
    r"^(?:menu|menus|navigation|nav|sidebar|drawer|toolbar|header|footer|"
    r"logo|toggle|hamburger|open menu|close menu|main menu|more|options|"
    r"account menu|user menu)$",
    re.I,
)
_LOCALE_SEGMENT = re.compile(r"^[a-z]{2,3}(?:-[a-z]{2})?$", re.I)
_AUTH_FLOW_LABELS = {
    "login": "Login",
    "recovery": "Forgot password",
    "registration": "Create account",
    "authentication": "Sign in",
}


def is_chrome_label(name: str) -> bool:
    """True for shell chrome that is not a functional screen."""
    return bool(_CHROME_LABEL.fullmatch(" ".join(name.split())))


def clean_document_title(title: str) -> str:
    """'Menu | Kitchen POS' and 'Menu | Categories' collapse to 'Menu'."""
    text = " ".join(str(title or "").split())
    if not text:
        return ""
    for separator in (" | ", " • ", " – ", " — "):
        if separator in text:
            text = text.split(separator, 1)[0].strip()
            break
    return text


def canonical_route(url: str) -> str:
    """Path plus SPA hash route, with record ids stripped."""
    parsed = urlparse(url)
    path = normalize_url_pattern(url)
    fragment = parsed.fragment
    if fragment.startswith(("/", "!/")):
        hashed = _UUID_SEGMENT.sub("/{id}", fragment)
        hashed = _NUMERIC_ID_SEGMENT.sub("/{id}", hashed)
        path += "#" + hashed
    return path


def route_is_generic(route: str) -> bool:
    path = route.split("#", 1)[0] or "/"
    segments = [part for part in path.split("/") if part]
    if not segments:
        return True
    return len(segments) == 1 and bool(_LOCALE_SEGMENT.fullmatch(segments[0]))


def observed_page_label(nodes: list[dict], url: str = "", fallback: str = "Page") -> str:
    """Name the screen from its route first so catalog headings cannot become nodes."""
    from_route = section_page_label(url)
    if from_route:
        return from_route
    heading = next(
        (
            clean_document_title(str(node.get("name", "")))
            for node in nodes
            if node.get("role") in {"heading", "dialog", "alertdialog"}
            and str(node.get("name", "")).strip()
            and not is_chrome_label(str(node.get("name", "")))
        ),
        "",
    )
    if heading:
        return heading
    root = next((node for node in nodes if node.get("role") == "RootWebArea"), {})
    title = clean_document_title(str(root.get("name", "")))
    if title:
        return title
    return fallback


def functional_page_key(url: str, nodes: list[dict]) -> str:
    """Identity of a working screen, not a DOM snapshot or catalog item."""
    section = page_section_route(url)
    dialog = next(
        (
            clean_document_title(str(node.get("name", "")))
            for node in nodes
            if node.get("role") in {"dialog", "alertdialog"}
            and str(node.get("name", "")).strip()
            and not is_chrome_label(str(node.get("name", "")))
        ),
        "",
    )
    if section == "/":
        return f"{section}::{observed_page_label(nodes, url).casefold()}::{dialog.casefold()}"
    return f"{section}::{dialog.casefold()}"


def auth_flow_label(kind: str) -> str:
    return _AUTH_FLOW_LABELS.get(kind, "Sign in")


# Route prefixes that look like catalogs in any app (SKU/item URLs collapse
# to the parent screen). Not a site-specific list of product names.
_CATALOG_ROOTS = {
    "menu",
    "menus",
    "catalog",
    "catalogue",
    "products",
    "items",
    "shop",
    "store",
}
# Fallback for SPA buttons that have no href. Real page identity is the URL.
_APP_NAV_NAME = re.compile(
    r"^(?:dashboard|home|overview|menu|orders?|alerts?|notifications?|cart|"
    r"basket|profile|account|settings?|login|log in|sign in|forgot password|"
    r"reset password|browse menu|users?|reports?|details|user details|"
    r"inbox|messages?|billing|invoices?|customers?|projects?|tickets?|"
    r"calendar|files|documents?|help|support|admin|analytics|workspace)$",
    re.I,
)
_IN_PAGE_ACTION = re.compile(
    r"\b(?:"
    r"add to (?:cart|bag|basket)|added to (?:cart|bag|basket)|add again|"
    r"order now|buy now|view details|details for|"
    r"remove all|clear all|"
    r"search|filter|sort|load more|see all|show more|show less|"
    r"toggle (?:theme|dark|light|sidebar)|"
    r"apply(?: filters?)?|reset filters?"
    r")\b",
    re.I,
)
_IN_PAGE_FILTER = re.compile(
    r"^(?:all|active|open|closed|pending|completed|cancelled|canceled|"
    r"delivered|expired|read|unread|popular|featured|new|drafts?|"
    r"archived|published|failed|success)(?:\s+\d+)?$",
    re.I,
)
_PAGINATION_CONTROL = re.compile(r"^\d+$")
_COUNT_CHIP = re.compile(r"^[a-z][a-z\s]{0,24}\s+\d+$", re.I)
# Reversible "add to collection" verbs. No site names — if the control is
# absent, discovery never takes this path.
_GATED_REVEAL = re.compile(
    r"\badd(?:ed)? to (?:cart|bag|basket|wishlist|favourites?|favorites?)\b",
    re.I,
)
_GATED_COLLECTION = re.compile(
    r"\b(?:shopping[\s-]?cart|cart|bag|basket|wishlist|favourites?|favorites?)\b",
    re.I,
)
_GATED_COLLECTION_MUTATION = re.compile(
    r"\b(?:clear|empty|remove|delete|checkout|purchase|pay|order now|buy now|place order)\b",
    re.I,
)
_GATED_COLLECTION_SECTIONS = {
    "cart",
    "bag",
    "basket",
    "wishlist",
    "favorites",
    "favourites",
    "favourite",
}


def page_section_route(url: str) -> str:
    """Application page identity: /menu/boiled-egg stays Menu, /dashboard/users stays Users."""
    route = canonical_route(url)
    path, _, hashpart = route.partition("#")
    segments = [part for part in path.split("/") if part]
    if not segments and hashpart.startswith(("/", "!/")):
        segments = [part for part in hashpart.lstrip("!").split("/") if part]
    if not segments:
        return "/"
    if segments and segments[0].lower() in _CATALOG_ROOTS:
        return "/" + segments[0]
    return "/" + "/".join(segments)


def section_page_label(url: str) -> str:
    section = page_section_route(url).strip("/")
    if not section:
        return ""
    return section.split("/")[-1].replace("-", " ").replace("_", " ").title()


def is_app_nav_name(name: str) -> bool:
    return bool(_APP_NAV_NAME.fullmatch(" ".join(name.split())))


def is_gated_reveal_action(name: str) -> bool:
    """True for a reversible add-to-collection control on any catalog page."""
    return bool(_GATED_REVEAL.search(" ".join(str(name or "").split())))


def is_collection_mutation_action(name: str) -> bool:
    """True for checkout/clear/remove on a collection — never auto-clicked."""
    return bool(_GATED_COLLECTION_MUTATION.search(" ".join(str(name or "").split())))


def is_gated_collection_section(url: str) -> bool:
    section = page_section_route(url or "").strip("/").split("/")[0].lower()
    return bool(section) and section in _GATED_COLLECTION_SECTIONS


def is_gated_collection_entry(
    name: str,
    description: str | None = None,
    url: str | None = None,
) -> bool:
    """True for cart/bag/wishlist entry (named, described, or href) — not checkout."""
    parsed = urlparse(url or "")
    haystack = " ".join(
        part
        for part in (
            str(name or ""),
            str(description or ""),
            parsed.path.replace("/", " "),
            parsed.fragment.replace("/", " "),
        )
        if part
    ).strip()
    if not haystack or is_gated_reveal_action(haystack):
        return False
    if _GATED_COLLECTION_MUTATION.search(haystack):
        return False
    return bool(_GATED_COLLECTION.search(haystack))


def gated_probe_kind(
    name: str,
    description: str | None,
    destination: str | None,
    current_url: str,
) -> str | None:
    """'reveal' | 'collection' | None.

    A real href to another section (header Cart → /cart) is normal navigation.
    Collection follow-up is only for icon controls that have no new URL yet.
    """
    if is_gated_reveal_action(name):
        return "reveal"
    if is_gated_collection_section(current_url):
        return None
    if destination and page_section_route(destination) != page_section_route(current_url):
        return None
    if is_gated_collection_entry(name, description, destination):
        return "collection"
    return None


def repeated_in_page_control(name: str, nodes: list[dict]) -> bool:
    """Same unlabeled control many times is a list widget, not a unique screen."""
    label = " ".join(str(name or "").split()).casefold()
    if not label:
        return False
    matches = 0
    for node in nodes:
        if node.get("role") not in {"link", "button", "menuitem", "tab"}:
            continue
        if " ".join(str(node.get("name") or "").split()).casefold() == label:
            matches += 1
            if matches >= 2:
                return True
    return False


def is_in_page_content_control(role: str, name: str, destination: str | None, current_url: str) -> bool:
    """True for catalog cards, filters, and widgets — not application pages.

    Page identity is the URL section. Control *names* are never bound to a
    particular product; any application's invoices/users/settings links are pages.
    """
    label = " ".join(str(name or "").split())
    current = page_section_route(current_url)
    if not label:
        if not destination:
            return True
        return page_section_route(destination) == current
    if _PAGINATION_CONTROL.fullmatch(label) or _COUNT_CHIP.fullmatch(label):
        return True
    if _IN_PAGE_ACTION.search(label) or _IN_PAGE_FILTER.fullmatch(label):
        return True
    if role in {"radio", "checkbox", "combobox"}:
        return True
    if destination:
        target = page_section_route(destination)
        if target == current:
            return not is_app_nav_name(label)
        return False
    if is_app_nav_name(label):
        return False
    root = current.strip("/").split("/")[0].lower() if current != "/" else ""
    return root in _CATALOG_ROOTS


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
    parsed = urlparse(url)
    # Preserve SPA hash routes and query-driven screens, excluding transport secrets.
    if parsed.fragment.startswith(("/", "!/")):
        url_part += "#" + parsed.fragment
    # Record ids in the query (?id=123, ?user=<uuid>) are dynamic like path ids;
    # keeping them would give every record its own state.
    parameters = [
        (key, "{id}" if _QUERY_ID_VALUE.fullmatch(value) else value)
        for key, value in parse_qsl(parsed.query)
        if not re.search(r"token|session|nonce|csrf|password|secret|utm_|timestamp", key, re.I)
    ]
    if parameters:
        url_part += "?" + urlencode(sorted(parameters))
    structure_part = structural_hash(snapshot_text)
    combined = f"{url_part}::{structure_part}"
    return hashlib.sha256(combined.encode("utf-8")).hexdigest()
