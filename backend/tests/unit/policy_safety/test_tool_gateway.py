"""Agents without exploratory-browser permission cannot obtain MCP access."""

import pytest

from core.tool_gateway.gateway import ToolGateway


def test_only_exploratory_agents_can_get_chrome_devtools() -> None:
    gateway = ToolGateway()
    with pytest.raises(PermissionError):
        gateway.chrome_devtools("test_execution", "https://sample.test")
    assert gateway.chrome_devtools("application_discovery", "https://sample.test")
