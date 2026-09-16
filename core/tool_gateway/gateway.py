"""Least-privilege access to exploratory browser tools.

Only Discovery and Failure Analysis may obtain Chrome DevTools MCP. Scripted
test execution uses Playwright through a separate path in a later milestone.
"""

from typing import Any, Protocol

from core.tool_gateway.mcp_clients.chrome_devtools_client import ChromeDevToolsClient


class BrowserInspection(Protocol):
    async def navigate_page(self, url: str) -> Any: ...

    async def take_snapshot(self) -> Any: ...

    async def click(self, element_ref: str) -> Any: ...

    async def authenticate(self) -> None: ...

    async def wait_until_ready(self) -> None: ...


class ToolGateway:
    _CHROME_AGENTS = frozenset({"application_discovery", "failure_analysis"})

    def chrome_devtools(
        self, agent_name: str, allowed_url_pattern: str, credential_ref: str | None = None
    ) -> ChromeDevToolsClient:
        if agent_name not in self._CHROME_AGENTS:
            raise PermissionError(f"Agent {agent_name!r} has no Chrome DevTools access")
        return ChromeDevToolsClient(allowed_url_pattern=allowed_url_pattern, credential_ref=credential_ref)
