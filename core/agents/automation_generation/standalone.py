"""Standalone runner helpers; values are attached only to an authorized download."""
import json
import re
from pathlib import Path

from core.agents.automation_generation import templates
from core.agents.automation_generation.input_runtime import RUNTIME_SOURCE


def form_input_document(manifest, resolved, include_sensitive=False):
    """One representation for preview, folder materialization and ZIP export."""
    cases = {}
    for binding in manifest.get("input_bindings", []):
        key = str(binding.get("key") or "")
        if not key or binding.get("explicit_value") or str(binding.get("data_key") or "").startswith("credential:") or re.search(r"captcha|\botp\b", str(binding.get("field") or ""), re.I):
            continue
        answer = resolved.get(key)
        if binding.get("sensitive") and not include_sensitive:
            answer = {"mode": "value", "value": None, "source": "private-download" if answer else "missing"}
        else:
            answer = dict(answer) if answer is not None else {"mode": "value", "value": None, "source": "missing"}
        version_match = re.search(r":v(\d+):", key)
        version = int(version_match[1]) if version_match else 1
        code = str(binding.get("case_code") or "Unassigned case")
        case_key = code if code not in cases or cases[code]["version"] == version else f"{code} (v{version})"
        inputs = cases.setdefault(case_key, {"version": version, "inputs": {}})["inputs"]
        label = re.sub(r"\s*\*\s*$", "", str(binding.get("field") or "Input")).strip() or "Input"
        name, occurrence = label, 1
        while name in inputs:
            occurrence += 1
            step = binding.get("step_number") or (re.search(r":step(\d+)$", key) or [None, occurrence])[1]
            name = f"{label} (step {step})" if occurrence == 2 else f"{label} (step {step}, occurrence {occurrence})"
        inputs[name] = {"value": answer.get("value"), "mode": answer.get("mode", "value"),
                        "source": answer.get("source", "assigned"), "binding": key,
                        "allow_empty": bool(binding.get("allow_empty"))}
    return {"schema_version": 2, "test_cases": dict(sorted(cases.items()))}


def form_input_answers(document):
    return [answer for case in document.get("test_cases", {}).values() for answer in case.get("inputs", {}).values()]


def source_overrides(suite: Path, manifest: dict) -> dict[str, str]:
    result = {"data/execution-inputs.ts": RUNTIME_SOURCE}
    auth = suite / "fixtures" / "auth.ts"
    if auth.is_file():
        text = auth.read_text(encoding="utf-8")
        rendered = templates.render_auth(base_url=json.dumps(manifest.get("application_url") or ""))
        pattern = r"function credentialValue\(name: string\): string \{[\s\S]*?(?=function suiteBaseURL)"
        helper = re.search(pattern, rendered)
        if helper and re.search(pattern, text):
            text = re.sub(pattern, lambda _: helper.group(), text, count=1)
            text = text.replace('Object.entries(parsed).filter(([name, value]) => typeof name === "string" && typeof value === "string" && value)',
                                'Object.entries(parsed).filter((entry): entry is [string, string] => typeof entry[1] === "string" && entry[1].length > 0)')
            text = re.sub(
                r"function ocrCaptchaPng\(png: Buffer\): string \| null \{[\s\S]*?(?=async function captchaWidgetText)",
                'function ocrCaptchaPng(_png: Buffer): string | null {\n  return null;\n}\n\n',
                text,
                count=1,
            )
            text = text.replace('import { spawnSync } from "child_process";\n', '').replace('import os from "os";\n', '')
            result["fixtures/auth.ts"] = text
    return result


README = """# Standalone Playwright execution

This package runs without the QA platform, its API, or its database.

1. Run `npm install` and `npx playwright install chromium`.
2. Review `credentials/login-credentials.json`: accounts are assigned by spec
   path. Saved login values are included only when the credential download
   option is selected and export is enabled. Otherwise fill the empty template.
3. Review `credentials/test-data.json`: `test_cases` groups the saved values by
   test case, with readable input names such as Name, Subject, and Message.
   Edit each input's `value`; its `binding` maps it to the correct test step.
   Fill any unresolved `null` values before running.
4. Run `npx playwright test`.

Environment variables override local login settings. Within the QA platform,
managed execution inputs take precedence over these standalone files. Explicit
negative-test blanks stay blank. CAPTCHA/OTP must be supplied fresh at runtime;
they are not exported. The files are ignored by Git and contain private data.
CAPTCHA answers are never requested or exported. Configure the application
under test with its provider's official test mode or an owner-authorized staging
integration before running any login flow that requires a CAPTCHA.
Download again to include changed account or form-data assignments.
The Automation explorer's `credentials/test-data.json` entry previews current
assigned form values. Example files are templates. Use "Refresh inputs in
generated folder" to write current private JSON files for an IDE run; opening
VS Code also refreshes these files. Unsupplied values stay null.
Existing review reports describe the generation-time source. Regenerate automation
to refresh them, or run `npx tsc --noEmit` against this standalone package.
"""
