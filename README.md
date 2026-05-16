# Claude Code Limit Notifier

Telegram-уведомления о лимитах [Claude Code](https://claude.ai/code) — чтобы не смотреть на часы в ожидании следующего окна.

## Что делает

| Событие | Уведомление |
|---|---|
| 5-часовой лимит ≥ 80% | ⚠️ "80% использовано, окно в HH:MM МСК" |
| 5-часовой лимит ≥ 90% | 🔴 "90% использовано" |
| Окно сбросилось | ✅ "Можно возвращаться к работе!" |

Уведомление об открытии окна приходит **точно в момент сброса** — через `at`-демон, без необходимости держать скрипты запущенными.

## Требования

- Linux-сервер (Ubuntu/Debian)
- Python 3.8+
- Claude Code с активной Pro или Max подпиской
- Аккаунт Telegram

## Установка

```bash
git clone https://github.com/knoweat-app/claude-limit-notifier
cd claude-limit-notifier
bash install.sh
```

Скрипт проведёт через все шаги:
1. Проверит зависимости (Python, `at`-демон)
2. Поможет создать Telegram-бота через @BotFather
3. Автоматически определит ваш Chat ID
4. Установит watcher и добавит cron-задачу
5. Отправит тестовое сообщение

## Как это работает

```
cron (каждую минуту)
  → claude-limit-watcher.py
      читает ~/.claude/.usage-cache.json (обновляется Claude Code)
      при utilization ≥ 80%: создаёт at-задачу на время resets_at
      при utilization ≥ 90%: шлёт предупреждение

at-задача (срабатывает в resets_at)
  → claude-limit-open.py
      шлёт "окно открылось" в Telegram
```

Watcher читает кеш, который Claude Code поддерживает сам — без дополнительных API-вызовов. Уведомление об открытии окна работает даже если вы закрыли терминал.

## Файлы после установки

```
~/.claude-limit-notifier/
  claude-limit-watcher.py   # проверяет лимиты каждую минуту
  claude-limit-open.py      # вызывается at-демоном при сбросе
~/.claude/
  .limit-notifier-state.json  # состояние между запусками
  .limit-notifier.log         # лог событий
```

## Полезные команды

```bash
# Посмотреть запланированные уведомления
atq

# Посмотреть лог
tail -f ~/.claude/.limit-notifier.log

# Удалить из cron
crontab -e   # убрать строку с claude-limit-watcher
```

## Удаление

```bash
# Убрать из cron
crontab -l | grep -v claude-limit-watcher | crontab -

# Удалить файлы
rm -rf ~/.claude-limit-notifier
rm -f ~/.claude/.limit-notifier-state.json ~/.claude/.limit-notifier.log
```
