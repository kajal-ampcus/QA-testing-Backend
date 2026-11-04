"""Read-only comparison of CEP feedback DOM and persisted discovery metadata."""
import asyncio
import json
import sys
from pathlib import Path

from sqlalchemy import text
from infra.db.session import AsyncSessionLocal, engine


async def main():
    report = {}
    try:
        async with AsyncSessionLocal() as session:
            await session.execute(text("SET TRANSACTION READ ONLY"))
            rows = (await session.execute(text("""
                SELECT m.id, m.project_id, p.name, m.version, m.status,
                       m.termination_reason, m.created_at, m.discovery_checkpoint
                FROM application_maps m JOIN projects p ON p.id=m.project_id
                WHERE m.base_url ILIKE '%cep.ampcustech.info%'
                ORDER BY m.created_at DESC LIMIT 5
            """))).mappings().all()
            maps = []
            for row in rows:
                entry = {k: str(v) if v is not None else None for k, v in row.items() if k != 'discovery_checkpoint'}
                states = (await session.execute(text("""
                    SELECT state_code, url_pattern, elements, reached_via, created_at
                    FROM application_map_states
                    WHERE application_map_id=:id AND url_pattern ILIKE '%feedback%'
                    ORDER BY created_at DESC
                """), {'id': row['id']})).mappings().all()
                allowed = {'element_code', 'role', 'name', 'input_type', 'required', 'validation',
                           'options', 'option_items', 'disabled', 'readonly', 'visible', 'css_selector',
                           'xpath', 'locator_scope', 'landmark', 'source', 'accept', 'min', 'max', 'maxlength'}
                entry['feedback_states'] = [{**{k: str(v) for k, v in state.items() if k not in {'elements', 'reached_via'}},
                    'elements': [{k: v for k, v in element.items() if k in allowed} for element in state['elements'] or []]}
                    for state in states]
                cp = row['discovery_checkpoint'] or {}
                entry['feedback_jobs'] = [{k: job.get(k) for k in ['key', 'status', 'observed_form', 'result_fingerprint']}
                    for context in [cp, *(cp.get('account_contexts') or {}).values()]
                    for job in context.get('jobs', []) if 'feedback' in str(job.get('path', [])).lower()]
                maps.append(entry)
            report['maps'] = maps
    except Exception as exc:
        report['database_error'] = type(exc).__name__
    finally:
        await engine.dispose()

    if '--database-only' in sys.argv:
        print(json.dumps(report, indent=2, default=str))
        return

    try:
        from playwright.async_api import async_playwright
        candidates = [r'C:\Program Files\Google\Chrome\Application\chrome.exe',
                      r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe']
        executable = next((p for p in candidates if Path(p).is_file()), None)
        async with async_playwright() as p:
            browser = await p.chromium.launch(executable_path=executable, headless=True)
            try:
                page = await browser.new_page()
                await page.goto('https://cep.ampcustech.info/en/feedback', wait_until='networkidle', timeout=60000)
                report['live'] = await page.evaluate('''() => ({url: location.href,
                    forms: [...document.forms].map(form => ({text: form.innerText,
                        controls: [...form.querySelectorAll('input, textarea, select, button, [role="radio"], [role="slider"]')].map(el => ({
                            tag: el.tagName, type: el.type, role: el.getAttribute('role'),
                            name: el.getAttribute('name'), label: [...(el.labels || [])].map(l => l.innerText).join(' '),
                            text: el.tagName === 'BUTTON' ? el.innerText : undefined,
                            placeholder: el.getAttribute('placeholder'), required: el.required,
                            accept: el.accept, min: el.min, max: el.max, maxlength: el.getAttribute('maxlength'),
                            visible: !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length)
                        }))}))})''')
            finally:
                await browser.close()
    except Exception as exc:
        report['live_error'] = type(exc).__name__
    target = Path('feedback-discovery-audit.json')
    target.write_text(json.dumps(report, indent=2, default=str), encoding='utf-8')
    print(json.dumps(report, indent=2, default=str))


if __name__ == '__main__':
    asyncio.run(main())
