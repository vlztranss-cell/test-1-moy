"""Скрин секции Workzilla в дашборде через headless Chrome."""
import asyncio, base64
from pathlib import Path
from playwright.async_api import async_playwright


async def main():
    import sys; sys.path.insert(0, str(Path(__file__).parent))
    from env_loader import load_env
    e = load_env()
    creds = base64.b64encode(f"admin:{e['DASHBOARD_PASSWORD']}".encode()).decode()

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(
            viewport={"width": 1400, "height": 900},
            extra_http_headers={"Authorization": "Basic " + creds},
        )
        page = await ctx.new_page()
        errors = []
        page.on('pageerror', lambda x: errors.append(str(x)[:200]))
        page.on('console', lambda m: errors.append(f"console.{m.type}: {m.text[:200]}") if m.type == 'error' else None)

        await page.goto("https://n8n.24isk.ru/op/", wait_until="domcontentloaded", timeout=30000)
        print(f"loaded: {page.url}")
        await page.wait_for_timeout(8000)
        # full page для общего вида
        full_path = Path(__file__).resolve().parent.parent / "docs" / "dash_full.png"
        await page.screenshot(path=str(full_path), full_page=True)
        print(f"  full page: {full_path}")

        # Скролл к секции Workzilla
        wz = page.locator("#z-workzilla")
        if await wz.count() > 0:
            await wz.scroll_into_view_if_needed()
            await page.wait_for_timeout(2000)
            shot_path = Path(__file__).resolve().parent.parent / "docs" / "dash_workzilla_section.png"
            shot_path.parent.mkdir(parents=True, exist_ok=True)
            await page.screenshot(path=str(shot_path), full_page=False)
            print(f"✓ скрин секции сохранён: {shot_path}")

            # Содержимое
            cards = await page.locator(".wz-card").count()
            print(f"  карточек в секции: {cards}")
            tabs = await page.locator(".wz-tab").all_inner_texts()
            print(f"  табы: {tabs}")
        else:
            print("✗ секция #z-workzilla не найдена в DOM")

        if errors:
            print("\nERRORS:")
            for e in errors[:10]: print(f"  {e}")

        await browser.close()


asyncio.run(main())
