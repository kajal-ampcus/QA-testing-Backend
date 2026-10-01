"""
Wraps the official chrome-devtools-mcp server (Puppeteer/CDP-based,
Chromium-only) via the MCP Python SDK's stdio transport.

Never used for scripted Test Execution — that's
core/tool_gateway/playwright_client.py, always.
"""

import asyncio
import base64
import json
import logging
import os
import re
import socket
import tempfile
import time
import uuid
from contextlib import AsyncExitStack, suppress
from html import unescape
from pathlib import Path
from typing import Any, TextIO
from urllib.parse import unquote, urlparse

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from core.tool_gateway.secret_resolver import resolve_login
from core.tool_gateway.snapshot import merge_dom_hrefs, parse_elements

logger = logging.getLogger(__name__)

_MATH_EXPRESSION = re.compile(r"(\d{1,2})\s*([+\-*/x×÷−–＋])\s*(\d{1,2})")
_CAPTCHA_FIELD_PATTERN = re.compile(
    r"\b(?:your answer|captcha answer|enter the answer|captcha|math question)\b",
    re.I,
)
_CAPTCHA_CHALLENGE_PATTERN = re.compile(
    r"\b(?:captcha|enter\s+captcha|type\s+the\s+captcha|solve\s+the\s+captcha|"
    r"refresh\s+captcha|speak\s+captcha|verify\s+you\s+are\s+human|security\s+verification)\b",
    re.I,
)
_INCORRECT_CAPTCHA_PATTERN = re.compile(
    r"(?:incorrect|invalid|wrong|failed|empty).{0,40}captcha|"
    r"captcha.{0,40}(?:incorrect|invalid|wrong|failed|empty|required)|"
    r"please\s+(?:enter|solve|refresh).{0,20}captcha",
    re.I,
)


class ApplicationReadinessTimeoutError(RuntimeError):
    """The target application never left a loading or hosting wake-up page."""


class SecurityVerificationRequiredError(RuntimeError):
    """The target stopped at an interactive anti-bot verification page."""


_HOSTING_COLD_START_PATTERN = re.compile(
    r"\b(?:"
    r"service\s+waking\s+up|"
    r"allocating\s+compute\s+resources|"
    r"incoming\s+http\s+request\s+detected|"
    r"service\s+(?:is\s+)?starting|"
    r"spinning\s+up|"
    r"starting\s+(?:the\s+)?service|"
    r"waking\s+(?:the\s+)?service|"
    r"application\s+(?:is\s+)?loading|"
    r"application\s+failed\s+to\s+respond|"
    r"service\s+unavailable|"
    r"bad\s+gateway"
    r")\b",
    re.I,
)

_SECURITY_VERIFICATION_PATTERN = re.compile(
    r"(?:"
    r"performing\s+security\s+verification|"
    r"verify\s+(?:that\s+)?you\s+are\s+(?:a\s+)?human|"
    r"security\s+service\s+to\s+protect\s+against\s+malicious\s+bots|"
    r"checking\s+(?:if\s+)?(?:the\s+)?site\s+connection\s+is\s+secure|"
    r"cloudflare[^\n]{0,160}(?:challenge|verification|verify\s+you\s+are\s+human)"
    r")",
    re.I,
)


def _is_hosting_cold_start_page(text: str) -> bool:
    return bool(_HOSTING_COLD_START_PATTERN.search(text))


def _is_security_verification_page(text: str) -> bool:
    """Recognize an anti-bot interstitial before it is mapped as application UI."""
    return bool(_SECURITY_VERIFICATION_PATTERN.search(text))


def _positive_float_env(name: str, default: float) -> float:
    try:
        value = float(os.environ.get(name, str(default)))
    except ValueError:
        return default
    return value if value > 0 else default


def _same_site_allowed_patterns(target_url: str) -> list[str]:
    """Allow the application origin and HTTPS sibling hosts used by its APIs."""
    parsed = urlparse(target_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return [target_url]
    patterns = [f"{parsed.scheme}://{parsed.netloc}/*"]
    hostname = parsed.hostname or ""
    labels = hostname.split(".")
    if (
        parsed.scheme == "https"
        and len(labels) >= 3
        and not hostname.replace(".", "").isdigit()
    ):
        # SPAs commonly use sibling hosts such as app.example.com and
        # app-api.example.com. Restrict the expansion to HTTPS on the same
        # site instead of opening unrestricted networking.
        site_suffix = ".".join(labels[-2:])
        patterns.append(f"https://*.{site_suffix}/*")
    return patterns


def _snapshot_text(snapshot: object) -> str:
    if isinstance(snapshot, list):
        return "\n".join(getattr(block, "text", "") for block in snapshot)
    return str(snapshot)


def _evaluate_script_text(snapshot: object) -> str:
    """Unwrap chrome-devtools-mcp evaluate_script payloads (plain text or fenced)."""
    text = _snapshot_text(snapshot)
    match = re.search(r"```(?:json|text|js|javascript)?\s*([\s\S]*?)```", text)
    if not match:
        return text
    payload = match.group(1).strip()
    with suppress(json.JSONDecodeError):
        decoded = json.loads(payload)
        if isinstance(decoded, str):
            return decoded
    return payload


def _parse_controls(text: str) -> list[tuple[str, str, str]]:
    """Parse all uid/role/name triples from a snapshot text."""
    return [(e["uid"], e["role"], e["name"]) for e in parse_elements(text)]


def _login_form_visible(snapshot: object) -> bool:
    """True only when a password field is still on screen."""
    return any(
        role in {"textbox", "input"} and re.search(r"\bpassword\b", name, re.I)
        for _uid, role, name in _parse_controls(_snapshot_text(snapshot))
    )


def _authenticated_session_visible(snapshot: object) -> bool:
    """Detect that login left the form, including SPAs that keep the word Password in copy."""
    controls = _parse_controls(_snapshot_text(snapshot))
    if any(
        role == "button" and re.search(r"\b(?:log\s*out|sign\s*out)\b", name, re.I)
        for _uid, role, name in controls
    ):
        return True
    if any(
        role in {"heading", "StaticText"}
        and re.search(r"\b(?:welcome back|good (?:morning|afternoon|evening))\b", name, re.I)
        for _uid, role, name in controls
    ):
        return True
    return bool(controls) and not _login_form_visible(snapshot)


def _term_in_label(term: str, label: str) -> bool:
    """Match a login term without letting short words hit unrelated labels.

    "go" must not match Forgot Password. Longer terms still match a label
    that contains them, such as "password" inside "Enter your password".
    """
    if len(term) <= 3:
        return re.search(rf"(?<!\w){re.escape(term)}(?!\w)", label) is not None
    return term in label


_LOGIN_ROLE_BUTTONS = ("employee", "kitchen", "admin", "staff", "manager")
_SUBMIT_EXCLUDE = re.compile(
    r"\b(?:forgot|reset|recover|refresh|show password|hide password|remember)\b",
    re.I,
)


def _find_submit_uid(
    controls: list[tuple[str, str, str]], names: list[str]
) -> str | None:
    """Prefer a real login button. Never use Forgot Password or Refresh CAPTCHA."""
    wanted = [n.lower() for n in names if n]
    candidates = [
        (uid, role, name)
        for uid, role, name in controls
        if role in {"button", "link"} and not _SUBMIT_EXCLUDE.search(name)
    ]
    buttons = [(uid, role, name) for uid, role, name in candidates if role == "button"]
    for pool in (buttons, candidates):
        for uid, _role, name in pool:
            if name.lower() in wanted:
                return uid
        for uid, _role, name in pool:
            lowered = name.lower()
            if any(_term_in_label(term, lowered) for term in wanted if len(term) > 3):
                return uid
    return None


def _authentication_completed(text: str, login_text: str = "") -> bool:
    controls = _parse_controls(text)
    if any(role in {"button", "link", "menuitem"} and
           re.fullmatch(r"\s*(?:log\s*out|sign\s*out)\s*", name, re.I)
           for _, role, name in controls):
        return True
    if any(role in {"textbox", "input"} and re.search(r"\b(?:password|passphrase|pin)\b", name, re.I)
           for _, role, name in controls):
        return False
    old_url = re.search(r'RootWebArea[^\n]*url="([^"]+)"', login_text)
    new_url = re.search(r'RootWebArea[^\n]*url="([^"]+)"', text)
    return bool(old_url and new_url and old_url[1] != new_url[1]
                and any(role in {"heading", "button", "link"} for _, role, _ in controls)
                and not re.search(r"verification|one.time|captcha|loading", text, re.I))


def _find_uid(
    controls: list[tuple[str, str, str]],
    names: list[str],
    roles: set[str],
) -> str | None:
    """
    Return the uid of the first element whose role and name match.

    Works for ANY website — no hardcoded field names.
    Three-pass matching so this degrades gracefully on any login form:

    Pass 1 — exact match:     field name == wanted term exactly
                               e.g. stored selector "Username" matches "Username"
    Pass 2 — contains match:  field name contains a wanted term
                               e.g. "Email address" contains "email"
    Pass 3 — reverse contains: a wanted term contains the field name
                               e.g. field name "user" is contained in "username"

    This means the crawler never needs site-specific fallback lists for standard
    login forms. Custom selectors in the stored credential still take priority
    because they are the first item in the names list.
    """
    wanted = {n.lower() for n in names if n}
    if not wanted:
        return None

    # Pass 1: exact match
    exact = next(
        (uid for uid, role, name in controls if role in roles and name.lower() in wanted),
        None,
    )
    if exact:
        return exact

    # Pass 2: field name contains a wanted term
    # e.g. "Email address" contains "email", "Sign in with email" contains "email"
    contains = next(
        (
            uid
            for uid, role, name in controls
            if role in roles and any(_term_in_label(w, name.lower()) for w in wanted)
        ),
        None,
    )
    if contains:
        return contains

    # Pass 3: a wanted term contains the field name
    # e.g. field is "user", wanted has "username" — "username" contains "user"
    reverse = next(
        (
            uid
            for uid, role, name in controls
            if role in roles and any(name.lower() in w for w in wanted if len(name) > 2)
        ),
        None,
    )
    return reverse


_SVG_DATA_URI = re.compile(r"data:image/svg\+xml[^\"'\s>]*", re.I)
_TSPAN = re.compile(
    r"<tspan\b([^>]*)>(.*?)</tspan>",
    re.I | re.S,
)
_TSPAN_X = re.compile(r"""\bx\s*=\s*["']?(-?\d+(?:\.\d+)?)""", re.I)


def _expression_from_svg_markup(svg: str) -> str:
    """Read Cafinity-style math CAPTCHAs: one glyph per tspan, ordered by x.

    Empty decoy tspans are ignored. Attribute numbers such as x/y/width must
    not be treated as operands.
    """
    glyphs: list[tuple[float, str]] = []
    for attrs, inner in _TSPAN.findall(svg):
        text = re.sub(r"<[^>]+>", "", inner)
        text = unescape(text).strip()
        if not text:
            continue
        x_match = _TSPAN_X.search(attrs)
        x = float(x_match.group(1)) if x_match else float(len(glyphs))
        glyphs.append((x, text))
    glyphs.sort(key=lambda item: item[0])
    return " ".join(text for _x, text in glyphs)


def _svg_captcha_text(snapshot: str) -> str:
    """Read arithmetic text embedded in a CAPTCHA image data URI."""
    pieces: list[str] = []
    for raw in _SVG_DATA_URI.findall(snapshot):
        comma = raw.find(",")
        if comma < 0:
            continue
        svg = unescape(unquote(raw[comma + 1 :]))
        text = _expression_from_svg_markup(svg)
        if text:
            pieces.append(text)
    return "\n".join(pieces)


def _captcha_expression_score(text: str, match: re.Match[str]) -> int:
    """Prefer the actual CAPTCHA over unrelated numbers such as 24/7 or dates."""
    start, end = match.span()
    window = text[max(0, start - 80) : min(len(text), end + 80)].lower()
    score = 0
    if "=" in text[end : end + 12] or "what is" in window:
        score += 8
    if "captcha" in window or "your answer" in window or "enter the answer" in window:
        score += 10
    return score


def _solve_math_captcha(text: str) -> int | None:
    """
    Find and solve a simple arithmetic CAPTCHA in snapshot/DOM text.
    Handles: 'What is 3 + 3?', 'What is 6 - 2?', '3 + 16 = ?' etc.
    Ignores year ranges and other two-digit noise when a better match exists.
    """
    # SVG CAPTCHAs often split digits across <tspan> nodes: "1 9 - 7" -> "19 - 7".
    text = re.sub(r"\d(?:\s+\d)+", lambda match: re.sub(r"\s+", "", match.group(0)), text)
    matches = list(_MATH_EXPRESSION.finditer(text))
    if not matches:
        return None
    matches.sort(key=lambda match: (_captcha_expression_score(text, match), match.start()), reverse=True)
    for match in matches:
        a, op, b = int(match.group(1)), match.group(2), int(match.group(3))
        answer = {
            "+": a + b,
            "＋": a + b,
            "-": a - b,
            "*": a * b,
            "x": a * b,
            "×": a * b,
            "/": (a // b if b and a % b == 0 else None),
            "÷": (a // b if b and a % b == 0 else None),
            "−": a - b,
            "–": a - b,
        }.get(op)
        if answer is not None:
            return answer
    return None


def _captcha_challenge_kind(text: str) -> str:
    """Return the recognized CAPTCHA class in a generic, site-agnostic way."""
    if _solve_math_captcha(text) is not None:
        return "math"
    if _CAPTCHA_CHALLENGE_PATTERN.search(text):
        return "generic"
    return "none"


def _stdio_environment() -> dict[str, str]:
    """Pass a usable process environment into the MCP stdio subprocess.

    The MCP Python SDK only inherits a short allowlist (PATH, HOME, …). Chrome
    lookup on Windows needs PROGRAMFILES; Node/npx need PATHEXT/ComSpec; Docker
    Chrome needs PLAYWRIGHT_BROWSERS_PATH. Without these the server exits and
    the client surfaces MCPError: Connection closed.
    """
    names = {
        "PATH", "HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "TEMP", "TMP",
        "SYSTEMROOT", "WINDIR", "PROGRAMFILES", "PROGRAMFILES(X86)",
        "COMSPEC", "PATHEXT", "PLAYWRIGHT_BROWSERS_PATH", "NODE_PATH",
    }
    environment = {
        key: value
        for key, value in os.environ.items()
        if key.upper() in names and value and not value.startswith("()")
    }
    # The Node binary copied from node:22-bookworm-slim can segfault in the
    # final Python image after Chrome's system dependencies are installed.
    # Playwright ships a Node runtime alongside its driver; prefer it for the
    # MCP CLI's `#!/usr/bin/env node` launcher when it is available.
    try:
        import playwright

        driver_dir = Path(playwright.__file__).resolve().parent / "driver"
        bundled_node = driver_dir / "node"
        if bundled_node.is_file():
            existing_path = environment.get("PATH", os.defpath)
            environment["PATH"] = os.pathsep.join((str(driver_dir), existing_path))
    except ImportError:
        pass
    return environment


class ChromeDevToolsClient:
    # Serialize Chrome launches so parallel discovery workers do not all crash
    # the same constrained Docker/Windows host at once.
    _startup_gate = asyncio.Lock()

    def __init__(
        self,
        allowed_url_pattern: str | None = None,
        headless: bool = True,
        credential_ref: str | None = None,
        project_id: uuid.UUID | None = None,
        page_ready_timeout_seconds: float | None = None,
        cold_start_reload_interval_seconds: float | None = None,
        security_verification_timeout_seconds: float | None = None,
        ready_poll_interval_seconds: float = 0.5,
    ) -> None:
        self._allowed_url_pattern = allowed_url_pattern
        self._headless = headless
        self._credential_ref = credential_ref
        self._project_id = project_id
        self._authenticated = False
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            self._cdp_port = listener.getsockname()[1]
        self._page_ready_timeout_seconds = (
            page_ready_timeout_seconds
            if page_ready_timeout_seconds is not None
            else _positive_float_env("DISCOVERY_PAGE_READY_TIMEOUT_SECONDS", 180.0)
        )
        self._cold_start_reload_interval_seconds = (
            cold_start_reload_interval_seconds
            if cold_start_reload_interval_seconds is not None
            else _positive_float_env("DISCOVERY_COLD_START_RELOAD_INTERVAL_SECONDS", 15.0)
        )
        self._security_verification_timeout_seconds = (
            security_verification_timeout_seconds
            if security_verification_timeout_seconds is not None
            else _positive_float_env("DISCOVERY_SECURITY_VERIFICATION_TIMEOUT_SECONDS", 30.0)
        )
        self._ready_poll_interval_seconds = max(0.01, ready_poll_interval_seconds)
        self._session: ClientSession | None = None
        self._exit_stack: AsyncExitStack | None = None
        self._page_id: int | None = None
        self._omit_url_allowlist = False
        self._errlog_file: TextIO | None = None
        self._mcp_log_path: Path | None = None

    # def _server_params(self) -> StdioServerParameters:
    #     command = os.environ.get("CHROME_DEVTOOLS_MCP_COMMAND")
    #     args = [] if command else ["-y", "chrome-devtools-mcp@latest"]
    #     if executable_path := os.environ.get("CHROME_EXECUTABLE_PATH"):
    #         args.extend(["--executablePath", executable_path])
    #     if os.environ.get("CHROME_NO_SANDBOX", "false").lower() == "true":
    #         args.append("--chromeArg=--no-sandbox")
    #     if os.environ.get("CHROME_DEVTOOLS_MCP_ISOLATED", "true").lower() == "true":
    #         args.append("--isolated")
    #     if self._headless:
    #         args.append("--headless=true")
    #     if self._allowed_url_pattern:
    #         parsed = urlparse(self._allowed_url_pattern)
    #         allowed_pattern = (
    #             f"{parsed.scheme}://{parsed.netloc}/*"
    #             if parsed.scheme in {"http", "https"} and parsed.netloc
    #             else self._allowed_url_pattern
    #         )
    #         args.extend(["--allowedUrlPattern", allowed_pattern])
    #     if os.environ.get("CHROME_DEVTOOLS_MCP_REDACT_NETWORK_HEADERS", "true").lower() == "true":
    #         args.append("--redactNetworkHeaders")
    #     return StdioServerParameters(
    #         command=command or ("npx.cmd" if os.name == "nt" else "npx"), args=args
    #     )

    @staticmethod
    def _npx_command(extra_args: list[str], *, windows: bool | None = None) -> tuple[str, list[str]]:
        """Launch chrome-devtools-mcp through npx without an interactive prompt."""
        use_windows = os.name == "nt" if windows is None else windows
        package_args = ["-y", "chrome-devtools-mcp@latest", *extra_args]
        if use_windows:
            return "cmd", ["/c", "npx", *package_args]
        return "npx", package_args

    def _server_params(self) -> StdioServerParameters:
        """
        Build parameters for an installed MCP binary or the npx fallback.
        """
        import shutil

        command = os.environ.get("CHROME_DEVTOOLS_MCP_COMMAND")
        args: list[str] = []
        if not command:
            if shutil.which("chrome-devtools-mcp"):
                # Global binary is on PATH — use it directly, no npm overhead
                command = "chrome-devtools-mcp"
            else:
                command, args = self._npx_command([])
        args.append(f"--chromeArg=--remote-debugging-port={self._cdp_port}")
        if executable_path := os.environ.get("CHROME_EXECUTABLE_PATH"):
            args.extend(["--executablePath", executable_path])

        containerish = os.environ.get("CHROME_NO_SANDBOX", "false").lower() == "true"
        if containerish:
            args.append("--chromeArg=--no-sandbox")
            args.append("--chromeArg=--disable-setuid-sandbox")
            args.append("--chromeArg=--disable-dev-shm-usage")
            args.append("--chromeArg=--disable-gpu")

        if os.environ.get("CHROME_IGNORE_CERTIFICATE_ERRORS", "false").lower() == "true":
            args.append("--chromeArg=--ignore-certificate-errors")

        if os.environ.get("CHROME_DEVTOOLS_MCP_ISOLATED", "true").lower() == "true":
            args.append("--isolated")

        if self._headless:
            args.append("--headless")

        if self._allowed_url_pattern and not self._omit_url_allowlist:
            for pattern in (
                *_same_site_allowed_patterns(self._allowed_url_pattern),
                "about:*",
                "chrome://*",
            ):
                args.extend(["--allowedUrlPattern", pattern])
        if os.environ.get("CHROME_DEVTOOLS_MCP_REDACT_NETWORK_HEADERS", "true").lower() == "true":
            args.append("--redactNetworkHeaders")
        return StdioServerParameters(
            command=command,
            args=args,
            env=_stdio_environment(),
        )

    def _capture_logs(self) -> str:
        chunks: list[str] = []
        if self._errlog_file is not None:
            with suppress(OSError):
                self._errlog_file.flush()
                self._errlog_file.seek(0)
                chunks.append(self._errlog_file.read()[-4000:])
        if self._mcp_log_path is not None and self._mcp_log_path.exists():
            with suppress(OSError):
                chunks.append(self._mcp_log_path.read_text(encoding="utf-8", errors="replace")[-4000:])
        return "\n".join(chunk for chunk in chunks if chunk).strip()

    def _startup_error(self, exc: Exception) -> RuntimeError:
        logs = self._capture_logs()
        detail = f"{type(exc).__name__}: {exc}"
        if logs:
            detail = f"{detail}\nMCP server log:\n{logs}"
        return RuntimeError(
            "Chrome DevTools MCP failed to start (connection closed or process exited). "
            f"{detail}"
        )

    async def _close_session(self) -> None:
        if self._exit_stack is not None:
            with suppress(Exception):
                await self._exit_stack.aclose()
        self._session = None
        self._exit_stack = None
        self._page_id = None
        if self._errlog_file is not None:
            with suppress(OSError):
                self._errlog_file.close()
            self._errlog_file = None

    async def _open_session(self) -> None:
        log_dir = Path(os.environ.get("CHROME_DEVTOOLS_MCP_LOG_DIR", tempfile.gettempdir()))
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / f"chrome-devtools-mcp-{os.getpid()}-{uuid.uuid4().hex[:8]}.stderr.log"
        self._mcp_log_path = log_path
        # Lives for the whole MCP session; closed in _close_session.
        self._errlog_file = open(log_path, "w+", encoding="utf-8")  # noqa: SIM115
        self._exit_stack = AsyncExitStack()
        read, write = await self._exit_stack.enter_async_context(
            stdio_client(self._server_params(), errlog=self._errlog_file)
        )
        self._session = await self._exit_stack.enter_async_context(ClientSession(read, write))
        await self._session.initialize()
        pages = await self.list_pages()
        page_text = _snapshot_text(pages)
        selected = re.search(r"(?m)^(\d+): .*\[selected\]", page_text)
        first = re.search(r"(?m)^(\d+): ", page_text)
        match = selected or first
        if match is None:
            await self.new_page("about:blank")
            pages = await self.list_pages()
            page_text = _snapshot_text(pages)
            match = re.search(r"(?m)^(\d+): .*\[selected\]", page_text) or re.search(
                r"(?m)^(\d+): ", page_text
            )
        if match is None:
            raise RuntimeError("Chrome DevTools MCP returned no usable page ID")
        self._page_id = int(match.group(1))

    async def __aenter__(self) -> "ChromeDevToolsClient":
        async with ChromeDevToolsClient._startup_gate:
            last_error: Exception | None = None
            allow_unrestricted_retry = (
                os.environ.get("CHROME_DEVTOOLS_MCP_ALLOW_UNRESTRICTED_RETRY", "false").lower()
                == "true"
            )
            for attempt in range(3):
                # Chrome 149+ URL allowlists can detach the initial tab and take
                # the stdio server down. Dropping the allowlist lets the browser
                # reach any host, so it is an explicit opt-in, never a silent default.
                self._omit_url_allowlist = allow_unrestricted_retry and attempt >= 1
                try:
                    await self._open_session()
                    return self
                except Exception as exc:
                    last_error = self._startup_error(exc)
                    await self._close_session()
                    if attempt < 2:
                        await asyncio.sleep(1.0 * (attempt + 1))
            assert last_error is not None
            raise last_error

    async def __aexit__(self, *exc_info: object) -> None:
        await self._close_session()

    def _page_args(self, **kwargs: Any) -> dict[str, Any]:
        if self._page_id is None:
            raise RuntimeError("Chrome DevTools MCP has no selected page")
        return {"pageId": self._page_id, **kwargs}

    def _require_session(self) -> ClientSession:
        if self._session is None:
            raise RuntimeError("ChromeDevToolsClient used outside its 'async with' block.")
        return self._session

    async def _call(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        session = self._require_session()
        try:
            result = await session.call_tool(tool_name, arguments)
        except Exception as exc:
            if "connection closed" in str(exc).lower():
                raise self._startup_error(exc) from exc
            raise
        if result.is_error:
            raise RuntimeError(f"chrome-devtools-mcp tool '{tool_name}' failed: {result.content}")
        return result.content

    async def list_tool_schemas(self) -> dict[str, dict[str, Any]]:
        session = self._require_session()
        tools = await session.list_tools()
        return {tool.name: tool.input_schema for tool in tools.tools}

    # ── Navigation ──────────────────────────────────────────────────────────
    async def navigate_page(self, url: str) -> Any:
        return await self._call("navigate_page", self._page_args(type="url", url=url))

    async def export_authenticated_session(self) -> dict[str, Any]:
        """Copy browser storage in memory; never write credentials to checkpoints."""
        from playwright.async_api import async_playwright

        async with async_playwright() as playwright:
            browser = await playwright.chromium.connect_over_cdp(
                f"http://127.0.0.1:{self._cdp_port}"
            )
            context = browser.contexts[0]
            # CDP attaches after navigation. Register the current origins with
            # Playwright before exporting local storage and IndexedDB.
            for page in context.pages:
                if page.url.startswith("http"):
                    await page.reload(wait_until="domcontentloaded")
            state = await context.storage_state(indexed_db=True)
            state["session_storage"] = [
                await page.evaluate("() => ({origin: location.origin, items: Object.entries(sessionStorage)})")
                for page in context.pages if page.url.startswith("http")
            ]
            return state

    async def import_authenticated_session(self, state: dict[str, Any]) -> None:
        from playwright.async_api import async_playwright

        if self._exit_stack is None:
            raise RuntimeError("Session import requires an active browser context")
        # Keep this CDP connection alive: init scripts belong to its session
        # and disappear on disconnect before the next MCP navigation.
        playwright = await self._exit_stack.enter_async_context(async_playwright())
        browser = await playwright.chromium.connect_over_cdp(
            f"http://127.0.0.1:{self._cdp_port}"
        )
        context = browser.contexts[0]
        await context.set_storage_state(storage_state={
            "cookies": state.get("cookies", []), "origins": state.get("origins", [])
        })
        await context.add_init_script(
            "for (const entry of " + json.dumps(state.get("session_storage", [])) + ") {"
            "if (location.origin === entry.origin) for (const [key, value] of entry.items)"
            "if (sessionStorage.getItem(key) === null) sessionStorage.setItem(key, value); }"
        )
        self._authenticated = True

    async def new_page(self, url: str | None = None) -> Any:
        return await self._call("new_page", {"url": url} if url else {})

    async def list_pages(self) -> Any:
        return await self._call("list_pages", {})

    # ── Inspection ──────────────────────────────────────────────────────────
    async def take_snapshot(self) -> Any:
        return await self._call("take_snapshot", self._page_args())

    async def take_screenshot(self) -> Any:
        directory = Path(os.environ.get("DISCOVERY_EVIDENCE_DIR", "artifacts/discovery")).resolve()
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / f"{uuid.uuid4()}.png"
        content = await self._call("take_screenshot", self._page_args(fullPage=True))
        image = next((block for block in content if getattr(block, "type", None) == "image"), None)
        if image is None or not getattr(image, "data", None):
            raise RuntimeError("Discovery screenshot returned no image data")
        target.write_bytes(base64.b64decode(image.data))
        return f"/api/v1/application-maps/evidence/{target.name}"

    async def inspect_elements(self, snapshot: object) -> list[dict[str, Any]]:
        """Attach browser-read attributes to the exact observed MCP controls."""
        nodes = parse_elements(snapshot)
        controls = [
            e
            for e in nodes
            if e["role"]
            in {
                "link",
                "button",
                "textbox",
                "combobox",
                "radio",
                "checkbox",
                "searchbox",
                "spinbutton",
            }
        ]
        function = """(...els) => els.map(el => ({
            dom_id: el.id || null,
            locator: el.id ? '#' + CSS.escape(el.id) : null,
            input_type: el.getAttribute('type'),
            required: !!el.required,
            disabled: !!el.disabled,
            visible: !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length),
            url: el.href || null,
            options: el.options ? [...el.options].filter(o => !o.disabled).map(o => o.value) : null
        }))"""
        try:
            for offset in range(0, len(controls), 30):
                batch = controls[offset : offset + 30]
                raw = await self._call(
                    "evaluate_script",
                    self._page_args(
                        function=function,
                        args=[e["uid"] for e in batch],
                        waitForStableDom=False,
                    ),
                )
                text = _snapshot_text(raw)
                match = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
                data = json.loads(match.group(1) if match else text)
                for node, attributes in zip(batch, data, strict=True):
                    node.update({k: v for k, v in attributes.items() if v is not None})
        except Exception:
            logger.debug("Could not attach DOM attributes to snapshot controls", exc_info=True)
        with suppress(Exception):
            nodes = merge_dom_hrefs(nodes, await self._same_origin_dom_hrefs())
        return nodes

    async def _same_origin_dom_hrefs(self) -> list[dict[str, Any]]:
        """Collect in-page <a href> values the accessibility snapshot may omit.

        Header icons (cart, profile) are often unlabeled SVGs wrapping a link.
        Navigating those hrefs is enough; we do not click unnamed buttons.
        """
        function = """() => {
            const labelOf = (el) => {
                const by = el.getAttribute("aria-labelledby");
                const labelled = by
                    ? by.split(/\\s+/).map((id) => document.getElementById(id)?.innerText || "").join(" ")
                    : "";
                return (el.getAttribute("aria-label") || el.getAttribute("title") || labelled || el.textContent || "")
                    .replace(/\\s+/g, " ").trim();
            };
            const seen = new Set();
            const links = [];
            for (const a of document.querySelectorAll("a[href]")) {
                const href = a.href;
                if (!href || href.startsWith("javascript:")) continue;
                let parsed;
                try { parsed = new URL(href); } catch { continue; }
                if (parsed.origin !== location.origin) continue;
                const key = parsed.pathname.replace(/\\/+$/, "") + parsed.hash;
                if (seen.has(key)) continue;
                seen.add(key);
                links.push({ name: labelOf(a), url: parsed.href });
            }
            return links;
        }"""
        raw = await self._call(
            "evaluate_script",
            self._page_args(function=function, waitForStableDom=False),
        )
        text = _evaluate_script_text(raw)
        match = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
        payload = match.group(1) if match else text
        data = json.loads(payload)
        if not isinstance(data, list):
            return []
        return [item for item in data if isinstance(item, dict) and item.get("url")]

    async def list_console_messages(self) -> Any:
        return await self._call("list_console_messages", self._page_args())

    async def list_network_requests(self) -> Any:
        return await self._call("list_network_requests", self._page_args())

    # ── Input automation ────────────────────────────────────────────────────
    async def click(self, element_ref: str) -> Any:
        return await self._call("click", self._page_args(uid=element_ref))

    async def fill(self, element_ref: str, value: str) -> Any:
        return await self._call("fill", self._page_args(uid=element_ref, value=value))

    @staticmethod
    def _is_stale_interaction_error(error: Exception) -> bool:
        message = str(error).lower()
        return (
            "did not become interactive" in message
            or "failed to interact with the element" in message
            or "node is detached" in message
            or "element is not attached" in message
        )

    async def _set_control_value(self, element_ref: str, value: str) -> Any:
        """Set a re-rendering controlled input in one browser-side operation."""
        value_json = json.dumps(value)
        function = """(element) => {
            const nextValue = VALUE;
            element.focus();
            const prototype = Object.getPrototypeOf(element);
            const setter = Object.getOwnPropertyDescriptor(prototype, "value")?.set;
            if (setter) setter.call(element, nextValue);
            else element.value = nextValue;
            element.dispatchEvent(new InputEvent("input", {
                bubbles: true,
                inputType: "insertText",
                data: nextValue,
            }));
            element.dispatchEvent(new Event("change", { bubbles: true }));
            return element.value;
        }""".replace("VALUE", value_json)
        return await self._call(
            "evaluate_script",
            self._page_args(function=function, args=[element_ref], waitForStableDom=False),
        )

    async def _fill_authentication_field(
        self,
        element_ref: str,
        value: str,
        names: list[str],
        roles: set[str],
        description: str,
    ) -> None:
        """Fill a login field, recovering when a reactive page replaces its DOM node."""
        try:
            await self.fill(element_ref, value)
            return
        except RuntimeError as error:
            if not self._is_stale_interaction_error(error):
                raise
            first_error = error

        # A controlled input can be replaced while chrome-devtools-mcp types.
        # Reacquire it from a fresh snapshot, then update it atomically while
        # still dispatching the events expected by React/Vue/Angular forms.
        await asyncio.sleep(0.2)
        controls = _parse_controls(_snapshot_text(await self.take_snapshot()))
        refreshed_ref = _find_uid(controls, names, roles)
        if not refreshed_ref:
            raise RuntimeError(
                f"{description.capitalize()} disappeared while the login form was updating."
            ) from first_error
        try:
            await self._set_control_value(refreshed_ref, value)
        except RuntimeError as recovery_error:
            raise RuntimeError(
                f"Could not fill the {description} after the page replaced its input element: "
                f"{recovery_error}"
            ) from recovery_error

    async def _click_authentication_control(
        self,
        element_ref: str,
        names: list[str],
        roles: set[str],
        description: str,
    ) -> None:
        """Click a login control again with a fresh UID if the form re-rendered."""
        try:
            await self.click(element_ref)
            return
        except RuntimeError as error:
            if not self._is_stale_interaction_error(error):
                raise
            first_error = error

        await asyncio.sleep(0.2)
        controls = _parse_controls(_snapshot_text(await self.take_snapshot()))
        refreshed_ref = _find_uid(controls, names, roles)
        if not refreshed_ref:
            raise RuntimeError(
                f"{description.capitalize()} disappeared while the login form was updating."
            ) from first_error
        await self.click(refreshed_ref)

    async def handle_dialog(self, action: str = "dismiss") -> Any:
        return await self._call("handle_dialog", {"action": action})

    async def wait_for(self, text: str | None = None, timeout_ms: int = 5000) -> Any:
        args: dict[str, Any] = {"timeout": timeout_ms}
        if text:
            args["text"] = text
        return await self._call("wait_for", args)

    async def clear_cookies(self) -> None:
        """
        Clear all browser cookies so no cached auth session can redirect
        the crawler away from the login page during Phase 1.
        Falls back to navigating to about:blank if the MCP tool is unavailable.
        """
        try:
            await self._call("clear_cookies", {})
        except Exception:
            # chrome-devtools-mcp may not expose clear_cookies in all versions.
            # Fallback: navigate to about:blank to drop session cookies.
            try:
                await self._call("navigate_page", self._page_args(type="url", url="about:blank"))
                await asyncio.sleep(0.3)
            except Exception:
                pass  # best-effort — crawl continues even if cookies can't be cleared

    # ── CAPTCHA-aware wait ───────────────────────────────────────────────────
    async def wait_until_ready(self) -> None:
        """Wait for the application, including hosting cold starts, to become usable."""
        previous_signature: tuple[tuple[str, str, str], ...] = ()
        last_text = ""
        started = time.monotonic()
        last_reload = started
        saw_hosting_cold_start = False
        security_verification_started: float | None = None

        while time.monotonic() - started < self._page_ready_timeout_seconds:
            text = _snapshot_text(await self.take_snapshot())
            last_text = text
            security_verification = _is_security_verification_page(text)
            now = time.monotonic()
            if security_verification:
                security_verification_started = security_verification_started or now
                if (
                    now - security_verification_started
                    >= self._security_verification_timeout_seconds
                ):
                    raise SecurityVerificationRequiredError(
                        "The target is showing an interactive security verification page "
                        "(for example, Cloudflare Turnstile). Discovery waited for it to "
                        "clear automatically but did not click or bypass the challenge. "
                        "Allowlist the discovery worker or disable the challenge for the "
                        "test/staging hostname, then continue discovery from the checkpoint."
                    )
                await asyncio.sleep(self._ready_poll_interval_seconds)
                continue
            security_verification_started = None
            elements = parse_elements(text)
            usable_controls = any(
                element["role"] in {"link", "button", "textbox", "combobox", "checkbox", "radio"}
                for element in elements
            )
            hosting_cold_start = _is_hosting_cold_start_page(text)
            saw_hosting_cold_start = saw_hosting_cold_start or hosting_cold_start
            loading = hosting_cold_start or (
                bool(re.search(r"\b(?:loading|please wait|starting service)\b", text, re.I))
                and not usable_controls
            )
            # An image CAPTCHA is not in the accessibility tree. A stable form
            # that already shows an answer box is ready; login reads the image
            # afterwards. Waiting here burned the full readiness timeout on
            # role switches such as Employee / Kitchen / Admin.
            captcha_still_loading = (
                bool(_CAPTCHA_FIELD_PATTERN.search(text))
                and not bool(_MATH_EXPRESSION.search(text))
                and not usable_controls
            )
            signature = tuple(
                (
                    element["role"],
                    re.sub(r"\d+", "#", element["name"]),
                    element.get("url", ""),
                )
                for element in elements
                if element["role"]
                in {
                    "RootWebArea",
                    "heading",
                    "link",
                    "button",
                    "textbox",
                    "combobox",
                    "checkbox",
                    "radio",
                }
            )
            if (
                signature
                and signature == previous_signature
                and not loading
                and not captcha_still_loading
            ):
                return

            if (
                hosting_cold_start
                and now - last_reload >= self._cold_start_reload_interval_seconds
            ):
                root = next(
                    (element for element in elements if element["role"] == "RootWebArea"),
                    None,
                )
                current_url = root.get("url") if root else None
                if current_url and urlparse(current_url).scheme in {"http", "https"}:
                    await self.navigate_page(current_url)
                    previous_signature = ()
                    last_reload = time.monotonic()
                    await asyncio.sleep(self._ready_poll_interval_seconds)
                    continue

            previous_signature = signature
            await asyncio.sleep(self._ready_poll_interval_seconds)

        timeout = self._page_ready_timeout_seconds
        if saw_hosting_cold_start:
            raise ApplicationReadinessTimeoutError(
                "Target application remained on a hosting cold-start page for "
                f"{timeout:g} seconds. The service did not finish waking before discovery's "
                "readiness deadline. Increase DISCOVERY_PAGE_READY_TIMEOUT_SECONDS or keep "
                "the target service warm, then retry discovery."
            )
        available_controls = [
            f'{element["role"]}:{element["name"][:80]}'
            for element in parse_elements(last_text)
            if element["role"] in {"link", "button", "textbox", "combobox", "checkbox", "radio"}
        ][:10]
        raise ApplicationReadinessTimeoutError(
            f"Application did not become ready within {timeout:g} seconds; "
            f"available_controls={available_controls!r}"
        )

    # ── Authentication ───────────────────────────────────────────────────────
    async def _read_visible_login_text(self) -> str:
        """Read visible login copy plus math text hidden inside SVG CAPTCHA images."""
        result = await self._call(
            "evaluate_script",
            self._page_args(
                function="""async () => {
                    const chunks = [];
                    const push = (value) => {
                        const text = (value || "").toString().trim();
                        if (text) chunks.push(text);
                    };
                    const captchaFromSvg = (svg) => {
                        const doc = new DOMParser().parseFromString(svg, "image/svg+xml");
                        return [...doc.querySelectorAll("tspan")]
                            .map((node, index) => ({
                                x: node.hasAttribute("x")
                                    ? parseFloat(node.getAttribute("x"))
                                    : index,
                                text: (node.textContent || "").trim(),
                            }))
                            .filter((item) => item.text)
                            .sort((a, b) => a.x - b.x)
                            .map((item) => item.text)
                            .join(" ");
                    };
                    const decodeSvgDataUri = (src) => {
                        if (!src || !src.startsWith("data:image/svg+xml")) return "";
                        const separator = src.indexOf(",");
                        if (separator < 0) return "";
                        const encoded = src.slice(separator + 1);
                        const svg = src.includes(";base64,")
                            ? atob(encoded)
                            : decodeURIComponent(encoded);
                        return captchaFromSvg(svg);
                    };
                    push(document.body?.innerText || "");
                    for (const svg of document.querySelectorAll("svg")) {
                        push(svg.textContent);
                    }
                    for (const image of document.images) {
                        if (!image.getClientRects().length) continue;
                        const src = image.currentSrc || image.src || "";
                        const hint = [
                            image.alt, image.title, image.id, image.className, image.src
                        ].join(" ");
                        if (
                            /captcha|challenge|verify|math/i.test(hint)
                            || src.startsWith("data:image/svg+xml")
                            || src.startsWith("blob:")
                            || /\\.svg(?:[?#]|$)/i.test(src)
                        ) {
                            push(image.alt);
                            try {
                                if (src.startsWith("data:image/svg+xml")) {
                                    push(decodeSvgDataUri(src));
                                } else if (src.startsWith("blob:")) {
                                    // Read the image already displayed by this form. Never
                                    // request a new challenge: it would invalidate its token.
                                    const response = await fetch(src, {
                                        signal: AbortSignal.timeout(2000)
                                    });
                                    if (response.ok && /svg/i.test(
                                        response.headers.get("content-type") || ""
                                    )) {
                                        const svg = await response.text();
                                        if (image.isConnected &&
                                            (image.currentSrc || image.src) === src) {
                                            push(captchaFromSvg(svg));
                                        }
                                    }
                                }
                            } catch (_) {
                                // A revoked blob or malformed image must not discard
                                // other readable challenges on the page.
                            }
                        }
                    }
                    for (const node of document.querySelectorAll(
                        '[id*="captcha" i], [class*="captcha" i], [aria-label*="captcha" i]'
                    )) {
                        push(node.innerText || node.textContent);
                        if (node.tagName === "IMG") push(decodeSvgDataUri(node.src));
                    }
                    return chunks.join("\\n");
                }""",
                args=[],
                waitForStableDom=False,
            ),
        )
        return _evaluate_script_text(result)

    async def _login_text_for_captcha(self) -> str:
        snapshot = _snapshot_text(await self.take_snapshot())
        decoded = _svg_captcha_text(snapshot)
        if _solve_math_captcha(decoded) is not None:
            return decoded
        with suppress(Exception):
            snapshot = f"{await self._read_visible_login_text()}\n{snapshot}"
        return snapshot

    async def _wait_for_math_captcha(self) -> tuple[int | None, str]:
        """Poll until the arithmetic CAPTCHA is readable from the DOM or snapshot."""
        text = ""
        for _ in range(20):
            text = await self._login_text_for_captcha()
            answer = _solve_math_captcha(text)
            if answer is not None:
                return answer, text
            await asyncio.sleep(0.5)
        return None, text

    async def _wait_for_submit_uid(
        self, names: list[str], timeout_seconds: float = 8.0
    ) -> tuple[list[tuple[str, str, str]], str | None]:
        """Wait for LOGIN to reappear after a failed submit or CAPTCHA refresh."""
        deadline = time.monotonic() + timeout_seconds
        controls: list[tuple[str, str, str]] = []
        submit_uid = None
        while time.monotonic() < deadline:
            controls = _parse_controls(_snapshot_text(await self.take_snapshot()))
            submit_uid = _find_submit_uid(controls, names)
            if submit_uid:
                return controls, submit_uid
            await asyncio.sleep(0.4)
        return controls, submit_uid

    async def _select_login_role(
        self, secret: dict[str, str], controls: list[tuple[str, str, str]]
    ) -> list[tuple[str, str, str]]:
        """Click Employee/Admin (or similar) before filling the matching ID field."""
        role_name = (secret.get("account_role") or "").strip()
        available_roles = [
            name
            for _uid, role, name in controls
            if role in {"radio", "tab", "button"} and name.lower() in _LOGIN_ROLE_BUTTONS
        ]
        if role_name.lower() in {"", "user", "default", "test account"}:
            if any(name.lower() == "employee" for name in available_roles):
                role_name = "Employee"
            elif available_roles:
                role_name = available_roles[0]
            else:
                return controls
        role_names = [
            role_name,
            f"{role_name} login",
            f"login as {role_name}",
            f"{role_name} portal",
        ]
        role_uid = _find_uid(
            controls, role_names, {"radio", "tab", "button", "option", "menuitem"}
        )
        if not role_uid:
            return controls
        await self._click_authentication_control(
            role_uid,
            role_names,
            {"radio", "tab", "button", "option", "menuitem"},
            "login role",
        )
        await self.wait_until_ready()
        return _parse_controls(_snapshot_text(await self.take_snapshot()))

    async def authenticate(self) -> None:
        """
        Fill the login form — including arithmetic CAPTCHA — and submit.

        BUG FIXES vs previous version:
        1. wait_until_ready() is called AGAIN after navigate to guarantee the
           CAPTCHA math question is fully rendered before we read the snapshot.
           Previously we called wait_until_ready() in _go_to_start() before
           authenticate(), but the CAPTCHA number can appear slightly after the
           rest of the form — so the first snapshot passed the ready check but
           still showed no math expression.

        2. The snapshot is re-read INSIDE authenticate() after waiting, not
           reused from the caller. This ensures the math question is present
           in the text we parse.

        3. After filling and clicking submit, we retry the login if we see
           "incorrect captcha answer" — the CAPTCHA refreshes on each wrong
           attempt, so we re-read the new question and answer it again. We
           retry up to 3 times before giving up.

        4. submit_selector now also matches "log in" (two words) in addition
           to "login" (one word), matching your app's button text exactly.
        """
        if self._credential_ref is None:
            return
        logger.info("[DISCOVERY AUTH] Starting authentication credential_ref=%s", self._credential_ref)
        current = _parse_controls(_snapshot_text(await self.take_snapshot()))
        if self._authenticated and not any(
            role in {"textbox", "input"} and name.lower() == "password" for _, role, name in current
        ):
            return
        self._authenticated = False

        secret = await resolve_login(self._credential_ref, self._project_id)

        last_page_text = ""
        login_page_text = ""
        for attempt in range(3):
            # Always re-read the snapshot fresh — CAPTCHA changes on each attempt
            await self.wait_until_ready()
            text = _snapshot_text(await self.take_snapshot())
            if _authentication_completed(text, login_page_text):
                self._authenticated = True
                return
            login_page_text = text
            if not _MATH_EXPRESSION.search(text):
                # A visual CAPTCHA can be absent from the accessibility tree.
                # Read visible DOM text and inline SVG text through the same
                # isolated MCP session. Cafinity renders the question as an
                # SVG image, so document.body.innerText alone cannot see it.
                with suppress(Exception):
                    dom_text = await self._read_visible_login_text()
                    text = f"{text}\n{dom_text}"
            controls = _parse_controls(text)
            controls = await self._select_login_role(secret, controls)
            logger.debug(
                "[DISCOVERY AUTH] Page controls: %s",
                [
                    {
                        "role": role,
                        "name": name,
                        "uid": uid,
                    }
                    for uid, role, name in controls
                ][:30],
            )
            # ── Locate form fields ──────────────────────────────────────────
            # Custom selectors from the stored credential are always first.
            # Fallback lists cover common field names across any website.
            # _find_uid uses 3-pass fuzzy matching so partial names also work.
            account_role = secret.get("account_role", "")
            username_names = [
                secret.get("username_selector", ""),
                f"{account_role} id" if account_role else "",
                f"{account_role} id number" if account_role else "",
                "employee id",
                "employee number",
                "staff id",
                # Common email/username field names across websites
                "email",
                "email address",
                "e-mail",
                "your email",
                "username",
                "user name",
                "user",
                "userid",
                "user id",
                "login",
                "account",
                "phone",
                "mobile",
                "mobile number",
                "id",
                "identifier",
            ]
            username_uid = _find_uid(
                controls,
                username_names,
                {"textbox", "input", "combobox", "searchbox"},
            )
            password_names = [
                secret.get("password_selector", ""),
                "password",
                "pass",
                "passphrase",
                "secret",
                "pin",
                "your password",
                "current password",
            ]
            password_uid = _find_uid(
                controls,
                password_names,
                {"textbox", "input"},
            )
            submit_names = [
                secret.get("submit_selector", ""),
                # Common submit button names across websites
                "log in",
                "login",
                "sign in",
                "signin",
                "submit",
                "continue",
                "next",
                "go",
                "enter",
                "access",
                "proceed",
                "send",
                "get started",
                "let me in",
            ]
            submit_uid = _find_submit_uid(controls, submit_names)
            if not submit_uid:
                controls, submit_uid = await self._wait_for_submit_uid(submit_names)
            logger.info(
                "[DISCOVERY AUTH] Detected login controls: %s",
                {
                    "username_uid": username_uid,
                    "password_uid": password_uid,
                    "submit_uid": submit_uid,
                },
            )
            if not username_uid or not password_uid or not submit_uid:
                snapshot = _snapshot_text(await self.take_snapshot())
                if _authenticated_session_visible(snapshot):
                    logger.info("[DISCOVERY AUTH] Already on an authenticated page; skipping login form.")
                    self._authenticated = True
                    return
                missing = []
                if not username_uid:
                    missing.append(
                        f"username field (tried selector={secret.get('username_selector')!r})"
                    )
                if not password_uid:
                    missing.append(
                        f"password field (tried selector={secret.get('password_selector')!r})"
                    )
                if not submit_uid:
                    missing.append(
                        f"submit button (tried selector={secret.get('submit_selector')!r})"
                    )
                available = [
                    (role, name)
                    for _, role, name in controls
                    if role in {"textbox", "input", "button", "link", "combobox", "radio"}
                ]
                if attempt < 2:
                    logger.warning(
                        "[DISCOVERY AUTH] Login controls missing, reloading form: %s", missing
                    )
                    login_url = secret.get("login_url")
                    if not login_url:
                        snapshot = _snapshot_text(await self.take_snapshot())
                        match = re.search(r'url="(https?://[^"]+)"', snapshot)
                        login_url = match.group(1) if match else None
                    if login_url:
                        await self.navigate_page(login_url)
                    await self.wait_until_ready()
                    continue
                raise RuntimeError(
                    f"Login form detection failed on attempt {attempt + 1}. "
                    f"Could not find: {', '.join(missing)}. "
                    f"Available interactive elements on page: {available[:10]}. "
                    "Fix by passing custom selectors when saving this credential "
                    "(scripts/store_credential.py --username-selector '...' --submit-selector '...')"
                )

            # ── Fill credentials ────────────────────────────────────────────
            await self._fill_authentication_field(
                username_uid,
                secret["username"],
                username_names,
                {"textbox", "input", "combobox", "searchbox"},
                "username field",
            )
            await self._fill_authentication_field(
                password_uid,
                secret["password"],
                password_names,
                {"textbox", "input"},
                "password field",
            )

            # ── Solve the CAPTCHA when it is a supported arithmetic challenge ─
            # Read the question AFTER filling credentials: some apps regenerate
            # their challenge when the username/password fields change.
            captcha_names = [
                secret.get("captcha_selector", ""),
                "enter the answer",
                "your answer",
                "captcha answer",
                "answer",
                "captcha",
            ]
            answer, text = await self._wait_for_math_captcha()
            controls = _parse_controls(_snapshot_text(await self.take_snapshot()))
            captcha_uid = _find_uid(
                controls,
                captcha_names,
                {"textbox", "input", "spinbutton"},
            )

            challenge_kind = _captcha_challenge_kind(text)
            has_captcha_image = bool(re.search(r'\bimage\b[^"]*"CAPTCHA"', text, re.I)) or bool(
                _SVG_DATA_URI.search(text)
            )
            if challenge_kind == "generic" and not has_captcha_image:
                raise RuntimeError(
                    "A CAPTCHA challenge is visible on the login form, but it is not a "
                    "supported arithmetic CAPTCHA. The discovery agent can only solve "
                    "standard math-style challenges; this page requires a generic CAPTCHA "
                    "workflow, a custom solver, or manual verification before discovery can continue. "
                    f"Page text snippet: {text[:500]}"
                )

            if captcha_uid:
                if answer is None:
                    refresh_uid = _find_uid(
                        controls,
                        ["refresh captcha", "reload captcha", "new captcha", "refresh"],
                        {"button", "link"},
                    )
                    # Some apps occasionally return an empty challenge and ask the
                    # user to refresh it. Mirror that recovery automatically.
                    if refresh_uid:
                        for _refresh_attempt in range(3):
                            await self.click(refresh_uid)
                            answer, text = await self._wait_for_math_captcha()
                            if answer is not None:
                                break
                    if answer is None:
                        raise RuntimeError(
                            "CAPTCHA remained unavailable after waiting for the challenge "
                            "and 3 automatic refreshes. "
                            f"Page text snippet: {text[:500]}"
                        )
                await self._fill_authentication_field(
                    captcha_uid,
                    str(answer),
                    captcha_names,
                    {"textbox", "input", "spinbutton"},
                    "CAPTCHA field",
                )
            elif _CAPTCHA_FIELD_PATTERN.search(text) or _solve_math_captcha(text) is not None:
                raise RuntimeError(
                    "A CAPTCHA field is visible but no answer input could be detected. "
                    f"Page text snippet: {text[:500]}"
                )

            # ── Submit ──────────────────────────────────────────────────────
            controls, submit_uid = await self._wait_for_submit_uid(submit_names)
            if not submit_uid:
                continue
            await self._click_authentication_control(
                submit_uid,
                submit_names,
                {"button"},
                "login button",
            )

            # ── Wait and check result ───────────────────────────────────────
            # Poll until the SPA leaves the login form. Cafinity can take
            # longer than 10s after a correct CAPTCHA before Logout appears.
            for _ in range(60):
                await asyncio.sleep(0.5)
                after = _snapshot_text(await self.take_snapshot())
                last_page_text = after
                logger.debug("[DISCOVERY AUTH] Post-login snapshot: %s", after[:1000])
                if _authenticated_session_visible(after):
                    self._authenticated = True
                    logger.info("[DISCOVERY AUTH] Login succeeded; authenticated session is visible.")
                    return

                if _INCORRECT_CAPTCHA_PATTERN.search(after):
                    await self._wait_for_submit_uid(submit_names)
                    break

                if _login_form_visible(after) and re.search(
                    r"\b(?:invalid|incorrect).{0,30}(?:email|password|credential|employee id)\b",
                    after,
                    re.I,
                ):
                    raise RuntimeError(
                        "Authentication failed: wrong email or password. "
                        "Update the credential using scripts/store_credential.py."
                    )

        raise RuntimeError(
            "Authentication failed after 3 attempts — login did not leave the "
            "sign-in page. If a math CAPTCHA is shown, confirm it is visible "
            "and that the Employee/Admin role radio is selected. "
            f"Page text snippet: {last_page_text[:400]}"
        )
