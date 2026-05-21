# Phase 4: Интеграция реферальной программы в Telegram-бот

Дата: 21.05.2026

## Контекст

Бот `AI_Photo2Video_Bot_v6_1_5_Hailuo_primary_ready` (id `jy1vZlmAgELvecJ8`) —
**production workflow со 136 нодами**. Прямая модификация опасна.

**Подход:** backend готов в отдельном workflow `Bot_Referral_API`, который
предоставляет 3 HTTP endpoint'а. Боту остаётся вызвать их в 3 местах своего
flow. Это минимальное вторжение в большой workflow.

## Готовые endpoint'ы (бэкенд)

| URL | Метод | Body | Когда вызывать |
|---|---|---|---|
| `/webhook/bot-referral-save` | POST | `{tg_user_id, ref_code}` | На команде `/start ref_<CODE>` — записать что юзера привёл referrer |
| `/webhook/bot-referral-link` | POST | `{tg_user_id, email, tg_username?}` | На команде `/referral` — получить ref_code юзера + ссылки |
| `/webhook/bot-referral-bonus` | POST | `{order_id}` | После того как платёж в боте успешно обработан и `orders.is_paid='yes'` |

Все endpoint'ы идемпотентны.

## Что нужно добавить в бот

### Изменение 1: обработка `/start ref_<CODE>`

В n8n бот сейчас имеет Telegram trigger → switch/if по тексту сообщения.
Найти ветку, которая ловит `/start ...` команду.

**Добавить ноду между Telegram Trigger и существующим обработчиком /start:**

- **Code (JS):**
```javascript
const msg = $input.first().json.message || {};
const text = (msg.text || '').trim();
const m = text.match(/^\/start\s+ref_([A-Z0-9]{4,12})$/);
if (!m) {
    return [{json: {...$input.first().json, has_ref: false}}];
}
return [{json: {
    ...$input.first().json,
    has_ref: true,
    ref_code: m[1],
    tg_user_id: String(msg.from?.id || ''),
}}];
```

- **IF (has_ref === true):**
  - true → **HTTP Request:**
    ```
    Method: POST
    URL: http://172.17.0.1:5678/webhook/bot-referral-save
    Body (JSON): { "tg_user_id": "{{$json.tg_user_id}}", "ref_code": "{{$json.ref_code}}" }
    Timeout: 5000ms
    ```
    (172.17.0.1:5678 — обращение к самому себе через docker bridge; URL без https
    т.к. внутри docker сети)
  - после HTTP — продолжить обычный /start flow (приветствие)
  - false → сразу обычный /start flow

### Изменение 2: команда `/referral`

Найти switch по командам в боте (обычно switch by message.text начало).
Добавить ветку `/referral`.

**Узел Code (JS):**
```javascript
const msg = $input.first().json.message || {};
const tg_user_id = String(msg.from?.id || '');
const tg_username = msg.from?.username || '';
return [{json: { tg_user_id, tg_username }}];
```

**Postgres (executeQuery):** получить email юзера из orders (если уже платил)
```sql
SELECT email FROM orders WHERE user_telegram_id = '{{$json.tg_user_id}}'
AND email IS NOT NULL ORDER BY paid_at DESC NULLS LAST LIMIT 1;
```

> ⚠️ Если в `orders` нет колонки `email` — берите из своего `user_state.email`
> или другой колонки, где бот хранит email.

**IF: email есть?**
- false → Telegram message «Чтобы получить свою реф-ссылку, оплатите любой
  тариф (или укажите email командой `/email your@mail.com`)»
- true → **HTTP Request:**
  ```
  Method: POST
  URL: http://172.17.0.1:5678/webhook/bot-referral-link
  Body: {
    "tg_user_id": "{{$json.tg_user_id}}",
    "email": "{{$json.email}}",
    "tg_username": "{{$json.tg_username}}"
  }
  ```

**Telegram message** (после успешного HTTP):
```
🎁 *Реферальная программа*

Ваш реф-код: `{{$json.ref_code}}`

Поделитесь любой из ссылок ниже — за каждого оплатившего друга
получаете +10/30/80 кредитов (Старт/Про/Бизнес).

🤖 В Telegram: {{$json.bot_link}}
🌐 На сайте: {{$json.web_link}}

Уже привели платных: *{{$json.paid_referred || 0}}* · Заработано: *{{$json.bonus_earned || 0}}* кредитов
```

### Изменение 3: бонус при оплате в боте

Где-то в боте после ЮKassa callback есть нода, которая `UPDATE orders SET is_paid='yes'`.
После этой ноды добавить:

**Code (JS):** прокинуть `order_id` (это integer `orders.id`, не `order_id` text)
```javascript
const o = $input.first().json;
return [{json: { order_id: o.id }}];
```

**HTTP Request:**
```
Method: POST
URL: http://172.17.0.1:5678/webhook/bot-referral-bonus
Body: { "order_id": {{$json.order_id}} }
Timeout: 10000ms
```

Если у заказа не было `ref_by` или уже выплачен — endpoint вернёт `null` поля
(no-op). Бесопасно.

**Дополнительно — перенос ref_by из user_state в orders:**

При создании заказа в боте, после INSERT INTO orders, добавить:
```sql
UPDATE orders SET ref_by = (
    SELECT ref_by FROM user_state WHERE user_id = '{{$json.user_id}}'
)
WHERE id = {{$json.new_order_id}} AND ref_by IS NULL;
```

Без этого orders.ref_by всегда NULL, и `bot-referral-bonus` ничего не сделает.

## План работы по изменению бота (когда сядем)

1. **Бекап**: экспортировать workflow перед изменениями
   ```
   python scripts/export_bot_workflow.py
   ```
   (создать как одноразовый script если ещё не было)

2. **Открыть в n8n UI** https://n8n.24isk.ru/ → войти → workflow Photo2Video_Bot_v6_1_5

3. **Изменение 1** (/start ref_): в Telegram Trigger → найти switch /start → добавить
   3 ноды (Code → IF → HTTP)

4. **Изменение 2** (/referral): добавить новую ветку в switch команд → 4 ноды
   (Code → Postgres → IF → HTTP → Telegram)

5. **Изменение 3** (bonus): найти "is_paid='yes'" update → добавить 2 ноды
   (Code → HTTP)

6. **Test**: на тестовом TG юзере проверить:
   - `/start ref_TW5Z9Q` → user_state.ref_by записан
   - `/referral` → бот выдал ссылки
   - тестовая оплата → bonus_credits начислены

7. **Activate** (если не было) и наблюдать первые часы.

## Откат при проблемах

Если что-то сломается:
```bash
python scripts/import_bot_workflow.py n8n-workflows/AI_Photo2Video_Bot.exported.json
```
(import script тоже надо создать)

Или из n8n UI: workflow history → откатиться на предыдущую версию.

## Связано

- `n8n-workflows/migration_bot_referrals.sql` — DB-схема (user_state.ref_by, orders.ref_by, web_users.telegram_user_id, функция ensure_web_user_for_bot)
- `scripts/create_bot_referral_workflow.py` — helper-workflow Bot_Referral_API
- `n8n-workflows/AI_Photo2Video_Bot.exported.json` — бэкап бот-workflow (gitignored)
