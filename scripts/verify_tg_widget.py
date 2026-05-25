"""Быстрая проверка: открывает botisk.ru, кликает «Купить», проверяет
что Telegram Login Widget реально загрузился (есть iframe от oauth.telegram.org)."""
import asyncio
from playwright.async_api import async_playwright


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context()
        page = await ctx.new_page()

        tg_resources = []
        page.on('request', lambda r: tg_resources.append(r.url) if 'oauth.telegram.org' in r.url or 'telegram-widget' in r.url else None)
        errors = []
        page.on('pageerror', lambda e: errors.append(str(e)))

        await page.goto('https://botisk.ru/', wait_until='networkidle', timeout=30000)
        print(f"loaded: {await page.title()}")
        await page.wait_for_timeout(2000)

        # Кликнем «Купить» чтобы открылась модалка с виджетом
        btn = page.locator('button:has-text("Купить")').first
        await btn.scroll_into_view_if_needed()
        await btn.click()
        await page.wait_for_timeout(3000)

        # Проверим что есть iframe от oauth.telegram.org
        iframes = await page.locator('iframe').all()
        tg_iframe_count = 0
        for f in iframes:
            src = await f.get_attribute('src') or ''
            if 'oauth.telegram.org' in src or 'telegram.org' in src:
                tg_iframe_count += 1
                print(f"  ✓ TG iframe: {src[:100]}")

        # Виджет
        section_visible = await page.locator('#tg-auth-section').is_visible()
        widget_visible = await page.locator('#tg-login-widget').is_visible()
        print(f"\n=== Результаты ===")
        print(f"  Секция tg-auth-section видна: {section_visible}")
        print(f"  Контейнер tg-login-widget видим: {widget_visible}")
        print(f"  TG iframes на странице: {tg_iframe_count}")
        print(f"  Запросы к Telegram: {len(tg_resources)}")
        for r in tg_resources[:5]: print(f"    - {r[:120]}")
        if errors: print(f"  JS errors: {errors}")
        print()
        if tg_iframe_count > 0:
            print("🎉 ВИДЖЕТ РАБОТАЕТ! Юзеры теперь могут авторизоваться 1 кликом.")
        else:
            print("⚠️ Виджет не загрузился — возможно DNS ещё пропагирует или нужно подождать.")
        await browser.close()


asyncio.run(main())
