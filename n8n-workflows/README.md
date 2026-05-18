# Настройка оплаты для лендинга VideoAI

## Схема работы

```
Лендинг (video.html)
    │
    ├─ [Купить] → POST yukassa-create-payment
    │                 │
    │                 ├─ Создаёт платёж в ЮKassa API
    │                 ├─ Сохраняет в PostgreSQL (web_payments)
    │                 └─ Возвращает confirmation_url → редирект
    │
    ├─ [Возврат после оплаты] → POST yukassa-check-payment
    │                              │
    │                              ├─ Проверяет статус в PostgreSQL
    │                              └─ Возвращает {status, credits}
    │
    └─ ЮKassa сама шлёт callback → POST yukassa-payment-callback
                                       │
                                       ├─ source=web → UPDATE web_payments
                                       └─ source=bot → пропускает (бот обработает сам)
```

## Шаги настройки

### 1. Создать таблицу в PostgreSQL

```bash
ssh root@72.56.96.64
psql -U <user> -d <database> -f setup.sql
```

Или выполнить содержимое `setup.sql` через любой PostgreSQL клиент.

### 2. Импорт workflows в n8n

1. Открыть https://n8n.24isk.ru/
2. Для каждого файла:
   - Меню → Import from File
   - Выбрать JSON файл
   - **Заменить ID credentials** на реальные (см. ниже)
3. Активировать все 3 workflow

### 3. Credentials — что заменить

В каждом workflow нужно привязать credentials к реальным:

| Placeholder в JSON | Что подставить |
|---|---|
| `YUKASSA_CREDENTIAL_ID` | ID credential "юкасса 23.10.2026" (httpBasicAuth) |
| `POSTGRES_CREDENTIAL_ID` | ID credential "ssh root@72.56.96.64" (postgres) |

Проще всего: после импорта открыть каждую ноду и выбрать credential из выпадающего списка.

### 4. Настроить callback в ЮKassa

В личном кабинете ЮKassa (https://yookassa.ru/my/payments):
- Настройки → HTTP-уведомления
- URL: `https://n8n.24isk.ru/webhook/yukassa-payment-callback`
- События: `payment.succeeded`, `payment.canceled`

**Важно:** если у бота уже настроен свой callback — можно:
- Вариант А: направить оба (бот + веб) на этот новый callback, а из него маршрутизировать по `metadata.source`
- Вариант Б: в ЮKassa указать callback только для веб-платежей (по shop_id), а бот оставить со своим

### 5. Проверка

1. Открыть лендинг → выбрать тариф → ввести email
2. Должен редиректить на страницу оплаты ЮKassa
3. После оплаты (тестовый режим) — вернуться на лендинг
4. Кредиты должны зачислиться автоматически

## Файлы

| Файл | Что делает |
|------|-----------|
| `yukassa-create-payment.json` | Создание платежа (лендинг → ЮKassa) |
| `yukassa-check-payment.json` | Проверка статуса (лендинг ← PostgreSQL) |
| `yukassa-webhook-callback.json` | Callback от ЮKassa → обновление БД |
| `setup.sql` | SQL для создания таблицы web_payments |
