"""
Mobile pathwalk — эмуляция реального пользователя на iPhone 12.
Проходит полный путь: загрузка → генерация → попытка купить.
Делает скриншоты каждого ключевого экрана.

Запуск:
    python scripts/mobile_pathwalk.py
"""
import asyncio
from pathlib import Path
from playwright.async_api import async_playwright

SHOTS_DIR = Path(__file__).resolve().parent.parent / "docs" / "mobile_walkthrough"
SHOTS_DIR.mkdir(parents=True, exist_ok=True)


async def main():
    async with async_playwright() as p:
        # iPhone 12 viewport
        device = p.devices["iPhone 12"]
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(**device, locale="ru-RU")
        page = await ctx.new_page()

        events = []
        def log(stage, note=""):
            ts = asyncio.get_event_loop().time()
            events.append((stage, note, ts))
            print(f"  [{stage}] {note}")

        # Логируем сетевые запросы и ошибки
        page.on('pageerror', lambda e: log('JS_ERROR', str(e)[:200]))
        page.on('console', lambda m: log('CONSOLE', f"{m.type}: {m.text[:200]}") if m.type in ('error','warning') else None)

        # === STEP 1: открываем лендинг
        log('STEP_1', 'открываем botisk.ru')
        await page.goto('https://botisk.ru/', wait_until='networkidle', timeout=30000)
        await page.wait_for_timeout(2000)
        await page.screenshot(path=str(SHOTS_DIR / '01_hero.png'), full_page=False)
        log('SHOT', '01_hero.png — что видит юзер при заходе')

        # Полный скрин страницы
        await page.screenshot(path=str(SHOTS_DIR / '02_fullpage.png'), full_page=True)
        log('SHOT', '02_fullpage.png — вся страница для оценки длины')

        # === STEP 2: ищем кнопку «Создать видео» в hero
        log('STEP_2', 'ищем кнопку «Создать видео»/«Попробовать»')
        try:
            cta = page.locator('a[href="#generator"], a[href*="generator"], button:has-text("Создать"), button:has-text("Попробовать")').first
            await cta.scroll_into_view_if_needed()
            await cta.click()
            await page.wait_for_timeout(1500)
            await page.screenshot(path=str(SHOTS_DIR / '03_generator.png'), full_page=False)
            log('SHOT', '03_generator.png — секция генератора')
        except Exception as e:
            log('FAIL', f'клик на CTA: {e}')

        # === STEP 3: ищем upload-zone
        log('STEP_3', 'ищем upload-input')
        upload = page.locator('input[type="file"]').first
        if await upload.count() == 0:
            log('FAIL', 'input[type=file] не найден')
        else:
            # Загружаем тестовое фото
            test_img = Path(__file__).resolve().parent.parent / "og-image.png"
            if test_img.exists():
                await upload.set_input_files(str(test_img))
                await page.wait_for_timeout(2000)
                await page.screenshot(path=str(SHOTS_DIR / '04_uploaded.png'), full_page=False)
                log('SHOT', '04_uploaded.png — после загрузки фото')

                # Email
                email_input = page.locator('input[type="email"]').first
                if await email_input.count() > 0:
                    await email_input.fill('mobile.test@botisk.ru')
                    log('EMAIL', 'заполнили mobile.test@botisk.ru')

                # Жмём «Создать видео» / «Generate»
                gen_btn = page.locator('button:has-text("Создать"), #generate-btn').first
                try:
                    await gen_btn.click()
                    log('CLICK', 'нажали Создать видео — ждём генерацию')
                    # Не ждём полную генерацию (она ~60с) — просто скрин состояния
                    await page.wait_for_timeout(3000)
                    await page.screenshot(path=str(SHOTS_DIR / '05_generating.png'), full_page=False)
                    log('SHOT', '05_generating.png — после клика Создать')
                except Exception as e:
                    log('FAIL', f'клик Создать: {e}')

        # === STEP 4: scroll до тарифов и пробуем купить
        log('STEP_4', 'скроллим вниз к тарифам')
        await page.evaluate("document.querySelector('#pricing')?.scrollIntoView()")
        await page.wait_for_timeout(1500)
        await page.screenshot(path=str(SHOTS_DIR / '06_pricing.png'), full_page=False)
        log('SHOT', '06_pricing.png — секция тарифов на мобильном')

        # Кнопка «Купить за 99 ₽»
        try:
            buy_btn = page.locator('button:has-text("Купить")').first
            await buy_btn.scroll_into_view_if_needed()
            await buy_btn.click()
            await page.wait_for_timeout(2000)
            await page.screenshot(path=str(SHOTS_DIR / '07_payment_modal.png'), full_page=False)
            log('SHOT', '07_payment_modal.png — модалка оплаты на мобильном')

            # Проверим, виден ли TG-виджет
            tg_iframe = page.locator('iframe[src*="oauth.telegram"]')
            tg_visible = await tg_iframe.count() > 0
            log('TG_WIDGET', 'виден' if tg_visible else 'НЕ виден')
        except Exception as e:
            log('FAIL', f'клик Купить: {e}')

        # === STEP 5: метрика viewport
        viewport = page.viewport_size
        page_h = await page.evaluate("document.documentElement.scrollHeight")
        log('METRIC', f'viewport {viewport["width"]}x{viewport["height"]}, page height={page_h}px (= {page_h//viewport["height"]} экранов)')

        # Видна ли кнопка «Купить» без скролла после генерации
        try:
            buy_btn = page.locator('button:has-text("Купить")').first
            box = await buy_btn.bounding_box()
            if box:
                in_viewport = 0 <= box['y'] <= viewport['height']
                log('CTA_VISIBILITY', f'кнопка «Купить» y={box["y"]}, в viewport: {in_viewport}')
        except Exception:
            pass

        # === ВЫВОДЫ
        print()
        print("="*60)
        print("СОБЫТИЯ:")
        for st, note, _ in events:
            print(f"  {st:18} {note}")

        await browser.close()


asyncio.run(main())
