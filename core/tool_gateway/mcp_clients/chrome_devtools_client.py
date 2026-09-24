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
import uuid
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from core.tool_gateway.secret_resolver import resolve_login
from core.tool_gateway.snapshot import parse_elements


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
    match = re.search(r"\b(\d+)\s*([+\-*/x×÷])\s*(\d+)\b", text)
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
    }.get(op)


class ChromeDevToolsClient:
    def __init__(
        self,
        allowed_url_pattern: str | None = None,
        headless: bool = True,
        credential_ref: str | None = None,
    ) -> None:
        self._allowed_url_pattern = allowed_url_pattern
        self._headless = headless
        self._credential_ref = credential_ref
        self._authenticated = False
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
        command = os.environ.get("CHROME_DEVTOOLS_MCP_COMMAND")
        args = [] if command else ["-y", "chrome-devtools-mcp@latest"]

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
        return str(target)

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
        """
        Wait for a stable snapshot. Special rule: if the page has a CAPTCHA
        section but the math question ('What is N op M?') has not appeared yet,
        keep waiting — the question loads asynchronously and we must not proceed
        before we can read it.
        """
        previous_signature: tuple[tuple[str, str, str], ...] = ()
        for _ in range(30):
            text = _snapshot_text(await self.take_snapshot())
            loading = bool(re.search(r"\b(?:loading|please wait|starting service)\b", text, re.I))
            has_captcha_label = "captcha" in text.lower()
            has_math_question = bool(re.search(r"\b\d+\s*[+\-*/x×÷]\s*\d+\b", text))
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
                for element in parse_elements(text)
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
            previous_signature = signature
            await asyncio.sleep(0.5)
        raise RuntimeError("Application did not become ready (timed out after 15 s)")

    # ── Authentication ───────────────────────────────────────────────────────
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
            controls = _parse_controls(text)

            # ── Locate form fields ──────────────────────────────────────────
            # Custom selectors from the stored credential are always first.
            # Fallback lists cover common field names across any website.
            # _find_uid uses 3-pass fuzzy matching so partial names also work.
            username_uid = _find_uid(
                controls,
                [
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
                ],
                {"textbox", "input", "combobox", "searchbox"},
            )
            password_uid = _find_uid(
                controls,
                [
                    secret.get("password_selector", ""),
                    "password",
                    "pass",
                    "passphrase",
                    "secret",
                    "pin",
                    "your password",
                    "current password",
                ],
                {"textbox", "input"},
            )
            submit_uid = _find_uid(
                controls,
                [
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
                ],
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
            await self.fill(username_uid, secret["username"])
            await self.fill(password_uid, secret["password"])

            # ── Solve arithmetic CAPTCHA ────────────────────────────────────
            # BUG FIX 1+2: snapshot is already fresh from above — math question
            # is guaranteed present because wait_until_ready() waited for it.
            captcha_uid = _find_uid(
                controls,
                [
                    secret.get("captcha_selector", ""),
                    "your answer",
                    "captcha answer",
                    "answer",
                    "captcha",
                ],
                {"textbox", "input"},
            )

            if captcha_uid:
                answer = _solve_math_captcha(text)
                if answer is None:
                    raise RuntimeError(
                        f"CAPTCHA input found but could not parse math question from page text.\n"
                        f"Page text snippet: {text[:500]}"
                    )
                await self.fill(captcha_uid, str(answer))

            # ── Submit ──────────────────────────────────────────────────────
            await self.click(submit_uid)

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
