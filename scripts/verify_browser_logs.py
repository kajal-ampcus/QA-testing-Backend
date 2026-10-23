"""Verify logs from the frontend tests/log-verification/capture.mjs browser run.

Run from the backend root: .venv/Scripts/python scripts/verify_browser_logs.py
"""
import json
from pathlib import Path

from core.agents.test_execution.playwright_runner import extract_trace_logs, parse_playwright_json

root = Path(__file__).resolve().parents[2] / 'QA-testing-Frontend/tests/log-verification/output'
console, network = extract_trace_logs(root / 'trace.zip')
console_events, network_events = json.loads(console), json.loads(network)
for marker, level in [('VERIFY_CONSOLE_INFO', 'log'), ('VERIFY_CONSOLE_WARNING', 'warning'), ('VERIFY_CONSOLE_ERROR', 'error')]:
    matches = [event for event in console_events if event.get('text') == marker]
    assert len(matches) == 1, f'Missing or duplicated console event: {marker}'
    assert matches[0]['messageType'] == level, matches[0]
    print(f'PASS console: {level}: {marker}')
for endpoint, status in [('/?log_verification=network_check', 200), ('/api/success', 200), ('/api/failure', 503)]:
    matches = [event for event in network_events if event.get('request', {}).get('url', '').endswith(endpoint)]
    assert len(matches) == 1, f'Missing or duplicated network request: {endpoint}'
    assert matches[0]['request']['method'] == 'GET'
    assert matches[0]['response']['status'] == status
    assert matches[0]['time'] >= 0
    print(f'PASS network: GET {endpoint} -> {status}, {matches[0]["time"]} ms')
status_messages = [event for event in console_events if event.get('text', '').startswith('VERIFY_NETWORK_STATUS=')]
assert len(status_messages) == 1
verification = next(event for event in network_events if 'log_verification=network_check' in event.get('request', {}).get('url', ''))
assert status_messages[0]['text'] == f"VERIFY_NETWORK_STATUS={verification['response']['status']}"
print('PASS console and network status match')
(root / 'console.json').write_bytes(console)
(root / 'network.json').write_bytes(network)
print('PASS: real browser console messages, severity, request URLs, methods and HTTP statuses verified.')

# Feed the real browser trace through the execution result parser as well.
report = {"suites": [{"specs": [{"file": "log-verification.spec.ts", "title": "Known console and network events", "tests": [{"results": [{"status": "passed", "attachments": [{"name": "trace", "path": str((root / "trace.zip").resolve())}]}]}]}]}]}
outcomes = parse_playwright_json(report, root, [])
assert len(outcomes) == 1
assert outcomes[0].console_log == console
assert outcomes[0].network_log == network
assert outcomes[0].trace == (root / "trace.zip").read_bytes()
print("PASS execution result parser preserves the captured console, network and trace evidence")
summary = {
    "result": "PASS",
    "scope": "Local Chrome capture, backend log extraction, execution result parsing",
    "deployed_execution_verified": False,
    "console_markers": ["VERIFY_CONSOLE_INFO", "VERIFY_CONSOLE_WARNING", "VERIFY_CONSOLE_ERROR"],
    "network_status_match": status_messages[0]["text"],
    "verification_request": {"method": verification["request"]["method"], "url": verification["request"]["url"], "status": verification["response"]["status"], "time_ms": verification["time"]},
}
(root / "verification-summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
