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
  expectedState = "AUTHENTICATED";
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

  async guard(action: () => Promise<void>): Promise<void> {
    try {
      await action();
    } catch (error) {
      const heading = await this.page.getByRole("heading").first().textContent().catch(() => "");
      const extra = [
        `Expected starting state: ${this.expectedState}`,
        `Current URL: ${this.page.url()}`,
        `Visible heading: ${(heading || "").trim() || "(none)"}`,
      ].join("\\n");
      if (error instanceof Error) {
        error.message = `${error.message}\\n${extra}`;
        throw error;
      }
      throw error;
    }
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

test([[ title ]], async ({ [[ fixture ]]: page }) => {
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
import { spawnSync } from "child_process";
import fs from "fs";
import os from "os";
import path from "path";
import { expect, test as base, type Browser, type BrowserContext, type Page } from "@playwright/test";

function chooseDropdowns(page: Page): void {
  const names = ["locator", "getByRole", "getByLabel", "getByPlaceholder", "getByText", "getByTestId", "getByAltText", "getByTitle"] as const;
  for (const name of names) {
    const original = page[name].bind(page) as (...args: never[]) => {
      fill: (value: string, options?: { timeout?: number }) => Promise<void>;
      elementHandle: (options?: { timeout?: number }) => Promise<{ evaluate: (fn: (node: Element) => string) => Promise<string> } | null>;
      selectOption: (values: string | { label: string }) => Promise<string[]>;
    };
    (page as unknown as Record<string, (...args: never[]) => unknown>)[name] = (...args: never[]) => {
      const locator = original(...args);
      const fill = locator.fill.bind(locator);
      locator.fill = async (value: string, options?: { timeout?: number }) => {
        const handle = await locator.elementHandle({ timeout: options?.timeout }).catch(() => null);
        const tag = handle ? await handle.evaluate((node) => node.tagName).catch(() => "") : "";
        if (tag === "SELECT") {
          if (!value) return;
          try {
            await locator.selectOption({ label: value });
          } catch {
            await locator.selectOption(value);
          }
          return;
        }
        return fill(value, options);
      };
      return locator;
    };
  }
}

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
    const value = match[2].trim().replace(/^["']|["']$/g, "");
    // Discovery and Execution inject the account. A blank or older file must not replace it.
    if (!value || process.env[match[1]]?.trim()) continue;
    process.env[match[1]] = value;
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

const CAPTCHA_UI = new Set([
  "username",
  "password",
  "captcha",
  "login",
  "signin",
  "submit",
  "refresh",
  "speak",
  "remember",
  "home",
  "forgot",
  "instructions",
  "enter",
  "click",
  "icon",
  "generate",
  "show",
  "hide",
  "answer",
  "question",
  "math",
]);
const CAPTCHA_TOKEN = /^[A-Za-z0-9!@#$%^&*+=_?.-]{3,12}$/;

function mathAnswer(text: string): string | null {
  const compact = text.replace(/\d(?:\s+\d)+/g, (item) => item.replace(/\s+/g, ""));
  const pattern = /(\d{1,2})\s*([+\-−–x×*/÷])\s*(\d{1,2})/g;
  const matches = [...compact.matchAll(pattern)];
  matches.sort((left, right) => {
    const nearEquals = (match: RegExpMatchArray) =>
      compact.slice((match.index ?? 0) + match[0].length, (match.index ?? 0) + match[0].length + 8).includes("=")
        ? 1
        : 0;
    return nearEquals(right) - nearEquals(left);
  });
  for (const match of matches) {
    const left = Number(match[1]);
    const right = Number(match[3]);
    const operator = match[2];
    if (operator === "+") return String(left + right);
    if (operator === "-" || operator === "−" || operator === "–") return String(left - right);
    if (operator === "x" || operator === "×" || operator === "*") return String(left * right);
    if ((operator === "/" || operator === "÷") && right && left % right === 0) return String(left / right);
  }
  return null;
}

function textToken(widgetText: string): string | null {
  const text = widgetText.trim();
  if (!text || mathAnswer(text)) return null;
  const candidates: string[] = [];
  const pieces = text.split(/\s+/).filter(Boolean);
  if (pieces.length && pieces.every((piece) => piece.length === 1)) candidates.push(pieces.join(""));
  for (const line of text.split(/\n/)) {
    const parts = line.trim().split(/\s+/).filter(Boolean);
    if (!parts.length) continue;
    if (parts.length > 1) {
      if (parts.every((part) => part.length === 1)) candidates.push(parts.join(""));
      continue;
    }
    candidates.push(parts[0]);
  }
  for (const candidate of candidates) {
    if (CAPTCHA_UI.has(candidate.toLowerCase())) continue;
    if (CAPTCHA_TOKEN.test(candidate)) return candidate;
  }
  return null;
}

function ocrCaptchaPng(png: Buffer): string | null {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "captcha-"));
  const file = path.join(dir, "captcha.png");
  fs.writeFileSync(file, png);
  const python = process.env.PYTHON || "python";
  const result = spawnSync(python, ["-m", "core.agents.test_execution.captcha_solve", file], {
    encoding: "utf8",
    timeout: 20_000,
  });
  fs.rmSync(dir, { recursive: true, force: true });
  if (result.status !== 0) return null;
  const answer = (result.stdout || "").trim();
  return CAPTCHA_TOKEN.test(answer) ? answer : null;
}

async function captchaWidgetText(page: Page): Promise<string> {
  return page.evaluate(() => {
    const marked = [
      '[id*="captcha" i]',
      '[class*="captcha" i]',
      '[aria-label*="captcha" i]',
      'img[alt*="captcha" i]',
    ].join(", ");
    const glyphs: string[] = [];
    const readSvg = (svg: Element) => {
      const leaves = [...svg.querySelectorAll("tspan")];
      const nodes = leaves.length ? leaves : [...svg.querySelectorAll("text")];
      nodes
        .map((node, index) => ({
          x: node.hasAttribute("x") ? Number(node.getAttribute("x")) : index,
          text: (node.textContent || "").trim(),
        }))
        .filter((item) => item.text)
        .sort((a, b) => a.x - b.x)
        .forEach((item) => glyphs.push(item.text));
    };
    document.querySelectorAll(marked).forEach((node) => {
      if (node.tagName === "svg") readSvg(node);
      node.querySelectorAll("svg").forEach(readSvg);
    });
    const image = document.querySelector('img[alt*="captcha" i]');
    const src = image?.getAttribute("src") || "";
    const data = src.match(/^data:image\/svg\+xml[^,]*,(.*)$/i);
    if (data) {
      const svg = /;base64/i.test(src) ? atob(data[1]) : decodeURIComponent(data[1]);
      const doc = new DOMParser().parseFromString(svg, "image/svg+xml");
      readSvg(doc.documentElement);
    }
    const phrases: string[] = [];
    document.querySelectorAll(marked).forEach((node) => {
      if (node.tagName === "IMG" || node.tagName === "INPUT" || node.tagName === "BUTTON") return;
      const text = (node instanceof HTMLElement ? node.innerText : "").trim();
      if (text) phrases.push(text);
    });
    return [...glyphs, ...phrases].join("\n");
  });
}

async function captchaPng(page: Page): Promise<Buffer | null> {
  const dataUrl = await page.evaluate(() => {
    const image = document.querySelector(
      'img[alt*="captcha" i], img[id*="captcha" i], [id*="captcha" i] img, [class*="captcha" i] img',
    );
    const src = (image instanceof HTMLImageElement ? image.currentSrc || image.src : "") || "";
    if (src.startsWith("data:image/") && !src.toLowerCase().startsWith("data:image/svg")) return src;
    return "";
  });
  if (dataUrl.includes(",")) return Buffer.from(dataUrl.slice(dataUrl.indexOf(",") + 1), "base64");
  const picture = page
    .locator('img[alt*="captcha" i], img[id*="captcha" i], [id*="captcha" i] img, [class*="captcha" i] img, canvas')
    .first();
  if (!(await picture.isVisible().catch(() => false))) return null;
  return picture.screenshot().catch(() => null);
}

async function captchaAnswer(page: Page): Promise<string | null> {
  const widget = await captchaWidgetText(page);
  const widgetMath = mathAnswer(widget);
  if (widgetMath) return widgetMath;
  const png = await captchaPng(page);
  if (png) {
    const token = textToken(widget);
    if (token) return token;
    return ocrCaptchaPng(png);
  }
  const pageText = await page.locator("body").innerText().catch(() => "");
  return mathAnswer(pageText) || textToken(widget);
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

function sessionEndControl(page: Page) {
  const name = /^\s*(log\s*out|sign\s*out)\s*$/i;
  return page.getByRole("button", { name }).or(page.getByRole("link", { name }));
}

async function enterApplication(page: Page): Promise<void> {
  loadSuiteEnv();
  const username = process.env.TEST_USERNAME?.trim() ?? "";
  const password = process.env.TEST_PASSWORD?.trim() ?? "";
  if (!username || !password) {
    throw new Error("The discovery login is not available. Choose or edit the account on Automation, then generate again.");
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
  const answerBox = page
    .getByPlaceholder(/captcha|answer/i)
    .or(page.getByRole("textbox", { name: /captcha|answer/i }))
    .or(page.locator('input[name*="captcha" i], input[id*="captcha" i]'))
    .first();
  const picture = page
    .locator('img[alt*="captcha" i], img[id*="captcha" i], [id*="captcha" i] img, [class*="captcha" i] img')
    .first();
  for (let attempt = 0; attempt < 3; attempt += 1) {
    notice = await loginNotice(page);
    if (/too many captcha requests/i.test(notice)) {
      await waitOutCaptchaLimit(page);
      continue;
    }
    const needsCaptcha =
      (await answerBox.isVisible().catch(() => false)) ||
      (await picture.isVisible().catch(() => false));
    if (needsCaptcha) {
      await picture.waitFor({ state: "visible", timeout: 15_000 }).catch(() => undefined);
      const answer = await captchaAnswer(page);
      if (!answer) {
        notice = (await loginNotice(page)) || "The CAPTCHA could not be read.";
        const refresh = page.getByRole("button", { name: /refresh captcha|reload captcha|new captcha/i });
        if (await refresh.isEnabled().catch(() => false)) {
          await refresh.click();
          continue;
        }
        break;
      }
      await answerBox.fill(answer);
    }
    await page.getByRole("button", { name: /^log\s*in$/i }).click();
    try {
      await page.waitForURL((url) => !/\/login\/?$/i.test(url.pathname), { timeout: 20_000 });
      // A reload before the session is stored sends the app back to the login page.
      // CEP labels this control "Sign out"; other apps use "Log out".
      await sessionEndControl(page).first().waitFor({ state: "visible", timeout: 20_000 });
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

async function openSignedIn(page: Page, navigate: Page["goto"], target: URL, options?: Parameters<Page["goto"]>[1]) {
  const before = await page.evaluate(() => (document.body?.innerText ?? "").slice(0, 2000));
  const next = `${target.pathname}${target.search}${target.hash}`;
  await page.evaluate((path) => {
    window.history.pushState({}, "", path);
    window.dispatchEvent(new PopStateEvent("popstate"));
  }, next);
  // A client-side app swaps the screen. CEP keeps the old screen, so load the address.
  const rendered = await page
    .waitForFunction(
      (previous) => (document.body?.innerText ?? "").slice(0, 2000) !== previous,
      before,
      { timeout: 1_500 },
    )
    .then(() => true)
    .catch(() => false);
  if (rendered) return null;
  return navigate.call(page, target.href, options);
}

function keepSignedInSession(page: Page): void {
  const navigate = page.goto.bind(page);
  let signingIn = false;
  page.goto = async (url, options) => {
    const current = page.url();
    const base = current.startsWith("http") ? current : suiteBaseURL();
    const target = new URL(url, base);
    if (onLoginPage(target.href)) {
      return navigate(url, options);
    }
    if (!signingIn && onLoginPage(current)) {
      signingIn = true;
      try {
        await enterApplication(page);
      } finally {
        signingIn = false;
      }
    }
    const signedIn = page.url().startsWith("http") && !onLoginPage(page.url());
    if (signedIn && target.origin === new URL(page.url()).origin) {
      return openSignedIn(page, navigate, target, options);
    }
    return navigate(url, options);
  };
}

async function openContext(
  browser: Browser,
  signedIn: boolean,
): Promise<{ context: BrowserContext; page: Page }> {
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
  const page = await context.newPage();
  chooseDropdowns(page);
  page.setDefaultTimeout(20_000);
  page.setDefaultNavigationTimeout(30_000);
  page.on("load", () => {
    void page.mouse.move(480, 320).catch(() => undefined);
  });
  if (signedIn) {
    keepSignedInSession(page);
    await enterApplication(page);
  }
  return { context, page };
}

export const test = base.extend<{ publicPage: Page; sessionPage: Page }>({
  publicPage: async ({ browser }, use) => {
    const opened = await openContext(browser, false);
    await use(opened.page);
    await opened.context.close();
  },
  sessionPage: async ({ browser }, use) => {
    const opened = await openContext(browser, true);
    await use(opened.page);
    await opened.context.close();
  },
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
    const value = match[2].trim().replace(/^["']|["']$/g, "");
    // Discovery and Execution inject the account. A blank or older file must not replace it.
    if (!value || process.env[match[1]]?.trim()) continue;
    process.env[match[1]] = value;
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

The platform writes `.env` from the discovery account when the suite is generated. Do not commit `.env`, and do not paste passwords into the TypeScript files. Edit the account on the Automation step if a different login is needed.

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

The suite runs tests one after another. Each test opens its own browser context, so a public page does not inherit a signed-in URL, form, or CAPTCHA from the previous test.

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
TEST_CAPTCHA=
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
