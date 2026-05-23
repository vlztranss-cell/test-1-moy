"""
E2E-тест платёжной воронки на botisk.ru через реальный браузер Playwright.

Проверяет что Метрика-цели PAYMENT_OPEN и PAYMENT_REDIRECT действительно
отправляют pixel-запрос в mc.yandex.ru при клике пользователя.

НЕ совершает реальный платёж — останавливается на редиректе на yoomoney.ru.

Запуск:
    python scripts/e2e_payment_test.py
"""
from __future__ import annotations

import asyncio
import re
import sys
import time
from pathlib import Path

from playwright.async_api import async_playwright


async def main():
    metrika_hits = []   # все pixel-запросы к mc.yandex.ru

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context()
        page = await ctx.new_page()

        # Слушаем ВСЕ запросы к Метрика
        def on_request(req):
            url = req.url
            if 'mc.yandex.ru' in url or 'mc.yandex.com' in url:
                metrika_hits.append({'url': url[:150]})
                # Сокращённая запись с пометкой если есть goal://
                if 'goal' in url.lower():
                    import urllib.parse as up
                    decoded = up.unquote(url)
                    print(f"  🎯 GOAL: {decoded[:200]}")
                else:
                    print(f"  📍 metrika: {url[:120]}")

        page.on('request', on_request)
        page.on('console', lambda msg: print(f"  [console.{msg.type}] {msg.text[:200]}"))
        page.on('pageerror', lambda err: print(f"  [PAGE ERROR] {err}"))

        # ─── Шаг 1: открываем лендинг
        print("\n=== ШАГ 1: открываем botisk.ru ===")
        await page.goto('https://botisk.ru/', wait_until='networkidle', timeout=30000)
        print(f"  URL: {page.url}")
        print(f"  title: {await page.title()}")

        # Подождём чтобы Метрика инициализировалась
        await page.wait_for_timeout(2000)
        init_hits = len(metrika_hits)
        print(f"  metrika hits после загрузки: {init_hits}")

        # ─── Шаг 2: кликаем «Купить 290₽» (Старт)
        print("\n=== ШАГ 2: клик «Купить» — должна сработать PAYMENT_OPEN ===")
        # Находим кнопку
        btn = page.locator('button:has-text("Купить")').first
        await btn.scroll_into_view_if_needed()
        await btn.click()
        await page.wait_for_timeout(1500)
        after_open_hits = metrika_hits[init_hits:]
        print(f"  новых metrika-запросов: {len(after_open_hits)}")
        for h in after_open_hits:
            print(f"    {h['url']}")

        # Модалка должна открыться
        modal_visible = await page.locator('#payment-modal.active').is_visible()
        print(f"  модалка открыта: {modal_visible}")

        # ─── Шаг 3: вводим email, кликаем «Перейти к оплате»
        print("\n=== ШАГ 3: «Перейти к оплате» — должна сработать PAYMENT_REDIRECT ===")
        await page.locator('#payment-email').fill('e2e_test@botisk.ru')

        # Перехватываем redirect — НЕ хотим уходить на yoomoney
        async def block_yoomoney(route, request):
            if 'yoomoney' in request.url or 'yookassa' in request.url:
                print(f"  🚫 BLOCKED redirect to: {request.url[:80]}")
                await route.abort()
            else:
                await route.continue_()
        await page.route('**/*', block_yoomoney)

        before_redirect_hits = len(metrika_hits)
        await page.locator('#modal-pay-btn').click()

        # Ждём 2 секунды чтобы Метрика-pixel ушёл (наш фикс на 800ms callback)
        await page.wait_for_timeout(2500)

        after_redirect_hits = metrika_hits[before_redirect_hits:]
        print(f"  новых metrika-запросов после клика: {len(after_redirect_hits)}")
        for h in after_redirect_hits:
            print(f"    {h['url']}")

        # ─── Итог
        print(f"\n=== ИТОГ ===")
        print(f"всего pixel-запросов в Метрику: {len(metrika_hits)}")
        print(f"  - после загрузки страницы: {init_hits}")
        print(f"  - после клика «Купить»:   {len(after_open_hits)}  (ожидается +1 для PAYMENT_OPEN)")
        print(f"  - после клика «Оплатить»: {len(after_redirect_hits)}  (ожидается +1 для PAYMENT_REDIRECT)")

        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
