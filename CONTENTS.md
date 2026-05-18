# Содержимое проекта

## Основные файлы

| Файл | Что делает |
|------|-----------|
| `control.py` | Панель управления n8n. Команды: status, errors, activate, deactivate, notify, online, offline. Отправляет алерты в Telegram. |
| `dashboard.html` | Веб-дашборд (GitHub Pages). Показывает все workflows по проектам с описаниями, статусами, кнопками вкл/выкл и скачиванием JSON. |
| `.env` | Все ключи и пароли (не пушится в Git). n8n API, Telegram, WB, ЮКасса, PostgreSQL, Google, Yandex, OpenAI. |
| `README.md` | Описание репозитория и инструкция по запуску. |
| `PROJECTS_SUMMARY.md` | Краткая сводка: 7 проектов, 15 credentials, инфраструктура. |
| `PROJECTS_DETAILED.md` | Подробное описание каждого workflow: логика по нодам, сервисы, таблицы, API. |
| `.gitignore` | Исключения из Git: .env, __pycache__, .venv, .claude/. |

## Папка playground/

Учебные проекты, к основной работе не относятся.

| Файл | Что делает |
|------|-----------|
| `playground/hello.py` | Спрашивает имя, выводит приветствие. Первый скрипт проекта. |
| `playground/tictactoe.py` | Крестики-нолики в консоли. Игрок (X) vs компьютер (O) с простым AI. |
| `playground/tictactoe.html` | Крестики-нолики в браузере. Красивый UI, счётчик побед, тот же AI. |

## Что НЕ хранится в репо (в n8n)

| Workflow | Назначение |
|----------|-----------|
| Autopilot - Monitor & Restart | Мониторинг 1 раз в день (9:00). Проверяет ошибки, перезапускает упавшие, алертит в Telegram. |
| Dashboard API - Project Control Panel | Webhook API для дашборда. Собирает статусы workflows, группирует по проектам. |

## Ссылки

- Лендинг VideoAI: https://botisk.ru/
- Дашборд: https://botisk.ru/dashboard.html
- GitHub: https://github.com/vlztranss-cell/test-1-moy
- n8n: https://n8n.24isk.ru/
