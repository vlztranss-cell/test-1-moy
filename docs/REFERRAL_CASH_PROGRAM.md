# Партнёрская программа (cash payouts) — для админа

Дата создания: 21.05.2026

## Что это

В отличие от регулярной реферальной программы (кредитами, для обычных клиентов),
**партнёрская программа выплачивает реальные деньги** арбитражникам / блогерам /
каналам, приводящим оплачивающий трафик.

- Публичный лендинг: https://botisk.ru/partners.html
- Apply форма → POST `/webhook/partner-apply` → таблица `partner_applications`
- Status: `pending` → ручная проверка → `approved` (генерируется `ref_code` партнёра)
- Cash-выплаты: ручные, через ТБанк, по запросу партнёра

## Юнит-экономика (Вариант B)

| Тариф | Цена | Себест. (75% used) | Наша маржа | Партнёр | Net для нас |
|---|---|---|---|---|---|
| Старт | 290 ₽ | 105 ₽ | 176 ₽ | **70 ₽** | **106 ₽** |
| Про | 790 ₽ | 525 ₽ | 241 ₽ | **150 ₽** | **91 ₽** |
| Бизнес | 2 490 ₽ | 2 100 ₽ | 315 ₽ | **250 ₽** | **65 ₽** |

Маржа партнёрской программы ниже обычной кредитной (которая ~0 ₽ операционных расходов).
Идея: партнёры приведут трафик который мы бы сами не нашли.

## DB-схема

- `partner_applications` — заявки + одобрённые партнёры (с `ref_code`, балансом, ставками)
- `partner_conversions` — каждая оплата по реф-ссылке партнёра (статус: pending → paid)
- `partner_payouts` — лог выплат (метод, сумма, чек самозанятого, дата)

## Поток одобрения

1. Партнёр отправляет форму на partners.html → `partner_applications` (status=pending)
2. Админ видит заявку в дашборде `/op/` → раздел «🤝 Заявки в партнёрку»
3. Админ проверяет источник трафика, оценивает реалистичность
4. Если ок:
   ```sql
   UPDATE partner_applications
   SET status='approved', ref_code='P-' || UPPER(SUBSTRING(MD5(id::text || NOW()::text), 1, 5)),
       approved_at=NOW(), admin_note='Одобрено: причина'
   WHERE id = <ID>;
   ```
5. Админ отправляет партнёру email с реф-ссылкой `botisk.ru/?ref=P-XXXXX`
6. Партнёр распространяет ссылку

## Поток конверсии (ручной MVP)

1. Юзер заходит по `?ref=P-XXXXX` → ref сохраняется в localStorage
2. Юзер платит → `web_orders.ref_by='P-XXXXX'`
3. В `yukassa-payment-callback`:
   - Сейчас: код пытается найти `web_users.ref_code=P-XXXXX` — не находит (партнёр не в web_users) — никаких начислений
   - **TODO (вторая итерация):** добавить ветку — если ref_code начинается с `P-` → искать в `partner_applications`, начислить в `partner_conversions`
4. Текущий MVP: админ раз в неделю/месяц вручную делает:
   ```sql
   -- Найти все оплаты по партнёрским реф-кодам за период
   SELECT wo.id, wo.email, wo.tariff_code, wo.ref_by, pa.payout_starter, pa.payout_pro, pa.payout_business
   FROM web_orders wo
   JOIN partner_applications pa ON pa.ref_code = wo.ref_by
   WHERE wo.is_paid = 'yes'
     AND wo.paid_at > NOW() - INTERVAL '7 days'
     AND wo.id NOT IN (SELECT web_order_id FROM partner_conversions WHERE web_order_id IS NOT NULL);

   -- Записать в partner_conversions
   INSERT INTO partner_conversions (partner_id, web_order_id, tariff_code, amount_rub, status)
   SELECT pa.id, wo.id, wo.tariff_code,
          CASE wo.tariff_code
              WHEN 'starter' THEN pa.payout_starter
              WHEN 'pro' THEN pa.payout_pro
              WHEN 'business' THEN pa.payout_business
              ELSE 0
          END,
          'pending'
   FROM web_orders wo
   JOIN partner_applications pa ON pa.ref_code = wo.ref_by
   WHERE wo.is_paid = 'yes' AND wo.paid_at > NOW() - INTERVAL '7 days';

   -- Обновить балансы партнёров (hold 7 дней — переводим pending → paid после)
   UPDATE partner_applications pa
   SET balance_rub = balance_rub + sub.total
   FROM (
       SELECT partner_id, SUM(amount_rub) AS total
       FROM partner_conversions
       WHERE status = 'pending' AND created_at < NOW() - INTERVAL '7 days'
       GROUP BY partner_id
   ) sub
   WHERE pa.id = sub.partner_id;

   UPDATE partner_conversions SET status='paid' WHERE status='pending' AND created_at < NOW() - INTERVAL '7 days';
   ```

## Выплаты

Когда партнёр запрашивает вывод (через email/TG):
1. Проверить `partner_applications.balance_rub >= 1000`
2. Перевести по реквизитам (карта/счёт ТБанк)
3. Записать в `partner_payouts`:
   ```sql
   INSERT INTO partner_payouts (partner_id, amount_rub, method, admin_note)
   VALUES (<ID>, <SUM>, 'card', 'Перевод на ТБанк *1234');

   UPDATE partner_applications
   SET balance_rub = balance_rub - <SUM>,
       total_paid_out = total_paid_out + <SUM>
   WHERE id = <ID>;
   ```
4. **Для самозанятых:** запросить чек через приложение «Мой налог» (партнёр обязан)
5. Сохранить ссылку на чек в `partner_payouts.receipt_url`

## Анти-фрод

- Дубль заявок по `LOWER(email)` блокируется (`ON CONFLICT DO UPDATE`)
- Партнёр **не может** использовать свой `ref_code` для самого себя (TODO: проверка в callback)
- Конверсия = paid_referred / clicks. Если < 0.5% — ручная проверка, возможна блокировка
- Hold 7 дней — защита от чарджбэков
- Партнёрские IP/UA логируются

## Phase 2 (когда понадобится)

- Авто-зачисление partner_conversions в callback (через расширение SQL)
- Личный кабинет партнёра — отдельный раздел `partners-cabinet.html` с auth по email + magic link
- Авто-вывод балансов через ЮKassa Payouts API
- Sub-партнёры (рекомендация партнёра партнёром) — 5% от баланса второго уровня

## Связано

- `n8n-workflows/migration_partners.sql` — DB-схема
- `scripts/create_partner_workflow.py` — n8n workflow Web_Partner_Apply
- `partners.html` — публичный лендинг
- Dashboard (https://n8n.24isk.ru/op/) — раздел «🤝 Заявки в партнёрку»
