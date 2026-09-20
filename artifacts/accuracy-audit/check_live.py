import asyncio
import json
from pathlib import Path
from urllib.parse import urljoin

from playwright.async_api import async_playwright

OUT = Path(__file__).parent
ORIGIN = "https://practicetestautomation.com"


async def main():
    results = {"pages": [], "login_checks": []}
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, channel="chrome")
        page = await browser.new_page(viewport={"width": 1440, "height": 1000})
        paths = ["/", "/practice-test-login/", "/practice/", "/courses/", "/blog/",
                 "/contact/", "/privacy-policy/", "/practice-test-exceptions/", "/practice-test-table/"]
        for path in paths:
            try:
                response = await page.goto(urljoin(ORIGIN, path), wait_until="domcontentloaded", timeout=45000)
                await page.wait_for_timeout(1000)
                result = {"requested": path, "url": page.url, "status": response.status,
                          "title": await page.title(), "snapshot": await page.locator("body").aria_snapshot(),
                          "links": await page.locator("a[href]").evaluate_all(
                              "els => els.map(e => ({text:e.innerText, href:e.href}))"),
                          "inputs": await page.locator("input,textarea,select").evaluate_all(
                              "els => els.map(e => ({id:e.id,type:e.type,name:e.name,required:e.required,"
                              "visible:!!(e.offsetWidth||e.offsetHeight||e.getClientRects().length),"
                              "labels:[...(e.labels||[])].map(l=>l.innerText)}))")}
                if path == "/contact/":
                    result["thank_you_before_submission"] = await page.get_by_text("Thank you,", exact=False).all_text_contents()
                    result["thank_you_visible_before_submission"] = await page.get_by_text("Thank you,", exact=False).first.is_visible()
                    # Read client validation constraints; do not send the site's owner a contact message.
                await page.screenshot(path=str(OUT / (path.strip("/") or "home")) + ".png", full_page=True)
                results["pages"].append(result)
                print("PAGE", path, response.status, page.url, flush=True)
            except Exception as exc:
                results["pages"].append({"requested": path, "error": str(exc)})
                print("PAGE ERROR", path, type(exc).__name__, flush=True)
            (OUT / "live_evidence.json").write_text(json.dumps(results, indent=2), encoding="utf-8")

        for name, username, password in [
            ("valid", "student", "Password123"),
            ("invalid_username", "incorrectUser", "Password123"),
            ("invalid_password", "student", "incorrectPassword"),
            ("empty_both", "", ""),
            ("empty_username", "", "Password123"),
            ("empty_password", "student", ""),
        ]:
            await page.goto(ORIGIN + "/practice-test-login/", wait_until="domcontentloaded")
            await page.locator("#username").fill(username)
            await page.locator("#password").fill(password)
            await page.locator("#submit").click()
            if name == "valid":
                await page.wait_for_url("**/logged-in-successfully/", timeout=15000)
            else:
                await page.locator("#error").wait_for(state="visible")
            result = {"scenario": name, "url": page.url,
                      "snapshot": await page.locator("body").aria_snapshot()}
            if name == "valid":
                result["logout_visible"] = await page.get_by_role("link", name="Log out", exact=True).is_visible()
                await page.get_by_role("link", name="Log out", exact=True).click()
                await page.wait_for_url("**/practice-test-login/")
                result["logout_destination"] = page.url
            else:
                result["error_text"] = await page.locator("#error").inner_text()
            results["login_checks"].append(result)
            (OUT / "live_evidence.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
            print("LOGIN", name, result.get("error_text", result["url"]), flush=True)
        await browser.close()


asyncio.run(main())
