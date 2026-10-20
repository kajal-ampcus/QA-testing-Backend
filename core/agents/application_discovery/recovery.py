"""Bounded read-only refresh of a known URL through the existing discovery gateway."""

import asyncio
import re
from urllib.parse import urljoin, urlsplit

from core.policy_safety.destructive_action_lexicon import classify_risk
from core.policy_safety.environment_policy import validate_discovery_target
from core.tool_gateway.gateway import ToolGateway
from core.tool_gateway.snapshot import parse_elements


async def rediscover_state(*, project_id, base_url, state, credential_ref=None, gateway=None):
    url = urljoin(base_url, state.url_pattern)
    base, target = urlsplit(base_url), urlsplit(url)
    if (base.scheme, base.netloc) != (target.scheme, target.netloc):
        raise ValueError("Recovery URL must stay within the project's origin.")
    await validate_discovery_target(url)
    async with asyncio.timeout(25):
        async with (gateway or ToolGateway()).chrome_devtools(
            "application_discovery", re.escape(f"{base.scheme}://{base.netloc}") + r"(?:/.*)?",
            credential_ref=credential_ref, project_id=project_id,
        ) as browser:
            if credential_ref:
                await browser.authenticate()
            await browser.navigate_page(url)
            nodes = parse_elements(await browser.take_snapshot())
    roots = [node for node in nodes if str(node.get("role", "")).lower() == "rootwebarea"]
    if not roots or not roots[0].get("url") or urlsplit(roots[0]["url"])._replace(fragment="") != target._replace(fragment=""):
        raise ValueError("Recovery reached a different or unverifiable page; saved state was not changed.")
    # Preserve codes only for unique identities. Never use ephemeral browser UIDs.
    def identity(item):
        return tuple(str(item.get(key) or "") for key in ("role", "name", "label", "placeholder"))
    old = {}
    for item in state.elements:
        old.setdefault(identity(item), []).append(item)
    observed = []
    for index, node in enumerate(nodes):
        if node in roots:
            continue
        item = {key: value for key, value in node.items() if key not in {"uid", "value"}}
        matches = old.get(identity(item), [])
        item["element_code"] = matches[0]["element_code"] if len(matches) == 1 else f"REC-{state.state_code}-{index}"
        # Retain policy classifications from matching evidence.
        risk = classify_risk(str(item.get("role") or ""), str(item.get("name") or "")).value
        item["risk"] = "DESTRUCTIVE" if risk == "DESTRUCTIVE" else matches[0].get("risk", risk) if len(matches) == 1 else risk
        item["source"] = "OBSERVED_DOM"
        observed.append(item)
    if not observed:
        raise ValueError("Recovery captured no controls.")
    return observed
