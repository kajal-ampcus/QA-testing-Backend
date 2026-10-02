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
import { test, expect } from "../../fixtures/auth";
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

test([[ title ]], async ({ sessionPage: page }) => {
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

_AUTH = r"""
import fs from "fs";
import path from "path";
import { expect, test as base, type BrowserContext, type Page } from "@playwright/test";

/**
 * One browser window for the whole run.
 * Tests reuse this page instead of opening a new window each time.
 * Username and password are read from the suite .env at runtime.
 * This file does not contain credential values or credential references.
 */
function loadSuiteEnv(): void {
  const file = path.resolve(__dirname, "..", ".env");
  if (!fs.existsSync(file)) return;
  for (const line of fs.readFileSync(file, "utf8").split(/\r?\n/)) {
    const match = line.match(/^([A-Za-z_][A-Za-z0-9_]*)=(.*)$/);
    if (!match) continue;
    process.env[match[1]] = match[2].trim().replace(/^["']|["']$/g, "");
  }
}

function suiteBaseURL(): string {
  const fromEnv = process.env.BASE_URL?.trim() ?? "";
  const configured = [[ base_url ]];
  const chosen = /^https?:\/\//i.test(fromEnv) ? fromEnv : configured;
  if (!/^https?:\/\//i.test(chosen)) {
    throw new Error("Set BASE_URL in .env to the full site address, including https://");
  }
  return chosen;
}

function mathAnswer(text: string): string | null {
  const compact = text.replace(/\d(?:\s+\d)+/g, (item) => item.replace(/\s+/g, ""));
  const match = compact.match(/(\d+)\s*([+\-−–x×*/÷])\s*(\d+)/);
  if (!match) return null;
  const left = Number(match[1]);
  const right = Number(match[3]);
  const operator = match[2];
  if (operator === "+") return String(left + right);
  if (operator === "-" || operator === "−" || operator === "–") return String(left - right);
  if (operator === "x" || operator === "×" || operator === "*") return String(left * right);
  if ((operator === "/" || operator === "÷") && right && left % right === 0) return String(left / right);
  return null;
}

async function captchaAnswer(page: Page): Promise<string | null> {
  const expression = await page.evaluate(() => {
    const image = document.querySelector('img[alt="CAPTCHA"]');
    const src = image?.getAttribute("src") || "";
    const data = src.match(/^data:image\/svg\+xml[^,]*,(.*)$/i);
    if (!data) return "";
    const svg = /;base64/i.test(src) ? atob(data[1]) : decodeURIComponent(data[1]);
    const doc = new DOMParser().parseFromString(svg, "image/svg+xml");
    return [...doc.querySelectorAll("tspan")]
      .map((node, index) => ({
        x: node.hasAttribute("x") ? Number(node.getAttribute("x")) : index,
        text: (node.textContent || "").trim(),
      }))
      .filter((item) => item.text)
      .sort((a, b) => a.x - b.x)
      .map((item) => item.text)
      .join(" ");
  });
  return mathAnswer(expression);
}

async function loginNotice(page: Page): Promise<string> {
  const text = await page.locator("body").innerText().catch(() => "");
  return (
    text
      .split("\n")
      .map((item) => item.trim())
      .find((item) => /invalid|incorrect|captcha|too many|required/i.test(item)) || ""
  );
}

async function waitOutCaptchaLimit(page: Page): Promise<void> {
  const blocked = page.getByText(/too many captcha requests/i);
  if (!(await blocked.isVisible().catch(() => false))) return;
  await page.waitForFunction(() => false, undefined, { timeout: 65_000 }).catch(() => undefined);
  await page.reload({ waitUntil: "domcontentloaded" });
}

async function enterApplication(page: Page): Promise<void> {
  loadSuiteEnv();
  const username = process.env.TEST_USERNAME?.trim() ?? "";
  const password = process.env.TEST_PASSWORD?.trim() ?? "";
  if (!username || !password) {
    throw new Error("Set TEST_USERNAME and TEST_PASSWORD in the suite .env before running.");
  }
  await page.goto("/login", { waitUntil: "domcontentloaded" });
  await waitOutCaptchaLimit(page);
  const employee = page.getByRole("button", { name: /^employee$/i });
  if (await employee.isVisible().catch(() => false)) {
    await employee.click();
  }
  const identifier = page
    .getByPlaceholder(/employee id|email|username/i)
    .or(page.getByRole("textbox", { name: /employee id|email|username/i }))
    .first();
  await identifier.fill(username);
  const secret = page
    .getByPlaceholder(/password/i)
    .or(page.getByRole("textbox", { name: /password/i }))
    .first();
  await secret.fill(password);
  let notice = "";
  for (let attempt = 0; attempt < 3; attempt += 1) {
    notice = await loginNotice(page);
    if (/too many captcha requests/i.test(notice)) {
      await waitOutCaptchaLimit(page);
      continue;
    }
    await page.locator('img[alt="CAPTCHA"]').waitFor({ state: "visible", timeout: 15_000 });
    await page.waitForFunction(() => {
      const src = document.querySelector('img[alt="CAPTCHA"]')?.getAttribute("src") || "";
      return src.includes("data:image/svg+xml");
    }, undefined, { timeout: 15_000 });
    const answer = await captchaAnswer(page);
    if (!answer) {
      notice = (await loginNotice(page)) || "The math CAPTCHA was not readable.";
      break;
    }
    const answerBox = page
      .getByRole("textbox", { name: /answer/i })
      .or(page.getByPlaceholder(/answer/i))
      .first();
    await answerBox.fill(answer);
    await page.getByRole("button", { name: /^log\s*in$/i }).click();
    try {
      await page.waitForURL((url) => !/\/login\/?$/i.test(url.pathname), { timeout: 20_000 });
      // A reload before the session is stored sends the app back to the login page.
      await page.getByRole("button", { name: /^log\s*out$/i }).waitFor({ state: "visible", timeout: 20_000 });
      return;
    } catch {
      notice = await loginNotice(page);
      if (/invalid login credentials/i.test(notice)) break;
    }
  }
  throw new Error(notice || "Login stayed on the login page.");
}

function onLoginPage(url: string): boolean {
  if (!url.startsWith("http")) return false;
  return /\/login(?:\/|$)/i.test(new URL(url).pathname);
}

function keepSignedInSession(page: Page): void {
  const navigate = page.goto.bind(page);
  let signingIn = false;
  page.goto = async (url, options) => {
    const current = page.url();
    const base = current.startsWith("http") ? current : suiteBaseURL();
    const target = new URL(url, base);
    if (!signingIn && onLoginPage(current) && !onLoginPage(target.href)) {
      signingIn = true;
      try {
        await enterApplication(page);
      } finally {
        signingIn = false;
      }
      const next = `${target.pathname}${target.search}${target.hash}`;
      await page.evaluate((path) => {
        window.history.pushState({}, "", path);
        window.dispatchEvent(new PopStateEvent("popstate"));
      }, next);
      return null;
    }
    const signedIn = current.startsWith("http") && !onLoginPage(current);
    if (signedIn && target.origin === new URL(current).origin) {
      const next = `${target.pathname}${target.search}${target.hash}`;
      await page.evaluate((path) => {
        window.history.pushState({}, "", path);
        window.dispatchEvent(new PopStateEvent("popstate"));
      }, next);
      return null;
    }
    return navigate(url, options);
  };
}

export const test = base.extend<{}, { sessionContext: BrowserContext; sessionPage: Page }>({
  sessionContext: [
    async ({ browser }, use) => {
      const context = await browser.newContext({
        baseURL: suiteBaseURL(),
        viewport: null,
      });
      await context.addInitScript(() => {
        const paint = () => {
          const cursor = document.createElement("div");
          cursor.setAttribute("data-playwright-cursor", "true");
          cursor.style.position = "fixed";
          cursor.style.zIndex = "2147483647";
          cursor.style.width = "16px";
          cursor.style.height = "16px";
          cursor.style.marginLeft = "-8px";
          cursor.style.marginTop = "-8px";
          cursor.style.borderRadius = "50%";
          cursor.style.border = "2px solid #111";
          cursor.style.background = "rgba(220, 38, 38, 0.9)";
          cursor.style.pointerEvents = "none";
          document.documentElement.appendChild(cursor);
          const move = (event: MouseEvent) => {
            cursor.style.left = `${event.clientX}px`;
            cursor.style.top = `${event.clientY}px`;
          };
          document.addEventListener("mousemove", move, true);
          document.addEventListener("mousedown", () => {
            cursor.style.transform = "scale(0.75)";
          }, true);
          document.addEventListener("mouseup", () => {
            cursor.style.transform = "scale(1)";
          }, true);
        };
        if (document.readyState === "loading") {
          document.addEventListener("DOMContentLoaded", paint, { once: true });
        } else {
          paint();
        }
      });
      await use(context);
      await context.close();
    },
    { scope: "worker" },
  ],
  sessionPage: [
    async ({ sessionContext }, use) => {
      const page = await sessionContext.newPage();
      page.setDefaultTimeout(20_000);
      page.setDefaultNavigationTimeout(30_000);
      keepSignedInSession(page);
      page.on("load", () => {
        void page.mouse.move(480, 320).catch(() => undefined);
      });
      await enterApplication(page);
      await use(page);
    },
    { scope: "worker" },
  ],
});

test.beforeEach(async ({ sessionPage }) => {
  const signedIn = await sessionPage.getByRole("button", { name: "Logout", exact: true }).isVisible().catch(() => false);
  if (!signedIn) {
    await enterApplication(sessionPage);
  }
});

export function envCredential(name: "TEST_USERNAME" | "TEST_PASSWORD"): string {
  loadSuiteEnv();
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

_CONFIG = r"""
import fs from "fs";
import path from "path";
import { defineConfig } from "@playwright/test";

function loadSuiteEnv(): void {
  const file = path.resolve(__dirname, ".env");
  if (!fs.existsSync(file)) return;
  for (const line of fs.readFileSync(file, "utf8").split(/\r?\n/)) {
    const match = line.match(/^([A-Za-z_][A-Za-z0-9_]*)=(.*)$/);
    if (!match) continue;
    process.env[match[1]] = match[2].trim().replace(/^["']|["']$/g, "");
  }
}

loadSuiteEnv();

const fromEnv = process.env.BASE_URL?.trim() ?? "";
const configured = [% if base_url %][[ base_url ]][% else %]""[% endif %];
const baseURL = /^https?:\/\//i.test(fromEnv) ? fromEnv : configured;

export default defineConfig({
  testDir: "./tests",
  fullyParallel: false,
  workers: 1,
  forbidOnly: true,
  retries: 0,
  timeout: 150_000,
  expect: { timeout: 15_000 },
  use: {
    baseURL,
    headless: false,
    viewport: null,
    actionTimeout: 20_000,
    navigationTimeout: 30_000,
    launchOptions: {
      slowMo: 1400,
      args: ["--start-maximized"],
    },
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

The suite uses one Chromium window. Tests run one after another in that same window.

```bash
npm install
npx playwright install chromium
npx playwright test
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


def render_auth(**values: Any) -> str:
    return render(_AUTH, **values)


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
