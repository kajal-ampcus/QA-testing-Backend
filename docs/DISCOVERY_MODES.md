# Application discovery modes

## Guided discovery

Send `discovery_mode: "guided"` to inspect only the entry page. Safe child
actions are saved as `available` jobs in `discovery_checkpoint.jobs`; the
browser job finishes instead of waiting for a person. The map remains
`PARTIAL` with `AWAITING_BRANCH_SELECTION` while choices remain. This is
navigation coverage, not evidence that functional assertions passed.

To inspect a saved branch, submit the same mode with
`resume_application_map_id` and `selected_branches: [job.key]`. Only those
paths are replayed; newly observed children remain available. Previously
unselected siblings survive reloads and later runs. Completed paths can also
be selected again. A new guided request without a map ID starts a fresh map.
Existing automatic discovery remains available.

The Discovery screen shows full path labels with checkboxes, an Explore
selected paths action, and a Review map and generate tests action. Tests
can be generated from the observed partial scope using the existing map
review and generation screens. Unvisited paths are not test coverage.

Choose the test account before starting the map. On an observed login page,
guided discovery offers an explicit sign-in branch when an account is
configured. Selecting it signs in and inspects the landing page; dashboard
children then become separate choices. Resume requires the original account
reference so branches from different personas are not silently combined.
Mutation/form-submission restrictions remain in effect; arbitrary input
prompts and sensitive-action approval are not part of guided navigation.

`discovery_mode: "targeted"` and `"full"` use the existing `ParallelCrawler`, ARQ job, checkpoint repository, and combined `app_flow_graph`. Legacy mode names remain accepted.

Both modes observe the configured entry, follow an observed login link/button or the configured login URL, and authenticate with the configured account. The post-login URL is observed, never constructed from a dashboard route. The bootstrap records an authentication edge between observed states.

Targeted discovery without `selected_modules` records entry/login and landing states and returns safe observed navigation candidates in `coverage.discovery_catalog.modules`. Submit their IDs as `selected_modules` to explore their branches. These IDs come from observations, not manually entered functionality names. Parent states remain in the combined graph. Repeated global navigation into unselected root branches is excluded.

Full discovery expands the same observed navigation using the existing shared priority queue, state expansion locks, task keys, and isolated browser workers. All results merge into the same graph. Destructive, financial, logout and form submission actions are skipped and recorded.

Authentication occurs once per run. Playwright attaches to each existing MCP Chrome process through loopback CDP to copy cookies, local storage, IndexedDB and session storage into separate contexts. Session material remains in memory, never in checkpoints. Resumed jobs establish a fresh authenticated session, then retry the saved frontier. Playwright 1.63 or later is required for importing storage into existing browser contexts.

`discovery_checkpoint.progress` exposes authentication, dashboard discovery, queue size, active workers, states, transitions, pending tasks, failed tasks and unsafe actions. The UI polls the existing map endpoint. Limits interrupt work with a partial map and retain the frontier. Start from Scratch continues to create a fresh map version; other runs merge observations into the existing map.

Verification: `tests/unit/agents/test_discovery_modes.py` covers modes, safety, arbitrary landing URLs, signatures and page-limit resume. Set `RUN_CHROME_SESSION_TEST=1` to run `tests/unit/tool_gateway/test_isolated_session_transfer.py` against installed Chrome MCP and a local HTTP fixture. No application credentials are needed for this test.
