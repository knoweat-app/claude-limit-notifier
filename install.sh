#!/bin/bash
# Claude Code Limit Notifier — интерактивный установщик
# https://github.com/knoweat-app/claude-limit-notifier

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

info()    { echo -e "${CYAN}→${NC} $1"; }
success() { echo -e "${GREEN}✓${NC} $1"; }
warn()    { echo -e "${YELLOW}!${NC} $1"; }
error()   { echo -e "${RED}✗${NC} $1"; exit 1; }
header()  { echo -e "\n${BOLD}$1${NC}"; echo "────────────────────────────────────────"; }

INSTALL_DIR="$HOME/.claude-limit-notifier"
WATCHER="$INSTALL_DIR/claude-limit-watcher.py"
OPEN_SCRIPT="$INSTALL_DIR/claude-limit-open.py"

clear
echo -e "${BOLD}"
echo "  ╔══════════════════════════════════════════╗"
echo "  ║   Claude Code Limit Notifier — Setup     ║"
echo "  ║   Уведомления в Telegram о лимитах CC    ║"
echo "  ╚══════════════════════════════════════════╝"
echo -e "${NC}"
echo "Этот скрипт настроит уведомления в Telegram:"
echo "  ⚠️  при 80% использования 5-часового лимита"
echo "  🔴  при 90% использования"
echo "  ✅  когда окно откроется снова"
echo ""
echo "Требования:"
echo "  • Linux-сервер с Python 3"
echo "  • Claude Code (Pro или Max подписка)"
echo "  • Аккаунт Telegram"
echo ""
read -rp "Продолжить? [Enter / Ctrl+C для отмены] "

# ─── Шаг 1: Проверка зависимостей ────────────────────────────────────────────

header "Шаг 1/5 — Проверка зависимостей"

python3 --version >/dev/null 2>&1 || error "Python 3 не найден. Установите: sudo apt install python3"
success "Python 3 найден: $(python3 --version)"

if ! command -v at >/dev/null 2>&1; then
    warn "at не установлен. Устанавливаю..."
    sudo apt-get install -y at >/dev/null 2>&1 || error "Не удалось установить at. Запустите: sudo apt install at"
    sudo systemctl enable --now atd >/dev/null 2>&1 || true
fi
success "at демон найден"

CC_CACHE="$HOME/.claude/.usage-cache.json"
CREDS="$HOME/.claude/.credentials.json"
if [ ! -f "$CREDS" ]; then
    error "Файл $CREDS не найден.\nУбедитесь что Claude Code установлен и вы вошли в аккаунт: claude /login"
fi
success "Claude Code credentials найдены"

# ─── Шаг 2: Создание Telegram бота ───────────────────────────────────────────

header "Шаг 2/5 — Telegram бот"

echo "Вам нужен Telegram-бот для отправки уведомлений."
echo "Если бот уже есть — пропустите инструкцию ниже."
echo ""
echo -e "${YELLOW}Как создать бота (30 секунд):${NC}"
echo "  1. Откройте Telegram и найдите @BotFather"
echo "  2. Отправьте команду: /newbot"
echo "  3. Введите имя бота (например: Claude Limit Bot)"
echo "  4. Введите username бота (например: my_claude_limit_bot)"
echo "     ⚠️  username должен заканчиваться на _bot"
echo "  5. BotFather выдаст токен вида:"
echo "     1234567890:ABCDefGhIJKlmNoPQRsTUVwxyZ"
echo ""
read -rp "Вставьте токен бота: " BOT_TOKEN
BOT_TOKEN=$(echo "$BOT_TOKEN" | tr -d '[:space:]')

# Validate token format
if ! echo "$BOT_TOKEN" | grep -qE '^[0-9]+:[A-Za-z0-9_-]{35,}$'; then
    error "Токен выглядит неправильно. Проверьте что скопировали полностью."
fi

# Test token
info "Проверяю токен..."
BOT_RESPONSE=$(python3 -c "
import urllib.request, json
try:
    r = urllib.request.urlopen('https://api.telegram.org/bot$BOT_TOKEN/getMe', timeout=10)
    d = json.loads(r.read())
    if d.get('ok'):
        print('ok:' + d['result']['username'])
    else:
        print('fail')
except Exception as e:
    print('error:' + str(e))
" 2>/dev/null)

if [[ "$BOT_RESPONSE" == ok:* ]]; then
    BOT_USERNAME="${BOT_RESPONSE#ok:}"
    success "Бот найден: @$BOT_USERNAME"
else
    error "Не удалось проверить токен: $BOT_RESPONSE\nПроверьте токен и интернет-соединение."
fi

# ─── Шаг 3: Получение Chat ID ─────────────────────────────────────────────────

header "Шаг 3/5 — Ваш Chat ID"

echo "Теперь нужно узнать ваш Telegram Chat ID — это число,"
echo "которое идентифицирует ваш чат с ботом."
echo ""
echo -e "${YELLOW}Сделайте это прямо сейчас:${NC}"
echo "  1. Откройте Telegram"
echo "  2. Найдите вашего бота @$BOT_USERNAME"
echo "  3. Нажмите START или отправьте любое сообщение (например: /start)"
echo ""
read -rp "Нажмите Enter когда отправите сообщение боту..."

info "Получаю Chat ID..."
CHAT_ID=$(python3 -c "
import urllib.request, json, time
for attempt in range(5):
    try:
        r = urllib.request.urlopen('https://api.telegram.org/bot$BOT_TOKEN/getUpdates?limit=10', timeout=10)
        d = json.loads(r.read())
        if d.get('ok') and d['result']:
            # Get the most recent chat_id
            updates = sorted(d['result'], key=lambda x: x['update_id'], reverse=True)
            for upd in updates:
                msg = upd.get('message') or upd.get('channel_post')
                if msg and msg.get('chat', {}).get('id'):
                    print(msg['chat']['id'])
                    exit(0)
    except Exception as e:
        pass
    time.sleep(1)
print('')
" 2>/dev/null)

if [ -z "$CHAT_ID" ]; then
    echo ""
    warn "Не удалось определить Chat ID автоматически."
    echo ""
    echo "Определите вручную:"
    echo "  1. Откройте браузер"
    echo "  2. Перейдите по адресу:"
    echo "     https://api.telegram.org/bot${BOT_TOKEN}/getUpdates"
    echo "  3. Найдите в ответе: \"chat\":{\"id\": XXXXXXXX"
    echo "     Это и есть ваш Chat ID"
    echo ""
    read -rp "Введите Chat ID вручную: " CHAT_ID
fi

CHAT_ID=$(echo "$CHAT_ID" | tr -d '[:space:]')
if ! echo "$CHAT_ID" | grep -qE '^-?[0-9]+$'; then
    error "Chat ID должен быть числом. Получено: $CHAT_ID"
fi
success "Chat ID: $CHAT_ID"

# ─── Шаг 4: Установка скриптов ───────────────────────────────────────────────

header "Шаг 4/5 — Установка"

mkdir -p "$INSTALL_DIR"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Copy and configure watcher
sed "s|YOUR_BOT_TOKEN|$BOT_TOKEN|g; s|YOUR_CHAT_ID|$CHAT_ID|g; s|~/claude-limit-open.py|$OPEN_SCRIPT|g" \
    "$SCRIPT_DIR/claude-limit-watcher.py" > "$WATCHER"
chmod +x "$WATCHER"
success "Watcher установлен: $WATCHER"

# Copy and configure open-script
sed "s|YOUR_BOT_TOKEN|$BOT_TOKEN|g; s|YOUR_CHAT_ID|$CHAT_ID|g" \
    "$SCRIPT_DIR/claude-limit-open.py" > "$OPEN_SCRIPT"
chmod +x "$OPEN_SCRIPT"
success "Open-скрипт установлен: $OPEN_SCRIPT"

# Fix LOG_FILE path in open script
sed -i "s|os.path.expanduser.*limit-notifier.log.*|\"$HOME/.claude/.limit-notifier.log\"|g" "$OPEN_SCRIPT" 2>/dev/null || true

# Add cron job (avoid duplicates)
CRON_CMD="* * * * * /usr/bin/python3 $WATCHER 2>/dev/null"
if crontab -l 2>/dev/null | grep -qF "claude-limit-watcher"; then
    warn "Cron уже настроен, пропускаю"
else
    (crontab -l 2>/dev/null; echo "$CRON_CMD") | crontab -
    success "Cron добавлен (каждую минуту)"
fi

# ─── Шаг 5: Тест ─────────────────────────────────────────────────────────────

header "Шаг 5/5 — Тест"

info "Отправляю тестовое сообщение в Telegram..."
TEST_RESULT=$(python3 -c "
import urllib.request, json
url = 'https://api.telegram.org/bot$BOT_TOKEN/sendMessage'
payload = json.dumps({
    'chat_id': '$CHAT_ID',
    'text': '✅ <b>Claude Limit Notifier установлен!</b>\n\nБудете получать уведомления:\n⚠️ при 80% лимита — с временем открытия окна\n🔴 при 90% лимита\n✅ когда окно откроется',
    'parse_mode': 'HTML'
}).encode()
try:
    req = urllib.request.Request(url, data=payload, headers={'Content-Type': 'application/json'})
    r = urllib.request.urlopen(req, timeout=15)
    d = json.loads(r.read())
    print('ok' if d.get('ok') else 'fail')
except Exception as e:
    print('error: ' + str(e))
" 2>/dev/null)

if [ "$TEST_RESULT" = "ok" ]; then
    success "Тестовое сообщение отправлено — проверьте Telegram!"
else
    warn "Не удалось отправить тест: $TEST_RESULT"
    warn "Настройка завершена, но проверьте соединение с Telegram."
fi

# ─── Готово ───────────────────────────────────────────────────────────────────

echo ""
echo -e "${GREEN}${BOLD}Установка завершена!${NC}"
echo ""
echo "Что дальше:"
echo "  • Watcher проверяет лимиты каждую минуту"
echo "  • При 80% придёт предупреждение с временем сброса"
echo "  • При достижении resets_at придёт «окно открылось»"
echo ""
echo "Логи: $HOME/.claude/.limit-notifier.log"
echo "Статус at-задач: atq"
echo ""
echo "Удалить: crontab -e  (убрать строку claude-limit-watcher)"
