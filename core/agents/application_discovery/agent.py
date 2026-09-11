"""
Agent 2 — Application Discovery Agent (architecture doc Section 6, 9).

Builds the Application Map (pages, states, elements) without source code,
purely through chrome-devtools-mcp browser interaction (navigate_page,
take_snapshot, click/fill, list_console_messages, list_network_requests,
handle_dialog). Every discovered element must carry a DOM/accessibility
source — nothing is invented.

For large applications, this agent runs as multiple parallel workers (one
per navigation shard) — see crawler.py and Section 9's scaling subsection.
MVP scope is a single sequential crawl (Section 34); sharding is Production
scope (Section 35).

Phase 0 stub.
"""

# TODO (Phase 1): class ApplicationDiscoveryAgent(BaseAgent): ...
#   uses core.tool_gateway.mcp_clients.chrome_devtools_client, never touches
#   chrome-devtools-mcp directly (docs/PROJECT_STRUCTURE.md point 4)
