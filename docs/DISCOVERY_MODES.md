# Application discovery modes

`discovery_mode: "targeted"` and `"full"` use the existing `ParallelCrawler`, ARQ job, checkpoint repository, and combined `app_flow_graph`. Legacy mode names remain accepted.

Both modes observe the configured entry, follow an observed login link/button or the configured login URL, and authenticate with the configured account. The post-login URL is observed, never constructed from a dashboard route. The bootstrap records an authentication edge between observed states.

Targeted discovery without `selected_modules` records entry/login and landing states and returns safe observed navigation candidates in `coverage.discovery_catalog.modules`. Submit their IDs as `selected_modules` to explore their branches. These IDs come from observations, not manually entered functionality names. Parent states remain in the combined graph. Repeated global navigation into unselected root branches is excluded.

Full discovery expands the same observed navigation using the existing shared priority queue, state expansion locks, task keys, and isolated browser workers. All results merge into the same graph. Destructive, financial, logout and form submission actions are skipped and recorded.

Authentication occurs once per run. Playwright attaches to each existing MCP Chrome process through loopback CDP to copy cookies, local storage, IndexedDB and session storage into separate contexts. Session material remains in memory, never in checkpoints. Resumed jobs establish a fresh authenticated session, then retry the saved frontier. Playwright 1.63 or later is required for importing storage into existing browser contexts.

`discovery_checkpoint.progress` exposes authentication, dashboard discovery, queue size, active workers, states, transitions, pending tasks, failed tasks and unsafe actions. The UI polls the existing map endpoint. Limits interrupt work with a partial map and retain the frontier. Start from Scratch continues to create a fresh map version; other runs merge observations into the existing map.

Verification: `tests/unit/agents/test_discovery_modes.py` covers modes, safety, arbitrary landing URLs, signatures and page-limit resume. Set `RUN_CHROME_SESSION_TEST=1` to run `tests/unit/tool_gateway/test_isolated_session_transfer.py` against installed Chrome MCP and a local HTTP fixture. No application credentials are needed for this test.
