"""Jinja templates for the Playwright TypeScript suite.

Delimiters are [[ ]] so TypeScript braces stay literal. Values are inserted
only from the planner; templates do not invent selectors or assertions.
"""

from __future__ import annotations

from typing import Any

from jinja2 import Environment

_ENV = Environment(
    variable_start_string="[[",
    variable_end_string="]]",
    block_start_string="[%",
    block_end_string="%]",
    autoescape=False,
    keep_trailing_newline=True,
)

_PAGE = """\
import { expect, type Locator, type Page } from "@playwright/test";

/** Observed application-map state [[ state_code ]]. */
export class [[ class_name ]] {
  readonly page: Page;
[% for locator in locators %]
  readonly [[ locator.prop ]]: Locator;
[% endfor %]

  constructor(page: Page) {
    this.page = page;
[% for locator in locators %]
    this.[[ locator.prop ]] = page.[[ locator.expression ]];
[% endfor %]
  }

  async open() {
    await this.page.goto([[ url_literal ]]);
  }

  async waitForReady() {
    await this.page.waitForLoadState("domcontentloaded");
  }

  async expectOnPage() {
    await expect(this.page).toHaveURL([[ url_pattern ]]);
  }
[% for action in actions %]

  async [[ action.name ]]([[ action.params ]]) {
    [[ action.body ]]
  }
[% endfor %]
}
"""

_SPEC = """\
import { test, expect } from "@playwright/test";
[% for page in pages %]
import { [[ page.class_name ]] } from "../../pages/[[ page.file_name ]]";
[% endfor %]
[% if uses_data %]
import { testData } from "../../data/testdata";
[% endif %]

/**
 * Traceability
 * project_id: [[ trace.project_id ]]
 * requirement_id: [[ trace.requirement_id ]]
 * requirement_version: [[ trace.requirement_version ]]
 * test_case_id: [[ trace.test_case_id ]]
 * test_case_code: [[ trace.test_case_code ]]
 * test_case_version: [[ trace.test_case_version ]]
 * application_map_id: [[ trace.application_map_id ]]
 * application_map_version: [[ trace.application_map_version ]]
 * generation_id: [[ trace.generation_id ]]
 */
[% if destructive %]
test.skip(process.env.RUN_DESTRUCTIVE !== "true", "Destructive flow is skipped unless RUN_DESTRUCTIVE=true");
[% endif %]

test([[ title ]], async ({ page }) => {
[% for line in lines %]
  [[ line ]]
[% endfor %]
});
"""

_BLOCKED = """\
import { test } from "@playwright/test";

/**
 * Traceability
 * project_id: [[ trace.project_id ]]
 * requirement_id: [[ trace.requirement_id ]]
 * requirement_version: [[ trace.requirement_version ]]
 * test_case_id: [[ trace.test_case_id ]]
 * test_case_code: [[ trace.test_case_code ]]
 * test_case_version: [[ trace.test_case_version ]]
 * application_map_id: [[ trace.application_map_id ]]
 * application_map_version: [[ trace.application_map_version ]]
 * generation_id: [[ trace.generation_id ]]
 * status: BLOCKED
 */

// TODO: [[ reason ]]
// This approved case is blocked. No passing assertion is generated.
test.fixme([[ title ]], async () => {
  // Blocked: [[ reason ]]
});
"""

_AUTH = """\
import { expect, test as base } from "@playwright/test";

/**
 * Authentication hook.
 * Username and password are read from the environment at runtime.
 * This file does not contain credential values or credential references.
 */
export const test = base.extend({
  page: async ({ page }, use) => {
    await use(page);
  },
});

export function envCredential(name: "TEST_USERNAME" | "TEST_PASSWORD"): string {
  return process.env[name] ?? "";
}

export { expect };
"""

_TESTDATA = """\
/**
 * Inputs are supplied through the environment.
 * Do not put real credentials in this file.
 */
export const testData = {
[% for case in cases %]
  [[ case.code ]]: {
[% for field in case.fields %]
    [[ field.name ]]: process.env.[[ field.env ]] ?? "",
[% endfor %]
  },
[% endfor %]
} as const;
"""

_CONFIG = """\
import { defineConfig } from "@playwright/test";

const baseURL = process.env.BASE_URL[% if base_url %] ?? [[ base_url ]][% endif %];

export default defineConfig({
  testDir: "./tests",
  fullyParallel: false,
  forbidOnly: true,
  retries: 0,
  use: {
    baseURL,
  },
});
"""

_README = """\
# Generated Playwright suite

Generated and reviewed — not executed.

This suite was produced from approved test cases and the observed application
map. Selectors come only from that map. The generator did not run
`npx playwright test` against the application.

## Traceability

- Project: `[[ project_id ]]`
- Generation: `[[ generation_id ]]`
- Application map: `[[ map_id ]]` version [[ map_version ]]

Each spec names its requirement, test case, test-case version, and
application-map version in the file header. `manifest.json` repeats that
chain for the whole suite.

## Environment

Copy `.env.example` to `.env` and fill values locally. Do not commit `.env`.

- `BASE_URL` — application under test
- `TEST_USERNAME` and `TEST_PASSWORD` — account inputs, when a flow needs them
- `RUN_DESTRUCTIVE` — leave unset or `false`. Destructive specs stay skipped
  until this is exactly `true`

Per-case inputs are listed in `.env.example`. Their values are not stored in
the generated source.

## Checks already performed

Static review wrote `review-report.json`. When Node is available, the
platform also runs `tsc --noEmit` and `npx playwright test --list`. Those
commands do not execute the tests.

Install and list, without running the suite:

```bash
npm install
npx tsc --noEmit
npx playwright test --list
```
"""

_GITIGNORE = """\
node_modules/
test-results/
playwright-report/
blob-report/
playwright/.cache/
.env
*.auth.json
storageState.json
"""

_ENV_EXAMPLE = """\
# Fill locally. Do not commit real values.
BASE_URL=[[ base_url ]]
TEST_USERNAME=
TEST_PASSWORD=
RUN_DESTRUCTIVE=false
[% for name in names %]
[[ name ]]=
[% endfor %]
"""


def render(template: str, **values: Any) -> str:
    return _ENV.from_string(template).render(**values)


def render_page(**values: Any) -> str:
    return render(_PAGE, **values)


def render_spec(**values: Any) -> str:
    return render(_SPEC, **values)


def render_blocked(**values: Any) -> str:
    return render(_BLOCKED, **values)


def render_auth() -> str:
    return render(_AUTH)


def render_testdata(**values: Any) -> str:
    return render(_TESTDATA, **values)


def render_config(**values: Any) -> str:
    return render(_CONFIG, **values)


def render_readme(**values: Any) -> str:
    return render(_README, **values)


def render_gitignore() -> str:
    return render(_GITIGNORE)


def render_env_example(**values: Any) -> str:
    return render(_ENV_EXAMPLE, **values)
