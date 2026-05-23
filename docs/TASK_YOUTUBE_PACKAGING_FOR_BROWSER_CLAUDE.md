# Задача для браузерной версии Claude — упаковка YouTube-канала VideoAI

> Скопируй этот файл целиком в чат claude.ai и работай по нему с пользователем.

## Контекст

Пользователь — Денис, владелец сервиса **VideoAI / botisk.ru** (оживление фото через AI).
У него есть YouTube-канал **VideoAI** (channel ID `UC6wfhx42PKNBW4ISk5Ai8ZA`),
на него уже автоматически постятся короткие видео-креативы через cron на VPS.

Канал нужно **упаковать**: обновить описание, ключевые слова, создать 4 тематических плейлиста
(Память / Детство / Питомцы / Love Story).

**Скрипт уже написан и лежит в репозитории:** `scripts/youtube_channel_packaging.py`.
Тексты описания и плейлистов — внутри скрипта, можно отредактировать перед запуском.

**Блокер:** текущий OAuth refresh_token имеет scope только `youtube.upload + youtube.readonly + yt-analytics.readonly`.
Для `channels.update` и `playlists.insert` нужен полный `https://www.googleapis.com/auth/youtube` (write).
Скрипт `scripts/youtube_oauth_setup.py` уже обновлён — новый scope в списке. Нужно лишь **переавторизоваться**.

## Что должен сделать пользователь (твоя задача — провести его за руку)

### Шаг 1 — Revoke старого токена

Открыть https://myaccount.google.com/permissions

Найти приложение **VideoAI Uploader** (или похожее по имени Google Cloud-проекта).
Нажать **Remove access**.

Это нужно, чтобы при следующей авторизации Google показал диалог согласия со ВСЕМИ
новыми scopes — иначе он молча отдаст старый токен без write-разрешений.

### Шаг 2 — Запустить OAuth-скрипт

В терминале на машине пользователя (Windows, PowerShell):

```powershell
cd "C:\AI moy\test 1 moy"
python scripts\youtube_oauth_setup.py
```

Скрипт:
1. Откроет в браузере страницу Google OAuth с правильными scopes.
2. На странице Google **обязательно выбрать канал VideoAI** (не личный аккаунт!).
   Если показывается селектор brand-каналов — кликнуть **VideoAI**.
3. После успеха перенаправит на `http://127.0.0.1:8090/?code=...` — этот код
   скрипт автоматически перехватит и обменяет на refresh_token.
4. Запишет новый `YOUTUBE_REFRESH_TOKEN` в `.env` (старый перезатрётся).

**Если что-то пойдёт не так:**
- «Could not connect to 127.0.0.1:8090» → порт занят. В скрипте поправить `REDIRECT_PORT = 8091` (или другой свободный) и обновить redirect URL в Google Cloud Console.
- Google отдал токен не от того канала → повторить Шаг 1 (revoke), затем при OAuth внимательно выбрать VideoAI.

### Шаг 3 — Проверить scopes свежего токена

```powershell
python -c "import urllib.request, urllib.parse, json, os; from dotenv import load_dotenv; load_dotenv(); body = urllib.parse.urlencode({'refresh_token': os.environ['YOUTUBE_REFRESH_TOKEN'], 'client_id': os.environ['YOUTUBE_CLIENT_ID'], 'client_secret': os.environ['YOUTUBE_CLIENT_SECRET'], 'grant_type': 'refresh_token'}).encode(); r = urllib.request.urlopen(urllib.request.Request('https://oauth2.googleapis.com/token', data=body, method='POST', headers={'Content-Type': 'application/x-www-form-urlencoded'})); d = json.loads(r.read()); print('scope:', d.get('scope'))"
```

Ожидаемый scope содержит `https://www.googleapis.com/auth/youtube`
(не путать с `youtube.upload` — последний без write-доступа).

Если этой строки нет — Google не выдал полный scope, нужно повторить Шаг 1.

### Шаг 4 — (Опционально) Подправить тексты

Открыть `scripts\youtube_channel_packaging.py`. Там 4 переменные:

- `CHANNEL_DESCRIPTION` — описание канала (~600 символов)
- `CHANNEL_KEYWORDS` — ключевые слова через запятую
- `PLAYLISTS` — массив из 4 плейлистов (title + description + tag)

Пользователь может попросить тебя:
- Переписать описание под другой tone of voice
- Добавить/убрать плейлисты
- Перевести на EN (если будет i18n-эксперимент)

### Шаг 5 — Запустить упаковку

```powershell
python scripts\youtube_channel_packaging.py
```

Скрипт идемпотентен:
- Описание/keywords обновляются всегда
- Плейлисты создаются только если ещё не существуют (проверка по title)

**Ожидаемый вывод (успех):**
```
✓ access_token получен
✓ канал: VideoAI
✓ branding обновлён
  ✓ создан '🎞 Память — оживление архивных фото' → PLxxxxx
  ✓ создан '👶 Детство — детские фото в движении' → PLxxxxx
  ✓ создан '🐶 Питомцы — в память о любимцах' → PLxxxxx
  ✓ создан '💍 Love Story — свадьбы и годовщины' → PLxxxxx
✅ Упаковка YouTube канала завершена
```

### Шаг 6 — Проверить результат

Открыть https://www.youtube.com/channel/UC6wfhx42PKNBW4ISk5Ai8ZA
- Вкладка «О канале»: новое описание + ключевые слова
- Вкладка «Плейлисты»: 4 новых плейлиста (пока без видео)

### Шаг 7 — (Опционально) Распределить уже загруженные видео по плейлистам

Каждое загруженное видео можно добавить в плейлист через UI YouTube Studio
(Контент → выбрать видео → Добавить в плейлист) **или** через API: эндпоинт
`playlistItems.insert`. Если пользователь захочет автоматизации — попроси меня
(консольного Claude) реализовать этот шаг отдельно.

## Что НЕ должен делать (важно!)

- ❌ Не предлагать commit/push в git перед верификацией — `.env` с свежим
  refresh_token **не должен** уходить в публичный репозиторий (он в `.gitignore`,
  это уже настроено).
- ❌ Не запускать `youtube_oauth_setup.py` повторно если первый раз сработал —
  это снова выпишет новый refresh_token и инвалидирует все автоматизации.
- ❌ Не делать массовых API-операций без чёткого запроса пользователя
  (на канале есть платный квота-лимит: 10 000 единиц/сутки).

## Если что-то сломалось — куда смотреть

| Симптом | Действие |
|---|---|
| `RuntimeError: OAuth refresh failed HTTP 401` | refresh_token инвалидирован → повторить Шаг 1-2 |
| `HTTP 403: Insufficient Permission` | scope недостаточный → повторить Шаг 1-3 |
| `quotaExceeded` | дневной лимит → подождать сутки или попросить увеличить в Google Cloud Console |
| Плейлист создался, но не публичный | в `youtube_channel_packaging.py` уже стоит `privacyStatus: public` |

## Полный путь файлов в репозитории

- `scripts/youtube_oauth_setup.py` — interactive OAuth flow
- `scripts/youtube_channel_packaging.py` — основной скрипт упаковки
- `scripts/youtube_uploader.py` — модуль с `_get_access_token`, `API_BASE`
- `.env` — токены (НЕ в git)

## После завершения

Когда упаковка завершится — попроси пользователя сказать «готово» или
прислать скриншот канала. После этого можешь:
1. Поздравить
2. Предложить следующий шаг: «Хочешь, чтобы я (консольный Claude) написал
   скрипт для авто-распределения новых видео по плейлистам в зависимости от
   `kling_<task>_v2_<category>_..` имени файла?»
