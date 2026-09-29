"""Opt-in real Chromium contract test against a local fixture, no application credentials."""
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from core.tool_gateway.mcp_clients.chrome_devtools_client import ChromeDevToolsClient, _snapshot_text


@pytest.mark.skipif(os.environ.get("RUN_CHROME_SESSION_TEST") != "1", reason="requires installed Chrome MCP")
@pytest.mark.asyncio
async def test_cookie_and_web_storage_transfer_between_isolated_browsers():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<html><body><h1>Workspace</h1></body></html>")

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("0.0.0.0", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_port}/workspace"
    try:
        async with ChromeDevToolsClient(allowed_url_pattern=url) as source:
            await source.navigate_page(url)
            await source._call("evaluate_script", source._page_args(function="() => { localStorage.setItem('test-local', 'local-value'); sessionStorage.setItem('test-session', 'session-value'); document.cookie = 'test-cookie=cookie-value; path=/'; }"))
            state = await source.export_authenticated_session()
        async with ChromeDevToolsClient(allowed_url_pattern=url) as target:
            await target.import_authenticated_session(state)
            await target.navigate_page(url)
            result = await target._call("evaluate_script", target._page_args(function="() => [localStorage.getItem('test-local'), sessionStorage.getItem('test-session'), document.cookie]"))
            text = _snapshot_text(result)
            assert "local-value" in text
            assert "session-value" in text
            assert "cookie-value" in text
    finally:
        server.shutdown()
        server.server_close()
