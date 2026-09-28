"""Regression tests for browser readiness detection."""

import pytest

from core.tool_gateway.mcp_clients.chrome_devtools_client import (
    ApplicationReadinessTimeoutError,
    ChromeDevToolsClient,
    SecurityVerificationRequiredError,
    _is_hosting_cold_start_page,
    _is_security_verification_page,
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
    client._page_id = 0
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


@pytest.mark.asyncio
async def test_login_fill_recovers_when_reactive_input_replaces_element(monkeypatch):
    client = ChromeDevToolsClient()
    attempted: list[tuple[str, str]] = []
    recovered: list[tuple[str, str]] = []

    async def fill(uid, value):
        attempted.append((uid, value))
        raise RuntimeError(
            "chrome-devtools-mcp tool 'fill' failed: element did not become interactive"
        )

    async def take_snapshot():
        return 'uid=2_7 textbox "Email"'

    async def set_control_value(uid, value):
        recovered.append((uid, value))

    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr(client, "fill", fill)
    monkeypatch.setattr(client, "take_snapshot", take_snapshot)
    monkeypatch.setattr(client, "_set_control_value", set_control_value)
    monkeypatch.setattr(
        "core.tool_gateway.mcp_clients.chrome_devtools_client.asyncio.sleep",
        no_sleep,
    )

    await client._fill_authentication_field(
        "1_5", "user@example.com", ["email"], {"textbox"}, "username field"
    )

    assert attempted == [("1_5", "user@example.com")]
    assert recovered == [("2_7", "user@example.com")]


@pytest.mark.asyncio
async def test_login_fill_does_not_hide_non_transient_errors(monkeypatch):
    client = ChromeDevToolsClient()

    async def fill(_uid, _value):
        raise RuntimeError("browser session closed")

    monkeypatch.setattr(client, "fill", fill)

    with pytest.raises(RuntimeError, match="browser session closed"):
        await client._fill_authentication_field(
            "1_5", "user@example.com", ["email"], {"textbox"}, "username field"
        )


RENDER_WAKE_PAGE = """
uid=9_0 RootWebArea "Render" url="https://sample.onrender.com/login"
uid=9_1 StaticText "INCOMING HTTP REQUEST DETECTED ..."
uid=9_2 StaticText "SERVICE WAKING UP ..."
uid=9_3 StaticText "ALLOCATING COMPUTE RESOURCES ..."
uid=9_4 link "Render Logo"
"""

LOGIN_PAGE = """
uid=10_0 RootWebArea "Login" url="https://sample.onrender.com/login"
uid=10_1 textbox "Email"
uid=10_2 textbox "Password"
uid=10_3 button "Log in"
"""

CLOUDFLARE_VERIFICATION_PAGE = """
uid=11_0 RootWebArea "Security verification" url="https://sample.test/login"
uid=11_1 heading "sample.test"
uid=11_2 StaticText "Performing security verification"
uid=11_3 StaticText "This website uses a security service to protect against malicious bots."
uid=11_4 checkbox "Verify you are human"
uid=11_5 StaticText "CLOUDFLARE"
"""


class ReadinessClient(ChromeDevToolsClient):
    def __init__(self, snapshots: list[str], *, timeout: float = 0.1) -> None:
        super().__init__(
            page_ready_timeout_seconds=timeout,
            cold_start_reload_interval_seconds=0.0001,
            ready_poll_interval_seconds=0.001,
        )
        self.snapshots = snapshots
        self.index = 0
        self.reloads: list[str] = []

    async def take_snapshot(self):
        value = self.snapshots[min(self.index, len(self.snapshots) - 1)]
        self.index += 1
        return value

    async def navigate_page(self, url: str):
        self.reloads.append(url)


def test_render_wake_page_is_recognized():
    assert _is_hosting_cold_start_page(RENDER_WAKE_PAGE)
    assert not _is_hosting_cold_start_page(LOGIN_PAGE)


def test_cloudflare_verification_page_is_recognized():
    assert _is_security_verification_page(CLOUDFLARE_VERIFICATION_PAGE)
    assert not _is_security_verification_page(LOGIN_PAGE)


@pytest.mark.asyncio
async def test_readiness_does_not_treat_security_checkbox_as_application_ui():
    client = ReadinessClient([CLOUDFLARE_VERIFICATION_PAGE], timeout=0.1)
    client._security_verification_timeout_seconds = 0.005

    with pytest.raises(SecurityVerificationRequiredError, match="did not click or bypass"):
        await client.wait_until_ready()


@pytest.mark.asyncio
async def test_readiness_continues_when_security_verification_clears():
    client = ReadinessClient(
        [CLOUDFLARE_VERIFICATION_PAGE, LOGIN_PAGE, LOGIN_PAGE], timeout=0.1
    )

    await client.wait_until_ready()


@pytest.mark.asyncio
async def test_readiness_waits_through_render_cold_start():
    client = ReadinessClient([RENDER_WAKE_PAGE, RENDER_WAKE_PAGE, LOGIN_PAGE, LOGIN_PAGE])

    await client.wait_until_ready()

    assert client.index >= 4
    assert client.reloads


@pytest.mark.asyncio
async def test_readiness_returns_specific_cold_start_timeout():
    client = ReadinessClient([RENDER_WAKE_PAGE], timeout=0.01)

    with pytest.raises(ApplicationReadinessTimeoutError, match="hosting cold-start page"):
        await client.wait_until_ready()
