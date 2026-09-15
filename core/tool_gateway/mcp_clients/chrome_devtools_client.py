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

import os
import re
from contextlib import AsyncExitStack
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


class ChromeDevToolsClient:
    def __init__(self, allowed_url_pattern: str | None = None, headless: bool = True) -> None:
        self._allowed_url_pattern = allowed_url_pattern
        self._headless = headless
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
        return await self._call("fill", {"uid": element_ref, "value": value})

    async def handle_dialog(self, action: str = "dismiss") -> Any:
        return await self._call("handle_dialog", {"action": action})

    async def wait_for(self, text: str | None = None, timeout_ms: int = 5000) -> Any:
        args: dict[str, Any] = {"timeout": timeout_ms}
        if text:
            args["text"] = text
        return await self._call("wait_for", args)
