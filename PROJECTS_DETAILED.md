# Подробное описание всех проектов

---

## 1. AI Photo2Video Bot (Оживление фото)

### Что это
Коммерческий Telegram-бот. Пользователь отправляет фото, бот оживляет его в видео с помощью нейросетей. Есть платная подписка через ЮКассу.

### Как работает (пошагово)
1. Пользователь пишет боту в Telegram, отправляет фото
2. Бот сохраняет фото на Yandex Disk
3. Отправляет фото в Hailuo/Minimax (основной) или Kling AI (резервный) для генерации видео
4. Ждёт результат (polling каждые N секунд)
5. Скачивает готовое видео, отправляет пользователю в Telegram
6. Данные о пользователе и заказе записываются в PostgreSQL

### Монетизация
- Оплата через ЮКассу (webhook callback)
- Есть пробный период (trial)
- Админ может выдавать доступ вручную (AdminGrant)

### Follow-up система
- Отдельный workflow по расписанию (cron) проверяет неактивных пользователей
- Отправляет им напоминания в Telegram
- Логика "Growth" — стимулирует вернуться

### Админ-панель
- Отдельный workflow собирает статистику из PostgreSQL
- Отправляет отчёт админу в Telegram

### Сервисы
| Сервис | Для чего | Credential в n8n |
|--------|----------|-----------------|
| Telegram Bot API | Общение с пользователем | `оживление фото` |
| Hailuo/Minimax | Генерация видео (основной) | Ключ в коде/env |
| Kling AI | Генерация видео (резервный) | Ключ в коде/env |
| PostgreSQL | БД пользователей, заказов, подписок | `ssh root@72.56.96.64` |
| ЮКасса | Приём платежей | `юкасса 23.10.2026` |
| Yandex Disk | Хранение фото/видео | Ключ в коде |

### Активные workflows
- **AI_Photo2Video_Bot_v6_1_5_Hailuo_primary_ready** — основной бот (136 нод!)
- **AI_Photo2Video_FollowUp_v4_Growth** — рассылки неактивным
- **AI_Photo2Video_Admin_Stats** — статистика для админа

### Неактивные версии (история развития)
- v3_PG → v6_0_2_AdminGrant → v6_1_1_PhotoVideoDemo → v6_1_5_WelcomeEditInPlace → **v6_1_5_Hailuo_primary_ready** (текущая)

---

## 2. iskPhotoAlive Bot

### Что это
Тестовый/альтернативный Telegram-бот для оживления фото. Использует AIMLAPI как прокси к Kling 2.1.

### Как работает
1. Пользователь отправляет фото боту @iskPhotoAlive_bot
2. Фото отправляется в AIMLAPI (Kling 2.1)
3. Бот ждёт результат
4. Возвращает видео пользователю

### Отличие от основного бота
- Без оплаты (тестовый)
- Без PostgreSQL — нет базы пользователей
- Проще (19 нод vs 136)
- Использует AIMLAPI вместо прямого Hailuo

### Сервисы
| Сервис | Credential |
|--------|-----------|
| Telegram Bot API | `iskPhotoAlive_bot` |
| AIMLAPI | Ключ в коде |
| Kling AI 2.1 | Через AIMLAPI |

---

## 3. Wildberries Cards (Карточки WB)

### Что это
Автоматизация выгрузки товаров на Wildberries. Полный цикл: от данных в Google Sheets до живой карточки с фото, ценами и остатками.

### Как работает (конвейер)

```
Google Sheets (данные о товаре)
    ↓
[WF_WB_CARD_CREATE] Создание карточки → WB API
    ↓
[WF_WB_CARD_CHECK] Проверка что карточка появилась (каждые 5 мин)
    ↓
[WF_WB_PHOTO_UPLOAD] Загрузка фото из Yandex Disk → WB
    ↓
[WF_WB_PRICES_STOCKS] Установка цен и остатков
    ↓
Карточка LIVE на Wildberries
```

### WF_WB_CARD_CREATE v3 (каждые 2 мин)
1. Читает из Google Sheets строки со статусом "ready"
2. Берёт batch по 5 штук
3. Ставит статус IN_PROGRESS (блокировка)
4. Для каждого товара: получает список файлов с Yandex Disk
5. Формирует JSON-запрос к WB Content API
6. Отправляет POST на `/content/v2/cards/upload`
7. При успехе → статус UPLOADING, при ошибке → статус ERROR

### WF_WB_CARD_CHECK (каждые 5 мин)
1. Читает SKU со статусом UPLOADING
2. Запрашивает WB API "Get All Cards"
3. Сопоставляет артикулы
4. Если карточка найдена → статус PUBLISHED + сохраняет nmID

### WF_WB_PHOTO_UPLOAD v3 (каждые 2 мин)
1. Берёт PUBLISHED карточки без фото
2. Получает список фото с Yandex Disk (публичная ссылка)
3. Получает URL размерной сетки
4. Отправляет все URL в WB API `/content/v3/media/save`
5. При успехе → статус ACTIVE

### WF_WB_PRICES_STOCKS (каждые 15 мин)
1. Читает ACTIVE SKU
2. Формирует payload с ценами
3. POST на WB Discounts API
4. Формирует payload с остатками
5. PUT на WB Marketplace API (по складу)
6. Статус → LIVE

### Вспомогательные workflows
- **Cache WB Dictionaries** — кэширует справочники WB (цвета, размеры, категории) в Google Sheets
- **WB_PHOTOS** — извлекает прямые ссылки из публичных папок Yandex Disk
- **WB → Yandex** — синхронизирует папки из Google Drive в Yandex Disk (для фото товаров)

### Сервисы
| Сервис | Для чего | Credential |
|--------|----------|-----------|
| Wildberries Content API | Создание карточек, загрузка фото | `валберис Контент, Цены...` |
| Wildberries Prices API | Установка цен | То же |
| Wildberries Marketplace API | Остатки по складам | То же |
| Google Sheets | Источник данных (товары, SKU) | `таблицы 23.10.2026` |
| Yandex Disk | Хранение фото товаров | `Yandex Disk OAuth` |

### Основная таблица
`10iqOyqoW3yS5U63qPmfMNv4YFcJwwd3b-USXckVqn3M` — вкладка с gid=567448099

---

## 4. Print-on-Demand (Принты)

### Что это
Автоматизированный бизнес по производству принтов для футболок/товаров. AI ищет тренды, генерирует дизайны, создаёт мокапы, проверяет качество.

### Конвейер

```
[WF1: Trend Scanner] Поиск трендов
    ↓
[WF2: Print Generator] Генерация принтов
    ↓
[WF3: Quality Control] Проверка качества (AI)
    ↓
[WF4: Mockup Generator] Создание мокапов
    ↓
[BACKFILL_PRINT_SCAN] Дополнительная генерация + мокапы (NanoBanana)
    ↓
→ Готово к загрузке на WB
```

### WF1: Trend Scanner (каждые 6 часов)
1. Читает настройки и список ниш из Google Sheets
2. Берёт топ-N ниш по приоритету
3. Для каждой ниши:
   - Запрашивает Google Trends (через SerpAPI)
   - Парсит Reddit Hot posts
   - Отправляет всё в GPT-4o для анализа
4. GPT возвращает JSON с трендами (ключевые слова, оценка потенциала)
5. Записывает найденные тренды в Google Sheets
6. Логирует ошибки/успехи

### WF2: Print Generator v4 (каждые 10 мин, ВЫКЛЮЧЕН)
1. Читает тренды со статусом "pending"
2. GPT-4o генерирует промпт для изображения
3. Роутер выбирает модель:
   - **Ideogram 3.0** — прозрачный фон (основной)
   - **Flux 1.1 Pro** (Replicate) + Remove Background
   - **GPT Image** (DALL-E)
4. Скачивает PNG
5. Загружает в Google Drive
6. Записывает в таблицу Prints
7. Обновляет тренд → статус "generated"

### WF3: Quality Control (каждые 15 мин)
1. Читает принты со статусом "pending_qc"
2. Техническая проверка (код): размер, формат, прозрачность
3. Если техпроверка пройдена → GPT-4o Vision оценивает:
   - Композиция
   - Читаемость
   - Коммерческий потенциал
4. Если оценка выше порога → статус "approved"
5. Если ниже → статус "rejected"

### WF4: Mockup Generator v9 (ВЫКЛЮЧЕН)
1. Берёт одобренные принты
2. Скачивает PNG из Google Drive
3. Накладывает на шаблоны мокапов (футболки разных цветов)
4. Использует NanoBanana API для наложения
5. Загружает готовые мокапы в Google Drive
6. Записывает ссылки в таблицу

### BACKFILL_PRINT_SCAN (ВЫКЛЮЧЕН, 107 нод!)
Массовая генерация принтов + мокапов через NanoBanana API:
- Читает строки из "фабрика" таблицы
- Генерирует принт (если нет)
- Создаёт 5 мокапов: man black, woman black, detail closeup и другие
- Каждый мокап — отдельный цикл: генерация → polling → скачивание → загрузка в Drive
- Записывает ссылки в столбцы H, J, K, L, M

### Сервисы
| Сервис | Для чего | Credential |
|--------|----------|-----------|
| SerpAPI | Google Trends данные | Ключ в URL |
| Reddit API | Hot posts | Публичный API |
| OpenAI GPT-4o | Анализ трендов, промпты, оценка | `OPENAI_CRED_ID` |
| GPT-4o Vision | Визуальная оценка качества | То же |
| Ideogram 3.0 | Генерация принтов (прозрачный фон) | Ключ в коде |
| Replicate (Flux 1.1) | Генерация принтов (альтернатива) | Ключ в коде |
| NanoBanana API | Создание мокапов | Ключ в коде |
| Google Drive | Хранение принтов и мокапов | `23.10.2026` |
| Google Sheets | Данные, очереди, логи | `таблицы 23.10.2026` |

### Основные таблицы
- `1dMa5qmEjx5TjkUSsVJrQHo40Tn_SBT06tjLvWCHXapo` — тренды, принты, логи
- `1GCYdYH3L8TZ0xO-wIp2MWxBa-_KoQ79Zo-9e7-YcuMI` — фабрика (BACKFILL)

---

## 5. SEO Generator (TeZilla v2)

### Что это
Автоматическая генерация SEO-текстов для карточек товаров на маркетплейсах.

### Как работает (каждую минуту)
1. Читает следующий SKU со статусом "pending" из Google Sheets
2. Если SKU есть → ставит IN_PROGRESS
3. Отправляет фото товара в GPT-4o Vision с промптом:
   - Сгенерировать название
   - Описание
   - Характеристики для WB
   - Ключевые слова
4. Парсит JSON-ответ
5. Если парсинг успешен → записывает SEO-данные обратно в таблицу
6. Если ошибка → записывает ERROR

### Сервисы
| Сервис | Для чего | Credential |
|--------|----------|-----------|
| OpenAI GPT-4o Vision | Генерация SEO по фото | `OPENAI_CRED_ID` |
| Google Sheets | Данные SKU, результаты | `таблицы 23.10.2026` |

### Таблица
`10iqOyqoW3yS5U63qPmfMNv4YFcJwwd3b-USXckVqn3M` — вкладка "SKU"

---

## 6. Arbitrage Bot (2D Trade)

### Что это
Telegram-бот для команды арбитража. Регистрирует закупки, отслеживает заказы из email, формирует поставки на WB.

### WF-ARB-2: Telegram Bot (АКТИВЕН)
1. Пользователь пишет боту @twoDtraid_bot
2. Switch по типу команды:
   - Регистрация новой закупки
   - Просмотр статистики
   - Другие команды
3. Данные записываются в Google Sheets

### WF-ARB-1: Email Trigger + WB Shipment (ВЫКЛЮЧЕН)
1. Читает email (IMAP Yandex) с подтверждениями заказов
2. Парсит данные из писем
3. Сверяет с базой в Google Sheets
4. Формирует поставку на WB через API
5. Уведомляет в Telegram

### WF-ARB-1-TEST (ВЫКЛЮЧЕН)
- Тестовая версия: только поиск и сверка (без реальной поставки)
- Использует Gmail OAuth вместо IMAP

### Сервисы
| Сервис | Для чего | Credential |
|--------|----------|-----------|
| Telegram Bot API | Бот @twoDtraid_bot | `twoDtraid_bot` |
| Google Sheets | База закупок | `таблицы 23.10.2026` |
| IMAP Yandex | Чтение email с заказами | `IMAP yandex` |
| Gmail | Альтернативный email | `Gmail 05/03/2026` |
| Wildberries API | Создание поставок | `2d WB 03.03.26` |

---

## 7. Прочие workflows

### полуавтоман контент фабрика принт (ВЫКЛЮЧЕН)
Telegram-бот для ручной загрузки принтов:
- Пользователь отправляет фото боту
- Бот загружает в Google Drive, делает публичным
- Записывает ссылку в таблицу "фабрика"
- Команда /start — приветствие

### AI Agent workflow (ВЫКЛЮЧЕН)
Тестовый AI-агент:
- Каждый день в 7:00
- Читает RSS: The Verge + BBC News
- GPT суммаризирует новости
- Отправляет email через Gmail

---

## Сводная таблица: ВСЕ сервисы

| # | Сервис | Используется в проектах | Тип ключа |
|---|--------|------------------------|-----------|
| 1 | Telegram Bot API | Photo2Video, iskPhotoAlive, Arbitrage | Bot token |
| 2 | Hailuo/Minimax | Photo2Video | API key |
| 3 | Kling AI | Photo2Video, iskPhotoAlive | API key |
| 4 | AIMLAPI | iskPhotoAlive | API key |
| 5 | PostgreSQL | Photo2Video | host/user/pass |
| 6 | ЮКасса | Photo2Video | shop_id + secret |
| 7 | Yandex Disk | WB Cards, Photo2Video | OAuth token |
| 8 | Wildberries API | WB Cards, Arbitrage | API key (header) |
| 9 | Google Sheets | ВСЕ проекты | OAuth2 client |
| 10 | Google Drive | Print-on-Demand, WB→Yandex | OAuth2 client |
| 11 | Gmail / IMAP | Arbitrage | OAuth2 / password |
| 12 | OpenAI GPT-4o | Print, SEO, Trends, QC | API key |
| 13 | Ideogram 3.0 | Print Generator | API key |
| 14 | Replicate (Flux) | Print Generator | API key |
| 15 | NanoBanana API | BACKFILL (мокапы) | API key |
| 16 | SerpAPI | Trend Scanner | API key |
| 17 | Reddit | Trend Scanner | Публичный |
| 18 | n8n внутренний | Cache WB, QC | Header token |

---

## Инфраструктура

| Ресурс | Адрес | Назначение |
|--------|-------|-----------|
| n8n сервер | https://n8n.24isk.ru/ | Все автоматизации |
| VPS | 72.56.96.64 (root) | PostgreSQL для бота |
| Google Sheets (WB) | `10iqOyqoW3yS5U63qPmfMNv4YFcJwwd3b` | SKU, карточки, SEO |
| Google Sheets (Prints) | `1dMa5qmEjx5TjkUSsVJrQHo40Tn_SBT06tjLvWCHXapo` | Тренды, принты |
| Google Sheets (Фабрика) | `1GCYdYH3L8TZ0xO-wIp2MWxBa-_KoQ79Zo` | BACKFILL принтов |
| Google Sheets (WB Cache) | `1iDkfD_I_zqTAnav-tv8joHwh1wZTvDimzrAwogyXC3g` | Справочники WB |
| GitHub | github.com/vlztranss-cell | Код, дашборд |
| Лендинг VideoAI | botisk.ru | Продающий сайт |
| Дашборд | botisk.ru/op-k7m3x9j2.html | Панель управления |
