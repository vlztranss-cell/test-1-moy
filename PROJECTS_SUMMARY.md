# Сводка по проектам n8n

Сервер: https://n8n.24isk.ru/
Всего workflows: 57 (13 активных, 20 архивных)

---

## 1. AI Photo2Video Bot (Оживление фото)

**Что делает:** Telegram-бот, который принимает фото от пользователя и создаёт из него видео с помощью AI (Hailuo/Minimax + Kling). Есть оплата через ЮКассу, хранение в PostgreSQL, follow-up рассылки, админ-статистика.

**Активная версия:** `AI_Photo2Video_Bot_v6_1_5_Hailuo_primary_ready`

| Сервис | Назначение |
|--------|-----------|
| Telegram Bot API | Приём фото, отправка видео пользователю |
| Hailuo/Minimax | Генерация видео (основной) |
| Kling AI | Генерация видео (резервный) |
| PostgreSQL | База данных пользователей, заказов, подписок |
| ЮКасса | Приём платежей |
| Yandex Disk | Хранение фото/видео |
| n8n Webhook | Callback от платёжных систем |

**Связанные workflows:**
- ✅ AI_Photo2Video_Bot_v6_1_5_Hailuo_primary_ready — основной бот
- ✅ AI_Photo2Video_FollowUp_v4_Growth — follow-up рассылки неактивным
- ✅ AI_Photo2Video_Admin_Stats — статистика для админа

---

## 2. iskPhotoAlive Bot

**Что делает:** Ещё один Telegram-бот для оживления фото, использует AIMLAPI + Kling 2.1. Вероятно, тестовый/альтернативный вариант.

| Сервис | Назначение |
|--------|-----------|
| Telegram Bot API | Бот @iskPhotoAlive_bot |
| AIMLAPI | Доступ к Kling 2.1 через API |
| Kling AI | Генерация видео |

**Workflows:**
- ✅ iskPhotoAlive_bot — AIMLAPI Kling 2.1 test

---

## 3. Wildberries Cards (Карточки WB)

**Что делает:** Автоматизация работы с Wildberries — создание карточек товаров, загрузка фото, установка цен и остатков. Данные берутся из Google Sheets, фото из Yandex Disk.

| Сервис | Назначение |
|--------|-----------|
| Wildberries API | Создание карточек, загрузка фото, цены, остатки |
| Google Sheets | Источник данных (товары, цены, размерные сетки) |
| Yandex Disk | Хранение фото товаров |

**Workflows:**
- ✅ WF_WB_CARD_CREATE v3 — создание карточек (batch)
- ✅ WF_WB_CARD_CHECK — проверка что карточка создалась
- ✅ WF_WB_PRICES_STOCKS — загрузка цен и остатков
- ✅ WF_WB_PHOTO_UPLOAD v3 — загрузка фото + размерная сетка
- ⏹ Cache WB Dictionaries — кэш справочников WB
- ⏹ WB_PHOTOS — извлечение ссылок из Yandex Disk
- ⏹ WB → Yandex — синхронизация папок

---

## 4. Print-on-Demand (Принты)

**Что делает:** Полный цикл для Print-on-Demand бизнеса: сканирование трендов → генерация принтов через GPT → создание мокапов → контроль качества.

| Сервис | Назначение |
|--------|-----------|
| OpenAI (GPT) | Анализ трендов, генерация идей принтов |
| Google Sheets | Хранение данных, очереди задач |
| Google Drive | Хранение сгенерированных принтов |

**Workflows:**
- ✅ WF1: Trend Scanner → Google Sheets — сканирование трендов
- ✅ WF3 — Quality Control — проверка качества принтов
- ⏹ WF2 — Print Generator v4 — генерация принтов (Smart Split)
- ⏹ WF4_Mockup_Generator_v9_AUTO_FIT — создание мокапов
- ⏹ WF_Fetch_Print_Area_Presets — пресеты зон печати
- ⏹ WF_Validate_Templates — валидация шаблонов

---

## 5. SEO Generator (TeZilla)

**Что делает:** Генерация SEO-текстов (названия, описания, характеристики) для карточек товаров на маркетплейсах с помощью GPT.

| Сервис | Назначение |
|--------|-----------|
| OpenAI (GPT) | Генерация SEO-текстов |
| Google Sheets | Источник товаров, результат записи |

**Workflows:**
- ✅ SEO Generator — TeZilla v2

---

## 6. Arbitrage Bot (Арбитраж / 2D Trade)

**Что делает:** Telegram-бот для регистрации закупок. Читает email с заказами, сверяет с базой, формирует поставки на WB.

| Сервис | Назначение |
|--------|-----------|
| Telegram Bot API | Бот @twoDtraid_bot — регистрация покупок |
| Google Sheets | База данных закупок |
| Gmail / IMAP Yandex | Чтение email с заказами |
| Wildberries API | Создание поставок |

**Workflows:**
- ✅ WF-ARB-2: Telegram Bot - Purchase Registration
- ⏹ WF-ARB-1: Email Trigger + WB Shipment
- ⏹ WF-ARB-1-TEST: Поиск и сверка (без поставки)

---

## 7. Dashboard (эта панель)

**Что делает:** API для панели управления всеми проектами.

- ✅ Dashboard API — Project Control Panel

---

## Все учётные данные (credentials)

| # | Тип | Имя в n8n | Сервис |
|---|-----|-----------|--------|
| 1 | telegramApi | оживление фото | Telegram-бот Photo2Video |
| 2 | telegramApi | iskPhotoAlive_bot | Telegram-бот iskPhotoAlive |
| 3 | telegramApi | twoDtraid_bot | Telegram-бот арбитража |
| 4 | httpBasicAuth | юкасса 23.10.2026 | ЮКасса (платежи) |
| 5 | postgres | ssh root@72.56.96.64 | PostgreSQL (сервер VPS) |
| 6 | googleSheetsOAuth2Api | таблицы 23.10.2026 | Google Sheets |
| 7 | googleDriveOAuth2Api | 23.10.2026 | Google Drive |
| 8 | gmailOAuth2 | Gmail 05/03/2026 | Gmail |
| 9 | imap | IMAP yandex | Yandex почта (IMAP) |
| 10 | httpHeaderAuth | валберис Контент, Цены... | Wildberries API |
| 11 | httpHeaderAuth | 2d WB 03.03.26 | Wildberries API (арбитраж) |
| 12 | httpHeaderAuth | Yandex Disk OAuth | Yandex Disk |
| 13 | httpHeaderAuth | N8N_CONTENT_READ | n8n внутренний (чтение) |
| 14 | httpHeaderAuth | N8N_CONTENT_RW | n8n внутренний (запись) |
| 15 | openAiApi | OPENAI_CRED_ID | OpenAI (GPT-4) |

---

## Инфраструктура

| Ресурс | Адрес/детали |
|--------|-------------|
| n8n сервер | https://n8n.24isk.ru/ |
| VPS (PostgreSQL) | root@72.56.96.64 |
| GitHub | https://github.com/vlztranss-cell |
| Дашборд | https://vlztranss-cell.github.io/test-1-moy/dashboard.html |
