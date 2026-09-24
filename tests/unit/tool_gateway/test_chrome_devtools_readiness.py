"""Regression tests for browser readiness detection."""

import pytest

from core.tool_gateway.mcp_clients.chrome_devtools_client import (
    ChromeDevToolsClient,
    _solve_math_captcha,
)


def test_math_captcha_supports_unicode_minus():
    assert _solve_math_captcha("4 − 2 = ?") == 2


@pytest.mark.asyncio
async def test_ready_login_form_is_not_blocked_by_stale_loading_text(monkeypatch):
    client = ChromeDevToolsClient()
    snapshot = (
        'uid=1_0 RootWebArea "Login" url="https://cafinity.example/login"\n'
        'uid=1_1 StaticText "Loading"\n'
        'uid=1_2 radio "Employee" checked\n'
        'uid=1_3 textbox "Employee ID"\n'
        'uid=1_4 textbox "Password"\n'
        'uid=1_5 StaticText "4 - 2 = ?"\n'
        'uid=1_6 textbox "Enter the answer"\n'
        'uid=1_7 button "LOGIN"'
    )

    async def take_snapshot():
        return snapshot

    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr(client, "take_snapshot", take_snapshot)
    monkeypatch.setattr(
        "core.tool_gateway.mcp_clients.chrome_devtools_client.asyncio.sleep",
        no_sleep,
    )

    await client.wait_until_ready()


@pytest.mark.asyncio
async def test_visible_login_text_extracts_inline_svg_captcha(monkeypatch):
    client = ChromeDevToolsClient()
    captured: dict[str, object] = {}

    async def call(tool_name, arguments):
        captured["tool_name"] = tool_name
        captured["arguments"] = arguments
        return 'Employee ID\nPassword\n3 + 16 = ?\nEnter the answer'

    monkeypatch.setattr(client, "_call", call)

    text = await client._read_visible_login_text()

    assert _solve_math_captcha(text) == 19
    assert captured["tool_name"] == "evaluate_script"
    function = captured["arguments"]["function"]
    assert "data:image/svg+xml" in function
    assert "DOMParser" in function
