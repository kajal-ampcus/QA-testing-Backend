# Page Object Templates

Reusable Playwright Page Object Model templates/snippets, generated once per
application-map state and reused across every test case touching that state
(architecture doc Section 13). Not Python modules — template files (e.g.
Jinja2) consumed by `agent.py` during code generation.

Phase 0 — empty. Populated as Automation Generation produces real POM classes
for real discovered states (Phase 1+).
