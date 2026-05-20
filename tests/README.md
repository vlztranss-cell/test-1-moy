# E2E-тесты VideoAI

Pytest-набор для проверки логики защиты кредитов, оплаты, генерации и
watermark'инга на работающем n8n + БД.

## Что покрывают

| Файл | Что тестирует |
|------|---------------|
| `test_validation.py` | HTTP 400 на невалидных email/image (5 кейсов) |
| `test_credits.py` | Free/paid списание, 402 при отсутствии кредитов, приоритет paid > free (5 кейсов) |
| `test_yukassa.py` | Callback зачисляет кредиты, идемпотентность, 3 тарифа, source=bot игнорируется (4 + 3 параметризованных) |
| `test_watermark.py` | Free → наш URL с watermark; paid → clean URL; раздача MP4 (3 кейса) |

## Запуск

```bash
# Зависимости (paramiko уже у вас локально)
pip install pytest requests

# Все тесты, verbose
python -m pytest tests/ -v

# Только один файл
python -m pytest tests/test_yukassa.py -v

# Один конкретный тест
python -m pytest tests/test_credits.py::test_drained_user_returns_402 -v
```

## Окружение

Тесты импортируют `scripts/ssh.py` и используют `vps_run` / `psql` для
прямой работы с БД на VPS (через SSH + sudo postgres). Из `.env` берётся
`VPS_HOST`, `VPS_USER`, `VPS_SSH_PASSWORD`.

Тесты бьются по живому стеку — нужно подключение к интернету и валидный
`.env`.

## Cleanup

Все тестовые юзеры создаются с email вида `e2e-{uuid8}@e2e.test`.
Fixture `test_email` автоматически удаляет строки из `web_users` и
`web_orders` после каждого теста.

При прерывании тестов (Ctrl+C) теневые юзеры могут остаться — почистить
вручную:

```sql
DELETE FROM web_orders WHERE email LIKE 'e2e-%@e2e.test';
DELETE FROM web_users  WHERE email LIKE 'e2e-%@e2e.test';
```

## Что НЕ покрывают

- Реальную PiAPI-генерацию (она стоит денег и занимает 30-60 сек)
- Реальный платёж через ЮKassa (sandbox можно добавить позже)
- Frontend — нет браузерного тестирования
- Безопасность (CSRF, SQL-injection — отдельная аудит-задача)

## CI (на будущее)

Можно прогонять в GitHub Actions, добавив `.env` через секреты:
- `VPS_SSH_PASSWORD`, `N8N_API_KEY` → repository secrets
- workflow generates `.env` from secrets, runs `pytest tests/`
- Это пока **не настроено** — нужно когда команда вырастет.
