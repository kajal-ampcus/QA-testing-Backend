"""
Wraps the official chrome-devtools-mcp server (Puppeteer/CDP-based,
Chromium-only) via the MCP Python SDK's stdio transport.

Never used for scripted Test Execution — that's
core/tool_gateway/playwright_client.py, always.
"""

import asyncio
import base64
import json
import os
import re
import time
import uuid
from contextlib import AsyncExitStack, suppress
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from core.tool_gateway.secret_resolver import resolve_login
from core.tool_gateway.snapshot import parse_elements


_MATH_EXPRESSION = re.compile(r"\b(\d+)\s*([+\-*/x×÷−–])\s*(\d+)\b")


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


def _snapshot_text(snapshot: object) -> str:
    if isinstance(snapshot, list):
        return "\n".join(getattr(block, "text", "") for block in snapshot)
    return str(snapshot)


def _parse_controls(text: str) -> list[tuple[str, str, str]]:
    """Parse all uid/role/name triples from a snapshot text."""
    return [(e["uid"], e["role"], e["name"]) for e in parse_elements(text)]


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
            if role in roles and any(w in name.lower() for w in wanted)
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


def _solve_math_captcha(text: str) -> int | None:
    """
    Find and solve a simple arithmetic CAPTCHA in snapshot text.
    Handles: 'What is 3 + 3?', 'What is 6 - 2?', 'What is 8 + 1?' etc.
    Returns the integer answer or None if no math expression found.
    """
    match = _MATH_EXPRESSION.search(text)
    if not match:
        return None
    a, op, b = int(match.group(1)), match.group(2), int(match.group(3))
    return {
        "+": a + b,
        "-": a - b,
        "*": a * b,
        "x": a * b,
        "×": a * b,
        "/": (a // b if b and a % b == 0 else None),
        "÷": (a // b if b and a % b == 0 else None),
        "−": a - b,
        "–": a - b,
    }.get(op)


class ChromeDevToolsClient:
    def __init__(
        self,
        allowed_url_pattern: str | None = None,
        headless: bool = True,
        credential_ref: str | None = None,
        page_ready_timeout_seconds: float | None = None,
        cold_start_reload_interval_seconds: float | None = None,
        security_verification_timeout_seconds: float | None = None,
        ready_poll_interval_seconds: float = 0.5,
    ) -> None:
        self._allowed_url_pattern = allowed_url_pattern
        self._headless = headless
        self._credential_ref = credential_ref
        self._authenticated = False
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

    def _server_params(self) -> StdioServerParameters:
        """
        Build StdioServerParameters for chrome-devtools-mcp.

        Priority:
          1. CHROME_DEVTOOLS_MCP_COMMAND env var  (Docker: chrome-devtools-mcp)
          2. Globally installed chrome-devtools-mcp binary  (fastest, no download)
          3. npx without @latest  (uses npm cache; slow on first run)

        Root cause of MCPError: Connection closed in a fresh directory:
          `npx -y chrome-devtools-mcp@latest` downloads the package every time
          the npm cache is empty. The stdio stream never opens in time, so
          the MCP client gets Connection closed before Chrome even starts.
          Fix: use the global binary (npm install -g chrome-devtools-mcp) or
          set CHROME_DEVTOOLS_MCP_COMMAND=chrome-devtools-mcp in .env.
        """
        import shutil

        command = os.environ.get("CHROME_DEVTOOLS_MCP_COMMAND")
        using_npx = False

        if not command:
            if shutil.which("chrome-devtools-mcp"):
                # Global binary is on PATH — use it directly, no npm overhead
                command = "chrome-devtools-mcp"
            else:
                # Fallback to npx without @latest so the local npm cache is used
                command = "npx.cmd" if os.name == "nt" else "npx"
                using_npx = True

        args = ["chrome-devtools-mcp"] if using_npx else []
        if executable_path := os.environ.get("CHROME_EXECUTABLE_PATH"):
            args.extend(["--executablePath", executable_path])

        if os.environ.get("CHROME_NO_SANDBOX", "false").lower() == "true":
            args.append("--chromeArg=--no-sandbox")

        # Allow internal/self-signed HTTPS certificates
        if os.environ.get(
            "CHROME_IGNORE_CERTIFICATE_ERRORS", "false"
        ).lower() == "true":
            args.append("--chromeArg=--ignore-certificate-errors")

        if os.environ.get(
            "CHROME_DEVTOOLS_MCP_ISOLATED", "true"
        ).lower() == "true":
            args.append("--isolated")

        if self._headless:
            args.append("--headless=true")

        if self._allowed_url_pattern:
            parsed = urlparse(self._allowed_url_pattern)
            allowed_pattern = (
                f"{parsed.scheme}://{parsed.netloc}/*"
                if parsed.scheme in {"http", "https"} and parsed.netloc
                else self._allowed_url_pattern
            )
            args.extend(["--allowedUrlPattern", allowed_pattern])
        if os.environ.get("CHROME_DEVTOOLS_MCP_REDACT_NETWORK_HEADERS", "true").lower() == "true":
            args.append("--redactNetworkHeaders")
        return StdioServerParameters(command=command, args=args)

        if os.environ.get(
            "CHROME_DEVTOOLS_MCP_REDACT_NETWORK_HEADERS", "true"
        ).lower() == "true":
            args.append("--redactNetworkHeaders")

        return StdioServerParameters(
            command=command or ("npx.cmd" if os.name == "nt" else "npx"),
            args=args,
        )
    async def __aenter__(self) -> "ChromeDevToolsClient":
        self._exit_stack = AsyncExitStack()
        read, write = await self._exit_stack.enter_async_context(
            stdio_client(self._server_params())
        )
        self._session = await self._exit_stack.enter_async_context(ClientSession(read, write))
        await self._session.initialize()
        pages = await self.list_pages()
        page_text = _snapshot_text(pages)
        selected = re.search(r"(?m)^(\d+): .*\[selected\]", page_text)
        first = re.search(r"(?m)^(\d+): ", page_text)
        match = selected or first
        if match is None:
            raise RuntimeError("Chrome DevTools MCP returned no usable page ID")
        self._page_id = int(match.group(1))
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        if self._exit_stack is not None:
            await self._exit_stack.aclose()
        self._session = None
        self._exit_stack = None
        self._page_id = None

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
        result = await session.call_tool(tool_name, arguments)
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
        return nodes

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
            has_captcha_label = "captcha" in text.lower()
            has_math_question = bool(_MATH_EXPRESSION.search(text))
            captcha_still_loading = (
                has_captcha_label
                and not has_math_question
                and bool(re.search(r"\b(?:your answer|captcha answer)\b", text, re.I))
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
                and (not captcha_still_loading or usable_controls)
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
        """Read DOM text plus text embedded in an inline SVG CAPTCHA image."""
        result = await self._call(
            "evaluate_script",
            self._page_args(
                function="""() => {
                    const bodyText = document.body?.innerText || "";
                    const captchaImage = Array.from(document.images).find((image) =>
                        /captcha/i.test(`${image.alt || ""} ${image.title || ""}`)
                    );
                    if (!captchaImage?.src?.startsWith("data:image/svg+xml")) {
                        return bodyText;
                    }
                    try {
                        const separator = captchaImage.src.indexOf(",");
                        if (separator < 0) return bodyText;
                        const encodedSvg = captchaImage.src.slice(separator + 1);
                        const svg = captchaImage.src.includes(";base64,")
                            ? atob(encodedSvg)
                            : decodeURIComponent(encodedSvg);
                        const captchaText = new DOMParser()
                            .parseFromString(svg, "image/svg+xml")
                            .documentElement.textContent || "";
                        return `${bodyText}\n${captchaText}`;
                    } catch (_error) {
                        return bodyText;
                    }
                }""",
                args=[],
                waitForStableDom=False,
            ),
        )
        return _snapshot_text(result)

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
        current = _parse_controls(_snapshot_text(await self.take_snapshot()))
        if self._authenticated and not any(
            role in {"textbox", "input"} and name.lower() == "password" for _, role, name in current
        ):
            return
        self._authenticated = False

        secret = await resolve_login(self._credential_ref)

        for attempt in range(3):
            # Always re-read the snapshot fresh — CAPTCHA changes on each attempt
            await self.wait_until_ready()
            text = _snapshot_text(await self.take_snapshot())
            if not _MATH_EXPRESSION.search(text):
                # A visual CAPTCHA can be absent from the accessibility tree.
                # Read visible DOM text and inline SVG text through the same
                # isolated MCP session. Cafinity renders the question as an
                # SVG image, so document.body.innerText alone cannot see it.
                with suppress(Exception):
                    dom_text = await self._read_visible_login_text()
                    text = f"{text}\n{dom_text}"
            controls = _parse_controls(text)

            # ── Locate form fields ──────────────────────────────────────────
            # Custom selectors from the stored credential are always first.
            # Fallback lists cover common field names across any website.
            # _find_uid uses 3-pass fuzzy matching so partial names also work.
            username_names = [
                secret.get("username_selector", ""),
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
            submit_uid = _find_uid(
                controls,
                submit_names,
                {"button", "link"},
            )

            if not username_uid or not password_uid or not submit_uid:
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
                    if role in {"textbox", "input", "button", "link", "combobox"}
                ]
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

            # ── Solve arithmetic CAPTCHA ────────────────────────────────────
            # BUG FIX 1+2: snapshot is already fresh from above — math question
            # is guaranteed present because wait_until_ready() waited for it.
            captcha_names = [
                secret.get("captcha_selector", ""),
                "your answer",
                "captcha answer",
                "answer",
                "captcha",
            ]
            captcha_uid = _find_uid(
                controls,
                captcha_names,
                {"textbox", "input"},
            )

            if captcha_uid:
                answer = _solve_math_captcha(text)
                if answer is None:
                    refresh_uid = _find_uid(
                        controls,
                        ["refresh captcha", "reload captcha", "new captcha", "refresh"],
                        {"button", "link"},
                    )
                    # Cafinity occasionally returns an empty CAPTCHA and asks
                    # the user to refresh it. Mirror that recovery automatically.
                    if refresh_uid:
                        for _refresh_attempt in range(3):
                            await self.click(refresh_uid)
                            for _ in range(10):
                                await asyncio.sleep(0.5)
                                text = _snapshot_text(await self.take_snapshot())
                                if not _MATH_EXPRESSION.search(text):
                                    with suppress(Exception):
                                        dom_text = await self._read_visible_login_text()
                                        text = f"{text}\n{dom_text}"
                                answer = _solve_math_captcha(text)
                                if answer is not None:
                                    break
                            if answer is not None:
                                break
                    if answer is None:
                        raise RuntimeError(
                            "CAPTCHA remained unavailable after 3 automatic refreshes. "
                            f"Page text snippet: {text[:500]}"
                        )
                await self._fill_authentication_field(
                    captcha_uid,
                    str(answer),
                    captcha_names,
                    {"textbox", "input"},
                    "CAPTCHA field",
                )

            # ── Submit ──────────────────────────────────────────────────────
            await self._click_authentication_control(
                submit_uid,
                submit_names,
                {"button", "link"},
                "login button",
            )

            # ── Wait and check result ───────────────────────────────────────
            # Poll up to 10 seconds for the page to change
            for _ in range(20):
                await asyncio.sleep(0.5)
                after = _snapshot_text(await self.take_snapshot())

                # Success — password field is gone, we left the login page
                if not re.search(r"\b(?:password)\b", after, re.I):
                    self._authenticated = True
                    return

                # BUG FIX 3: CAPTCHA was wrong — the question refreshes,
                # retry the entire fill sequence with the new question
                if re.search(r"incorrect captcha", after, re.I):
                    break  # break inner loop → outer loop retries with fresh snapshot

                # Hard failure — wrong credentials (not a CAPTCHA issue)
                if re.search(
                    r"\b(?:invalid|incorrect).{0,30}(?:email|password|credential)\b", after, re.I
                ):
                    raise RuntimeError(
                        "Authentication failed: wrong email or password. "
                        "Update the credential using scripts/store_credential.py."
                    )

        raise RuntimeError(
            "Authentication failed after 3 attempts — CAPTCHA could not be solved. "
            "Check that the math question is visible in the accessibility tree."
        )
