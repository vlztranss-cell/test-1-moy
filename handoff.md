# Claude Session State — handoff между Claude Code и Claude.ai

**Этот файл публичный** — https://botisk.ru/handoff.md
**НЕ КЛАСТЬ СЮДА:** пароли, токены, личные данные, SQL-секреты.

Сюда **можно** класть: текущий контекст задачи, что уже сделано, что нужно от браузерной Claude.

Как пользоваться:
1. **Claude Code** обновляет этот файл через `scripts/handoff.py write "..."` или Edit
2. **Вы** говорите Claude.ai в браузере: «прочитай свежий контекст: https://botisk.ru/handoff.md»
3. Claude.ai через WebFetch берёт актуальную версию (но имейте в виду — GitHub Pages кеширует до 10 мин, иногда нужен `?v=<timestamp>`)

---

## Last update
*(автоматически обновляется при push, см. last commit ниже)*

## Active task

**Перевыпуск YOUTUBE_REFRESH_TOKEN для канала VideoAI (НЕ Old Loft)**

### Текущее состояние
- ❌ Гипотеза «переключение активного канала меняет API» **НЕ ПОДТВЕРДИЛАСЬ**
- Тест: после переключения активного канала на VideoAI (галочка в Studio), `channels.list?mine=true` всё равно вернул **Old Loft**
- refresh_token зашит на канал который был активным при OAuth
- Старый токен стёрт из `.env`

### Что делать пользователю — в браузере, в этом порядке

**Шаг 1.** Открыть https://myaccount.google.com/permissions
- Найти разрешение **`botisk-youtube-uploader`** (или среди «Проектов без названия» — то у которого scopes `youtube.upload + youtube.readonly + yt-analytics.readonly`)
- Нажать **«Remove Access»** → подтвердить

**Шаг 2.** В YouTube Studio убедиться что активен канал **VideoAI · Оживляем фото** (галочка справа вверху)

**Шаг 3.** Запустить заново в PowerShell:
```
cd "C:\AI moy\test 1 moy"
& "C:\Users\Денис\AppData\Local\Programs\Python\Python312\python.exe" scripts\youtube_oauth_setup.py
```

**Шаг 4.** На странице Google **обязательно появится** «Выберите аккаунт» (это сделано через `prompt=consent select_account`)
- Должны быть оба варианта: **Old Loft** и **VideoAI · Оживляем фото**
- Выбрать **VideoAI** (handle @botisk-5577)
- Дальше — Advanced → Continue → разрешить 3 scopes

**Шаг 5.** Сказать Claude Code «готово» — он повторит `channels.list?mine=true`, должен вернуть **VideoAI**

### Что просить у Claude.ai в браузере (если нужна помощь)
- Конкретно: «помоги найти `botisk-youtube-uploader` в google permissions, проверь scope youtube.upload»
- Channel ID нужного: **UC6wfhx42PKNBW4ISk5Ai8ZA**
- Channel ID НЕнужного: UC17PhJ6_9r_SmhftCPqw_xA (Old Loft)

---

## Архивная история (последние 20)
*(старые состояния, можно прокручивать вниз)*
