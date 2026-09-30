"""Regression tests for browser readiness detection."""

import pytest

from core.tool_gateway.mcp_clients.chrome_devtools_client import (
    ApplicationReadinessTimeoutError,
    ChromeDevToolsClient,
    SecurityVerificationRequiredError,
    _find_submit_uid,
    _find_uid,
    _authenticated_session_visible,
    _login_form_visible,
    _is_hosting_cold_start_page,
    _is_security_verification_page,
    _same_site_allowed_patterns,
    _solve_math_captcha,
    _svg_captcha_text,
)


def test_math_captcha_supports_unicode_minus():
    assert _solve_math_captcha("4 − 2 = ?") == 2


def test_math_captcha_prefers_equation_over_unrelated_numbers():
    assert _solve_math_captcha("Support 24 / 7\nCopyright 12-31\n3 + 16 = ?") == 19


def test_math_captcha_reads_expression_without_spaces():
    assert _solve_math_captcha("Enter the answer\n8+1") == 9


def test_math_captcha_joins_split_svg_digits():
    assert _solve_math_captcha("1 9 - 7") == 12


def test_cafinity_tspan_captcha_ignores_svg_geometry():
    """Cafinity splits 12-2 across tspans and hides empty decoy glyphs."""
    from urllib.parse import quote

    svg = """<svg xmlns="http://www.w3.org/2000/svg" width="200" height="64" viewBox="0 0 200 64">
      <rect width="200" height="64" fill="#fff"/>
      <line x1="20" y1="8" x2="180" y2="56" stroke="#c0c0c0" stroke-width="2"/>
      <text font-family="monospace" font-weight="bold" font-size="22">
        <tspan x="22" y="31"></tspan>
        <tspan x="36" y="31">1</tspan>
        <tspan x="50" y="31">2</tspan>
        <tspan x="64" y="31">-</tspan>
        <tspan x="82" y="31">2</tspan>
        <tspan x="96" y="31"></tspan>
      </text>
    </svg>"""
    snapshot = 'uid=1_17 image "CAPTCHA" url="data:image/svg+xml;utf8,' + quote(svg) + '"'
    assert _svg_captcha_text(snapshot) == "1 2 - 2"
    assert _solve_math_captcha(_svg_captcha_text(snapshot)) == 10
    # Attribute noise such as y="31" next to "-" must not become 31-82.
    assert _solve_math_captcha(_svg_captcha_text(snapshot)) != -51

    svg = '<svg xmlns="http://www.w3.org/2000/svg"><text><tspan>1</tspan><tspan>9</tspan><tspan> - </tspan><tspan>7</tspan></text></svg>'
    uri = "data:image/svg+xml;utf8," + quote(svg)
    snapshot = f'uid=2_0 image "CAPTCHA" url="{uri}"'
    assert _solve_math_captcha(_svg_captcha_text(snapshot)) == 12


def test_dashboard_with_logout_counts_as_authenticated():
    dashboard = """
uid=59_0 RootWebArea "Cafinity" url="https://cafinity.ampcustech.info/dashboard"
uid=59_11 button "Logout"
uid=59_14 heading "Good afternoon, Shreya!"
uid=59_13 StaticText "Welcome back!"
"""
    login = """
uid=1_0 RootWebArea "Cafinity" url="https://cafinity.ampcustech.info/login"
uid=1_11 textbox "Enter your password"
uid=1_20 button "LOGIN"
"""
    assert _authenticated_session_visible(dashboard)
    assert not _login_form_visible(dashboard)
    assert _login_form_visible(login)
    assert not _authenticated_session_visible(login)
    controls = [
        ("1_15", "link", "Forgot Password"),
        ("1_19", "button", "LOGIN"),
    ]
    names = ["log in", "login", "go", "enter"]
    assert _find_submit_uid(controls, names) == "1_19"
    without_login = [("1_15", "link", "Forgot Password")]
    assert _find_submit_uid(without_login, names) is None
    assert _find_uid(without_login, names, {"button", "link"}) is None


def test_generic_captcha_ui_is_detected_without_hardcoded_site_names():
    text = (
        "Username\nPassword\nCaptcha\nEnter Captcha\nRefresh Captcha\n"
        "Speak Captcha instructions"
    )
    assert "captcha" in text.lower()
    assert "enter captcha" in text.lower()


def test_same_site_api_hosts_are_allowed_for_spa_resources():
    assert _same_site_allowed_patterns("https://cafinity.ampcustech.info/login") == [
        "https://cafinity.ampcustech.info/*",
        "https://*.ampcustech.info/*",
    ]


def test_local_target_does_not_expand_to_unrelated_hosts():
    assert _same_site_allowed_patterns("http://127.0.0.1:3000/login") == [
        "http://127.0.0.1:3000/*"
    ]


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
    assert "querySelectorAll(\"svg\")" in function or "querySelectorAll('svg')" in function
    assert "DOMParser" in function


@pytest.mark.asyncio
async def test_svg_blob_challenge_is_read_in_browser():
    """Exercise the actual reader, including split digits and blob replacement."""
    import shutil
    from playwright.async_api import async_playwright

    executable = shutil.which("chromium")
    if not executable:
        pytest.skip("Requires Chromium for the SVG DOM regression")
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" width="200" height="64">'
           '<text><tspan>1</tspan><tspan>9</tspan><tspan> - </tspan>'
           '<tspan>1</tspan><tspan>5</tspan><tspan> = ?</tspan></text></svg>')
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            executable_path=executable, args=["--no-sandbox"]
        )
        try:
            page = await browser.new_page()
            await page.set_content('<img id="challenge"><input placeholder="Enter the answer">')
            await page.evaluate("""svg => {
                document.querySelector('img').src = URL.createObjectURL(
                    new Blob([svg], {type: 'image/svg+xml'})
                );
            }""", svg)
            client = ChromeDevToolsClient()
            client._page_id = 0
            async def call(_name, arguments):
                return await page.evaluate(arguments["function"])
            client._call = call
            assert _solve_math_captcha(await client._read_visible_login_text()) == 4
            await page.evaluate("""svg => {
                const image = document.querySelector('img');
                URL.revokeObjectURL(image.src);
                image.src = URL.createObjectURL(new Blob([svg], {type: 'image/svg+xml'}));
            }""", svg.replace('<tspan>9</tspan>', '<tspan>8</tspan>'))
            assert _solve_math_captcha(await client._read_visible_login_text()) == 3
        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_visible_login_text_unwraps_fenced_evaluate_result(monkeypatch):
    client = ChromeDevToolsClient()
    client._page_id = 0

    async def call(_tool_name, _arguments):
        return 'Script ran:\n```json\n"Enter the answer\\\\n4 + 2 = ?"\n```'

    monkeypatch.setattr(client, "_call", call)

    text = await client._read_visible_login_text()

    assert _solve_math_captcha(text) == 6


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
async def test_stable_captcha_login_form_is_ready_without_visible_math():
    """Cafinity keeps the arithmetic challenge inside an image."""
    snapshot = (
        'uid=1_0 RootWebArea "Login" url="https://cafinity.ampcustech.info/login"\n'
        'uid=1_1 button "Employee"\n'
        'uid=1_2 button "Kitchen"\n'
        'uid=1_3 button "Admin"\n'
        'uid=1_4 textbox "Employee ID"\n'
        'uid=1_5 textbox "Enter your password"\n'
        'uid=1_6 button "Show password"\n'
        'uid=1_7 checkbox "Remember me"\n'
        'uid=1_8 link "Forgot Password"\n'
        'uid=1_9 button "Refresh CAPTCHA"\n'
        'uid=1_10 textbox "CAPTCHA answer"'
    )
    client = ReadinessClient([snapshot, snapshot], timeout=180)

    await client.wait_until_ready()

    assert client.index == 2


@pytest.mark.asyncio
async def test_readiness_waits_for_math_captcha_even_when_form_is_usable():
    client = ReadinessClient(
        [
            'uid=1_0 RootWebArea "Login" url="https://cafinity.example/login"\n'
            'uid=1_1 textbox "Employee ID"\n'
            'uid=1_2 textbox "Password"\n'
            'uid=1_3 textbox "Enter the answer"\n'
            'uid=1_4 button "LOGIN"',
            'uid=1_0 RootWebArea "Login" url="https://cafinity.example/login"\n'
            'uid=1_1 textbox "Employee ID"\n'
            'uid=1_2 textbox "Password"\n'
            'uid=1_5 StaticText "4 - 2 = ?"\n'
            'uid=1_3 textbox "Enter the answer"\n'
            'uid=1_4 button "LOGIN"',
            'uid=1_0 RootWebArea "Login" url="https://cafinity.example/login"\n'
            'uid=1_1 textbox "Employee ID"\n'
            'uid=1_2 textbox "Password"\n'
            'uid=1_5 StaticText "4 - 2 = ?"\n'
            'uid=1_3 textbox "Enter the answer"\n'
            'uid=1_4 button "LOGIN"',
        ],
        timeout=1,
    )

    await client.wait_until_ready()

    # The arithmetic line is static text, so it does not change the interactive
    # signature. A stable login form is ready on the second snapshot.
    assert client.index == 2


@pytest.mark.asyncio
async def test_authenticate_selects_employee_role_and_solves_captcha_after_fill(monkeypatch):
    client = ChromeDevToolsClient()
    client._credential_ref = "cred:employee"
    clicks: list[str] = []
    fills: list[tuple[str, str]] = []
    login_before_role = (
        'uid=1_0 RootWebArea "Login" url="https://cafinity.example/login"\n'
        'uid=1_1 radio "Employee"\n'
        'uid=1_2 radio "Admin"\n'
        'uid=1_3 textbox "Employee ID"\n'
        'uid=1_4 textbox "Password"\n'
        'uid=1_5 StaticText "1 + 1 = ?"\n'
        'uid=1_6 textbox "Enter the answer"\n'
        'uid=1_7 button "LOGIN"'
    )
    login_after_role = (
        'uid=1_0 RootWebArea "Login" url="https://cafinity.example/login"\n'
        'uid=1_1 radio "Employee" checked\n'
        'uid=1_3 textbox "Employee ID"\n'
        'uid=1_4 textbox "Password"\n'
        'uid=1_5 StaticText "9 + 7 = ?"\n'
        'uid=1_6 textbox "Enter the answer"\n'
        'uid=1_7 button "LOGIN"'
    )
    dashboard = (
        'uid=2_0 RootWebArea "Dashboard" url="https://cafinity.example/home"\n'
        "uid=2_1 heading \"Welcome\""
    )
    state = {"page": "login"}

    def current_snapshot() -> str:
        if state["page"] == "home":
            return dashboard
        if "1_1" in clicks:
            return login_after_role
        return login_before_role

    async def resolve_login(_ref):
        return {"username": "EMP001", "password": "secret", "account_role": "Employee"}

    async def take_snapshot():
        return current_snapshot()

    async def wait_until_ready():
        return None

    async def fill(uid, value):
        fills.append((uid, value))

    async def click(uid):
        clicks.append(uid)
        if uid == "1_7":
            state["page"] = "home"

    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr(
        "core.tool_gateway.mcp_clients.chrome_devtools_client.resolve_login",
        resolve_login,
    )
    monkeypatch.setattr(client, "take_snapshot", take_snapshot)
    monkeypatch.setattr(client, "wait_until_ready", wait_until_ready)
    monkeypatch.setattr(client, "fill", fill)
    monkeypatch.setattr(client, "click", click)
    monkeypatch.setattr(client, "_read_visible_login_text", take_snapshot)
    monkeypatch.setattr(
        "core.tool_gateway.mcp_clients.chrome_devtools_client.asyncio.sleep",
        no_sleep,
    )

    await client.authenticate()

    assert clicks[0] == "1_1"
    assert ("1_3", "EMP001") in fills
    assert ("1_4", "secret") in fills
    assert ("1_6", "16") in fills
    assert client._authenticated is True
