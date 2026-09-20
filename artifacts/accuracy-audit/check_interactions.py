import asyncio
import json
import re
from pathlib import Path

from playwright.async_api import async_playwright

OUT = Path(__file__).parent
BASE = "https://practicetestautomation.com"


async def main():
    results = {}
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, channel="chrome")
        page = await browser.new_page()
        await page.goto(BASE + "/", wait_until="domcontentloaded")
        skip = page.get_by_role("link", name='Press "Enter" to skip to content', exact=True)
        results["skip_link_href"] = await skip.get_attribute("href")
        results["workshop_href"] = await page.get_by_role("link", name="AI Workshop", exact=True).first.get_attribute("href")
        await skip.focus()
        await skip.press("Enter")
        results["skip_link_destination"] = page.url

        await page.goto(BASE + "/practice-test-table/", wait_until="domcontentloaded")
        await page.wait_for_timeout(1500)
        rows = page.locator("tbody tr:visible")
        results["table_initial_rows"] = await rows.count()
        await page.get_by_role("radio", name="Java", exact=True).check()
        languages = await rows.locator('[data-col="language"]').all_text_contents()
        results["java_filter"] = {"rows": await rows.count(), "languages": languages,
                                  "passed": bool(languages) and all(v.strip() == "Java" for v in languages)}
        (OUT / "interaction_evidence.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
        reset = page.get_by_role("button", name=re.compile("Reset", re.I))
        results["reset_buttons"] = await reset.all_text_contents()
        if await reset.count() and await reset.first.is_visible():
            await reset.first.click()
        else:
            results["reset_not_observed"] = True
            await page.goto(BASE + "/practice-test-table/", wait_until="domcontentloaded")
            await page.wait_for_timeout(1500)
        results["reset_rows"] = await rows.count()
        await page.locator("#sortBy").select_option(label="Enrollments")
        values = await rows.locator('[data-col="enrollments"]').all_text_contents()
        numbers = [int(v.strip().replace(",", "")) for v in values]
        results["numeric_sort"] = {"values": numbers, "passed": bool(numbers) and numbers == sorted(numbers)}
        await page.goto(BASE + "/practice-test-exceptions/", wait_until="domcontentloaded")
        await page.get_by_role("button", name="Add", exact=True).click()
        await page.locator("#row2 input").wait_for(state="visible", timeout=15000)
        results["exceptions_add_row"] = {"row2_visible": await page.locator("#row2 input").is_visible()}
        await page.screenshot(path=str(OUT / "exceptions-after-add.png"), full_page=True)
        await browser.close()
    (OUT / "interaction_evidence.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(json.dumps(results, indent=2))


asyncio.run(main())
