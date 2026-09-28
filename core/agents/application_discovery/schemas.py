"""
Payload schema for Discovery — matches companion doc Part 2 #2. No LLM-facing
tool schema needed here, unlike Milestone 1's agent — the crawl itself is
deterministic (crawler.py), so there's nothing for an LLM to be prompted for
in this agent's core loop. See prompts.py for why that file is currently a
documented stub rather than empty by oversight.
"""

from pydantic import BaseModel, Field


class DiscoveryTarget(BaseModel):
    url: str
    environment_id: str | None = None
    credential_ref: str | None = None  # resolved only inside tool_gateway, never here (Section 20/28)


class DiscoveryCrawlBudgetPayload(BaseModel):
    max_pages: int = 150
    max_depth: int = 6
    max_duration_seconds: int = 900
    worker_limit: int = Field(default=3, ge=1, le=5)
    automatic_limits: bool = True


class DiscoveryScopePayload(BaseModel):
    """User-controlled crawl scope. Limits remain safety guards only."""

    mode: str = Field(
        default="entry_points",
        pattern="^(entry_points|auth_flow|modules|inventory|deep|complete)$",
    )
    selected_auth_flow: str | None = None
    selected_areas: list[str] = Field(default_factory=list)
    selected_modules: list[str] = Field(default_factory=list)


class DiscoveryPayload(BaseModel):
    target: DiscoveryTarget
    focus_requirements: list[str] = Field(
        default_factory=list, description="REQ-IDs (with or without @version) used to derive crawl keywords"
    )
    crawl_budget: DiscoveryCrawlBudgetPayload = Field(default_factory=DiscoveryCrawlBudgetPayload)
    discovery_scope: DiscoveryScopePayload = Field(default_factory=DiscoveryScopePayload)
    resume_application_map_id: str | None = None
    start_from_scratch: bool = False
