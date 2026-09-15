"""
Prompt templates for Application Discovery.

Deliberately empty of real prompts right now: the crawl loop
(core/agents/application_discovery/crawler.py) is fully deterministic — no
LLM call happens during exploration, because take_snapshot() already
returns structured accessibility data, not pixels, so there's no perception
problem for a model to solve turn-by-turn. Relevance scoring is keyword
overlap against the associated requirement's own domain_tags/description
(agent.py's _extract_keywords_from_requirement), not an LLM judgment.

This file exists as a placeholder for a genuine future need: if a later
refinement wants semantic (not just keyword-overlap) relevance scoring, or
wants an LLM to name/summarize a discovered state for the "Agent Activity"
UI feed (Section 27) rather than showing raw role/name data, the prompt for
that would go here. Not needed for Milestone 2's acceptance criteria.
"""
