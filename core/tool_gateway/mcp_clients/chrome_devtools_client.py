"""
Wraps the official chrome-devtools-mcp server (Puppeteer/CDP-based,
Chromium-only) via the MCP Python SDK's stdio transport — for the two
exploratory use cases: Application Discovery and Failure Analysis's
reproduction step (architecture doc Section 10/11, companion doc Part 3).
Never used for scripted Test Execution — that's
core/tool_gateway/playwright_client.py, always.

Spawns `npx chrome-devtools-mcp@latest` as a subprocess, one per Discovery
run/shard (a fresh, isolated Chrome instance each time — --isolated), with
--allowedUrlPattern scoped to the crawl target and --redactNetworkHeaders on
by default (Section 20/28 — never let auth tokens/cookies reach agent
context via network evidence).

Navigation, snapshots, and clicks were checked against a live MCP server.
The other convenience methods still need schema checks before use.
"""

import asyncio
import os
import re
from contextlib import AsyncExitStack
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from core.tool_gateway.secret_resolver import resolve_login


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

    def _server_params(self) -> StdioServerParameters:
        args = ["chrome-devtools-mcp@latest"]
        if os.environ.get("CHROME_DEVTOOLS_MCP_ISOLATED", "true").lower() == "true":
            args.append("--isolated")
        if self._headless:
            args.append("--headless=true")
        if self._allowed_url_pattern:
            args.extend(["--allowedUrlPattern", self._allowed_url_pattern])
        if os.environ.get("CHROME_DEVTOOLS_MCP_REDACT_NETWORK_HEADERS", "true").lower() == "true":
            args.append("--redactNetworkHeaders")
        return StdioServerParameters(command="npx.cmd" if os.name == "nt" else "npx", args=args)

    async def __aenter__(self) -> "ChromeDevToolsClient":
        self._exit_stack = AsyncExitStack()
        read, write = await self._exit_stack.enter_async_context(
            stdio_client(self._server_params())
        )
        self._session = await self._exit_stack.enter_async_context(ClientSession(read, write))
        await self._session.initialize()
        pages = await self.list_pages()
        page_text = "\n".join(getattr(block, "text", "") for block in pages)
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
            raise RuntimeError(
                "ChromeDevToolsClient used outside its 'async with' block — "
                "the underlying subprocess/session isn't running."
            )
        return self._session

    async def _call(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        session = self._require_session()
        result = await session.call_tool(tool_name, arguments)
        if result.is_error:
            raise RuntimeError(f"chrome-devtools-mcp tool '{tool_name}' failed: {result.content}")
        return result.content

    async def list_tool_schemas(self) -> dict[str, dict[str, Any]]:
        """Returns {tool_name: input_schema} for every tool the running
        server actually exposes — use this to verify argument names (e.g.
        click's element-reference parameter) against the real, currently
        installed version rather than trusting the convenience methods
        below blindly."""
        session = self._require_session()
        tools = await session.list_tools()
        return {tool.name: tool.input_schema for tool in tools.tools}

    # --- Navigation ---
    async def navigate_page(self, url: str) -> Any:
        return await self._call("navigate_page", self._page_args(type="url", url=url))

    async def new_page(self, url: str | None = None) -> Any:
        return await self._call("new_page", {"url": url} if url else {})

    async def list_pages(self) -> Any:
        return await self._call("list_pages", {})

    # --- Inspection ---
    async def take_snapshot(self) -> Any:
        """Structured, accessibility-aware element map with stable
        references — this is what the crawler reasons over, never raw
        pixels (Section 9/13)."""
        return await self._call("take_snapshot", self._page_args())

    async def take_screenshot(self) -> Any:
        return await self._call("take_screenshot", {})

    async def list_console_messages(self) -> Any:
        return await self._call("list_console_messages", {})

    async def list_network_requests(self) -> Any:
        return await self._call("list_network_requests", {})

    # --- Input automation ---
    async def click(self, element_ref: str) -> Any:
        return await self._call("click", self._page_args(uid=element_ref))

    async def fill(self, element_ref: str, value: str) -> Any:
        return await self._call("fill", self._page_args(uid=element_ref, value=value))

    async def authenticate(self) -> None:
        """Log in once, resolving the secret only inside this browser client."""
        if self._credential_ref is None or self._authenticated:
            return
        secret = await resolve_login(self._credential_ref)
        snapshot = await self.take_snapshot()
        text = "\n".join(getattr(block, "text", "") for block in snapshot)
        controls = re.findall(r'uid=(\S+)\s+(\S+)\s+"([^"]*)"', text)

        def find(names: list[str], roles: set[str]) -> str | None:
            wanted = {name.lower() for name in names if name}
            return next(
                (uid for uid, role, name in controls if role in roles and name.lower() in wanted), None
            )

        username = find(
            [secret.get("username_selector", ""), "email", "email address", "username"],
            {"textbox", "input"},
        )
        password = find([secret.get("password_selector", ""), "password"], {"textbox", "input"})
        submit = find(
            [secret.get("submit_selector", ""), "sign in", "login", "log in"],
            {"button", "link"},
        )
        if not username or not password or not submit:
            raise RuntimeError(
                "Could not identify login controls; configure username_selector, "
                "password_selector, and submit_selector for this credential reference."
            )
        await self.fill(username, secret["username"])
        await self.fill(password, secret["password"])
        # Supported CAPTCHA: a visible arithmetic prompt such as "What is 4 + 7?".
        math = re.search(r"\b(\d+)\s*([+\-*/x×])\s*(\d+)\b", text)
        captcha = find(
            [
                secret.get("captcha_selector", ""),
                "captcha",
                "captcha answer",
                "your answer",
                "answer",
            ],
            {"textbox", "input"},
        )
        if math and captcha:
            left, operator, right = math.groups()
            a, b = int(left), int(right)
            answer = {"+": a + b, "-": a - b, "*": a * b, "x": a * b, "×": a * b}.get(operator)
            if operator == "/" and b and a % b == 0:
                answer = a // b
            if answer is None:
                raise RuntimeError("Unsupported arithmetic CAPTCHA")
            await self.fill(captcha, str(answer))
        await self.click(submit)
        # Render can take several seconds to process login. Do not inspect one
        # immediate snapshot: it still contains the old login form.
        for _ in range(20):
            after = "\n".join(getattr(block, "text", "") for block in await self.take_snapshot())
            still_login = re.search(r'\b(?:password|email|username)\b', after, re.I)
            if not still_login:
                self._authenticated = True
                return
            messages = [
                line.strip()
                for line in after.splitlines()
                if "StaticText" in line
                and re.search(r"\b(?:invalid|incorrect|failed|error|does not match)\b", line, re.I)
            ]
            if messages:
                raise RuntimeError(f"Authentication did not complete: {messages[-1]}")
            await asyncio.sleep(0.5)
        raise RuntimeError("Authentication timed out; the login form remained visible")

    async def wait_until_ready(self) -> None:
        """Wait for two stable, non-loading accessibility snapshots."""
        previous = ""
        for _ in range(20):
            text = "\n".join(getattr(block, "text", "") for block in await self.take_snapshot())
            loading = re.search(r"\b(?:loading|please wait|starting service)\b", text, re.I)
            # A login page is not ready while its arithmetic CAPTCHA is still
            # loading. Once the prompt appears, it is safe to fill it.
            captcha_loading = "captcha" in text.lower() and not re.search(
                r"\b\d+\s*[+\-*/x×]\s*\d+\b", text
            )
            if text and text == previous and not (loading and captcha_loading):
                return
            previous = text
            await asyncio.sleep(0.5)
        raise RuntimeError("Application did not become ready before discovery")

    async def handle_dialog(self, action: str = "dismiss") -> Any:
        return await self._call("handle_dialog", {"action": action})

    async def wait_for(self, text: str | None = None, timeout_ms: int = 5000) -> Any:
        args: dict[str, Any] = {"timeout": timeout_ms}
        if text:
            args["text"] = text
        return await self._call("wait_for", args)
