# Настройка оплаты для лендинга VideoAI

## Схема работы

```
Лендинг (botisk.ru / index.html)
    |
    +-- [Купить] --> POST yukassa-create-payment
    |                   |
    |                   +-- Создает платеж в ЮKassa API
    |                   +-- Обновляет web_orders (payment_id, tariff_code, amount_rub)
    |                   +-- Возвращает confirmation_url -> редирект
    |
    +-- [Возврат после оплаты] --> POST yukassa-check-payment
    |                                  |
    |                                  +-- Проверяет is_paid в web_orders
    |                                  +-- Возвращает {status, credits}
    |
    +-- ЮKassa callback --> POST yukassa-payment-callback
                                |
                                +-- source=web -> UPDATE web_orders SET is_paid='yes'
                                +-- source=bot -> пропускает (бот обработает сам)
```

## БД: photo_bot @ 72.56.96.64

Используется существующая таблица `web_orders` (та же что для генерации).
Добавлена колонка `email` для привязки веб-платежей.

Ключевые поля для оплаты:
- `payment_id` — ID платежа ЮKassa
- `tariff_code` — starter / pro / business
- `amount_rub` — сумма
- `is_paid` — 'no' / 'pending' / 'yes'
- `generations_limit` — сколько видео в пакете (10/50/200)
- `generations_left` — сколько осталось
- `paid_at` — дата оплаты
- `email` — email покупателя (NEW)

## Шаги настройки

### 1. SQL миграция — УЖЕ ВЫПОЛНЕНА

Колонка `email` добавлена, индекс создан.

### 2. Импорт workflows в n8n

1. Открыть https://n8n.24isk.ru/
2. Для каждого файла: Menu -> Import from File
3. В каждой ноде выбрать credentials из выпадающего списка:
   - PostgreSQL -> `ssh root@72.56.96.64`
   - HTTP Basic Auth (ЮKassa) -> `юкасса 23.10.2026`
4. Активировать все 3 workflow

### 3. Настроить callback в ЮKassa

В личном кабинете ЮKassa (https://yookassa.ru/my/payments):
- Настройки -> HTTP-уведомления
- URL: `https://n8n.24isk.ru/webhook/yukassa-payment-callback`
- События: `payment.succeeded`, `payment.canceled`

**Если у бота уже свой callback:** Можно направить оба на этот новый,
он различает по `metadata.source` (web vs bot).

### 4. Проверка

1. Открыть лендинг -> выбрать тариф -> ввести email
2. Редирект на ЮKassa
3. После оплаты (тестовый режим) -> возврат на лендинг
4. Кредиты зачислятся автоматически

## Файлы

| Файл | Назначение |
|------|-----------|
| `yukassa-create-payment.json` | Создание платежа (лендинг -> ЮKassa) |
| `yukassa-check-payment.json` | Проверка статуса (лендинг <- PostgreSQL) |
| `yukassa-webhook-callback.json` | Callback от ЮKassa -> обновление БД |
| `setup.sql` | SQL миграция (email колонка) |
