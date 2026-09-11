"""
Worker task wrapping core/agents/application_discovery. For large applications
this is where the sharded/parallel crawl fan-out happens (architecture doc
Section 9's scaling subsection): one task instance per navigation shard, each
with its own isolated chrome-devtools-mcp + Chrome instance, merged via the
`fingerprint` key on completion. MVP scope is a single sequential crawl
(Section 34); sharding is Production scope (Section 35).

Phase 0 stub.
"""

# TODO (Phase 1): async def run_discovery(project_id, target, crawl_budget): ...
#   -> instantiate core.agents.application_discovery.agent.ApplicationDiscoveryAgent
#      via core.tool_gateway, execute, persist application_map via infra.db.repositories
