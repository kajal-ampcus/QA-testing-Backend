"""
Wraps the official chrome-devtools-mcp server (Puppeteer/CDP-based, Chromium-
only) for the two exploratory use cases: Application Discovery and Failure
Analysis's reproduction step (architecture doc Section 10/11, companion doc
Part 3). Applies the recommended runtime flags on every connection:

--isolated                    fresh, auto-cleaned profile per Discovery run
--allowedUrlPattern            scoped to the target app's domain
--redactNetworkHeaders          strips auth tokens/cookies from network evidence
--autoConnect / --browser-url   for the SSO/MFA handoff case (Section 9)
--experimentalPageIdRouting     when running multiple concurrent Discovery shards

Never used for scripted Test Execution — that's playwright_client.py, always.

Phase 0 stub.
"""

# TODO (Phase 1): class ChromeDevToolsClient: async def navigate(...), take_snapshot(...), ...
