from __future__ import annotations
from typing import TYPE_CHECKING
import os
import json
import imaplib
import email
from bs4 import BeautifulSoup as BS
from datetime import datetime
import time
import logging
from telebot.types import Message , InlineKeyboardButton
from locales.localizer import Localizer
from FunPayAPI.updater.events import NewMessageEvent
from tg_bot import keyboards

if TYPE_CHECKING:
    from cardinal import Cardinal

logger = logging.getLogger("SteamGuardEmail")
localizer = Localizer()
_ = localizer.translate

NAME = "Steam Guard (Email)"
VERSION = "1.1"
DESCRIPTION = "Получение Steam Guard (Email) кода по команде с лимитами."
CREDITS = "@tinechelovec"
UUID = "d5dea9ee-c8a3-4dfe-a70c-f2e3a658fbaa"
SETTINGS_PAGE = False

PLUGIN_FOLDER = "storage/plugins/steam_guard_email"
DATA_FILE = os.path.join(PLUGIN_FOLDER, "data.json")
USAGE_FILE = os.path.join(PLUGIN_FOLDER, "usage.json")

os.makedirs(PLUGIN_FOLDER, exist_ok=True)
for fpath, default in [(DATA_FILE, {}), (USAGE_FILE, {})]:
    if not os.path.exists(fpath):
        with open(fpath, "w", encoding="utf-8") as f:
            json.dump(default, f, indent=4, ensure_ascii=False)

user_states = {}
last_seen_uid_map = {}

def load_data() -> dict:
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def save_data(data: dict):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)

def load_usage() -> dict:
    try:
        with open(USAGE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def save_usage(data: dict):
    with open(USAGE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)

def _format_time_left(seconds: int) -> str:
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    if h: return f"{h}ч {m}м"
    if m: return f"{m}м"
    return f"{s}с"

def get_imap_server(email_address: str) -> str:
    domain = email_address.split('@')[-1].lower()
    if 'mail.ru' in domain: return 'imap.mail.ru'
    if 'gmail' in domain: return 'imap.gmail.com'
    if 'yandex' in domain: return 'imap.yandex.ru'
    if 'rambler' in domain: return 'imap.rambler.ru'
    if 'firstmail' in domain: return 'imap.firstmail.ru'
    if 'notletters' in domain: return 'imap.notletters.com'
    if 'outlook' in domain or 'hotmail' in domain: return 'outlook.office365.com'
    raise ValueError("Неизвестный почтовый провайдер")

def check_email_credentials(email_address: str, password: str) -> bool:
    try:
        server = get_imap_server(email_address)
        with imaplib.IMAP4_SSL(server) as mail:
            mail.login(email_address, password)
        return True
    except Exception as e:
        logger.warning(f"Ошибка при проверке почты {email_address}: {e}")
        return False

def fetch_latest_steam_code(email_address: str, password: str, last_uid=None):
    try:
        server = get_imap_server(email_address)
        with imaplib.IMAP4_SSL(server) as mail:
            mail.login(email_address, password)
            mail.select("inbox")
            result, data = mail.uid('search', None, 'FROM "noreply@steampowered.com"')
            if not data or not data[0]:
                return None, None, last_uid
            uids = data[0].split()
            latest_uid = uids[-1]
            if latest_uid == last_uid:
                return None, None, last_uid
            result, data = mail.uid('fetch', latest_uid, '(RFC822)')
            raw_email = data[0][1]
            msg = email.message_from_bytes(raw_email)

            date_str = msg['Date']
            date = datetime.strptime(date_str, '%a, %d %b %Y %H:%M:%S %z').astimezone().strftime('%d.%m.%Y %H:%M:%S')

            for part in msg.walk():
                if part.get_content_type() == "text/html":
                    html = part.get_payload(decode=True).decode(errors="ignore")
                    soup = BS(html, 'html.parser')
                    text = soup.get_text(" ", strip=True).lower()
                    if "вам понадобится код steam guard" not in text and "you'll need to enter the steam guard code" not in text:
                        return None, None, last_uid
                    code_tag = soup.find('td', class_='title-48 c-blue1 fw-b a-center')
                    if code_tag:
                        return code_tag.get_text(strip=True), date, latest_uid
        return None, None, last_uid
    except Exception as e:
        logger.error(f"Ошибка при получении кода: {e}")
        return None, None, last_uid

def wait_for_steam_code(email_address: str = None, password: str = None, last_uid=None, timeout=60):
    start = time.time()
    while time.time() - start < timeout:
        code, date, new_uid = fetch_latest_steam_code(email_address, password, last_uid)
        if code:
            return code, date, new_uid
        time.sleep(5)
    return None, None, last_uid

orig_edit_plugin = keyboards.edit_plugin
def custom_edit_plugin(c, uuid, offset=0, ask_to_delete=False):
    kb = orig_edit_plugin(c, uuid, offset, ask_to_delete)
    if uuid == UUID:
        dev_btn = InlineKeyboardButton(text="👽 Разработчик", url=f"https://t.me/{CREDITS[1:]}")
        kb.keyboard[0] = [dev_btn]
    return kb
keyboards.edit_plugin = custom_edit_plugin

def cancel_if_command(message: Message) -> bool:
    return message.text.strip().startswith("/")

def addmail_start(message: Message, cardinal: Cardinal):
    user_states[message.chat.id] = {"step": "email"}
    cardinal.telegram.bot.send_message(message.chat.id, "📧 Введите вашу почту:")

def delmail_start(message: Message, cardinal: Cardinal):
    user_states[message.chat.id] = {"step": "del_target"}
    cardinal.telegram.bot.send_message(message.chat.id, "🗑 Введите команду или почту для удаления:")

def handle_fsm_step(message: Message, cardinal: Cardinal):
    chat_id = message.chat.id
    if chat_id not in user_states: return
    if cancel_if_command(message):
        user_states.pop(chat_id); return
    state = user_states[chat_id]
    data = load_data()
    uid = str(chat_id)

    if state["step"] == "del_target":
        target = message.text.strip().lower()
        if uid not in data or not data[uid]:
            cardinal.telegram.bot.send_message(chat_id, "❌ У вас нет привязанных аккаунтов.")
        else:
            before = len(data[uid])
            data[uid] = [acc for acc in data[uid] if acc["command"].lower() != target and acc["email"].lower() != target]
            if len(data[uid]) < before:
                save_data(data); cardinal.telegram.bot.send_message(chat_id, "✅ Аккаунт удалён.")
            else:
                cardinal.telegram.bot.send_message(chat_id, "❌ Аккаунт не найден.")
        user_states.pop(chat_id); return

    if state["step"] == "email":
        email_ = message.text.strip()
        if any(acc["email"].lower() == email_.lower() for acc in data.get(uid, [])):
            cardinal.telegram.bot.send_message(chat_id, "❌ Такая почта уже привязана.")
            user_states.pop(chat_id); return
        state["email"] = email_; state["step"] = "password"
        cardinal.telegram.bot.send_message(chat_id, "🔒 Введите пароль от почты:")

    elif state["step"] == "password":
        state["password"] = message.text.strip()
        if not check_email_credentials(state["email"], state["password"]):
            cardinal.telegram.bot.send_message(chat_id, "❌ Ошибка подключения к почте. Попробуйте ещё раз.")
            user_states.pop(chat_id); return
        state["step"] = "command"
        cardinal.telegram.bot.send_message(chat_id, "⌨️ Введите команду для получения кода:")

    elif state["step"] == "command":
        cmd = message.text.strip().lower()
        if any(acc["command"].lower() == cmd for acc in data.get(uid, [])):
            cardinal.telegram.bot.send_message(chat_id, "❌ Такая команда уже используется.")
            user_states.pop(chat_id); return
        state["command"] = cmd
        state["step"] = "limit"
        cardinal.telegram.bot.send_message(chat_id, "🔢 Введите лимит (например: 5, '-' для безлимита):")

    elif state["step"] == "limit":
        raw = message.text.strip()
        if raw == "-":
            acc = {"email": state["email"], "password": state["password"], "command": state["command"], "limit": None, "period_hours": None}
            data.setdefault(uid, []).append(acc); save_data(data)
            cardinal.telegram.bot.send_message(chat_id, f"✅ Аккаунт добавлен. Команда: <code>{acc['command']}</code>\n🔢 Лимит: без ограничений", parse_mode="HTML")
            user_states.pop(chat_id); return
        try:
            limit = int(raw); 
            if limit <= 0: raise ValueError
        except ValueError:
            cardinal.telegram.bot.send_message(chat_id, "❌ Введите число или '-'."); return
        state["limit"] = limit; state["step"] = "period"
        cardinal.telegram.bot.send_message(chat_id, "⏱ Введите период в часах (например: 24, '-' или 0 для навсегда):")

    elif state["step"] == "period":
        raw = message.text.strip()
        if raw in ["-", "0"]:
            acc = {"email": state["email"], "password": state["password"], "command": state["command"], "limit": state["limit"], "period_hours": None}
            data.setdefault(uid, []).append(acc); save_data(data)
            cardinal.telegram.bot.send_message(chat_id, f"✅ Аккаунт добавлен.\n💬 Команда: <code>{acc['command']}</code>\n🔢 Лимит: {acc['limit']} навсегда", parse_mode="HTML")
            user_states.pop(chat_id); return
        try:
            hours = int(raw); 
            if hours <= 0: raise ValueError
        except ValueError:
            cardinal.telegram.bot.send_message(chat_id, "❌ Введите положительное число, либо '-' или 0."); return
        acc = {"email": state["email"], "password": state["password"], "command": state["command"], "limit": state["limit"], "period_hours": hours}
        data.setdefault(uid, []).append(acc); save_data(data)
        cardinal.telegram.bot.send_message(chat_id, f"✅ Аккаунт добавлен.\n💬 Команда: <code>{acc['command']}</code>\n🔢 Лимит: {acc['limit']} за {hours}ч", parse_mode="HTML")
        user_states.pop(chat_id)

def listmails_handler(message: Message, cardinal: Cardinal):
    uid = str(message.chat.id); data = load_data()
    if uid not in data or not data[uid]:
        cardinal.telegram.bot.send_message(message.chat.id, "❌ У вас нет привязанных аккаунтов."); return
    lines = []
    for acc in data[uid]:
        if acc.get("limit") is None: limit_txt = "без ограничений"
        elif acc.get("period_hours") is None: limit_txt = f"{acc['limit']} навсегда"
        else: limit_txt = f"{acc['limit']} за {acc['period_hours']}ч"
        lines.append(f"📧 <code>{acc['email']}</code> — 💬 <code>{acc['command']}</code> — 🔢 <code>{limit_txt}</code>")
    cardinal.telegram.bot.send_message(message.chat.id, "📜 Ваши аккаунты:\n\n" + "\n".join(lines), parse_mode="HTML")

def new_message_handler(cardinal: Cardinal, event: NewMessageEvent):
    try:
        text = (getattr(event.message, "text", "") or "").strip().lower()
        if not text: return
        buyer_id = str(getattr(event.message, "chat_id", ""))
        data, usage, now = load_data(), load_usage(), int(time.time())

        for uid, accounts in data.items():
            for acc in accounts:
                if text != acc["command"].lower(): continue
                limit, period_hours = acc.get("limit"), acc.get("period_hours")

                if limit is None:
                    cardinal.account.send_message(event.message.chat_id, "🔍 Ищу код Steam Guard...")
                    code, date, new_uid = wait_for_steam_code(acc["email"], acc["password"], last_seen_uid_map.get(f"{uid}:{acc['email']}"))
                    if code:
                        cardinal.account.send_message(event.message.chat_id, f"✅ Ваш код: {code}\n🕒 Время: {date}")
                        last_seen_uid_map[f"{uid}:{acc['email']}"] = new_uid
                    else:
                        cardinal.account.send_message(event.message.chat_id, "❌ Код не найден за 60 секунд.")
                    return

                limit = int(limit)
                usage.setdefault(uid, {}).setdefault(buyer_id, {}).setdefault(acc["command"], {"count": 0})
                record = usage[uid][buyer_id][acc["command"]]

                if period_hours is None:
                    if record["count"] >= limit:
                        cardinal.account.send_message(event.message.chat_id, f"❌ Лимит {limit} навсегда исчерпан.")
                        save_usage(usage); return
                else:
                    period_seconds = int(period_hours) * 3600
                    record.setdefault("reset_time", now + period_seconds)
                    if now > record["reset_time"]:
                        record["count"] = 0; record["reset_time"] = now + period_seconds
                    if record["count"] >= limit:
                        seconds_left = int(record["reset_time"] - now)
                        cardinal.account.send_message(event.message.chat_id, f"❌ Лимит исчерпан. Новый запрос через {_format_time_left(seconds_left)}.")
                        save_usage(usage); return

                cardinal.account.send_message(event.message.chat_id, "🔍 Ищу код Steam Guard...")
                code, date, new_uid = wait_for_steam_code(acc["email"], acc["password"], last_seen_uid_map.get(f"{uid}:{acc['email']}"))
                if not code:
                    cardinal.account.send_message(event.message.chat_id, "❌ Код не найден за 60 секунд.")
                    return
                record["count"] += 1; save_usage(usage)
                left = max(0, limit - record["count"]); total_txt = "∞" if period_hours is None else str(limit)
                cardinal.account.send_message(event.message.chat_id, f"✅ Ваш код: {code}\n🕒 Время: {date}\n📊 Осталось: {left}/{total_txt}")
                last_seen_uid_map[f"{uid}:{acc['email']}"] = new_uid
                return
    except Exception as e:
        logger.exception(f"new_message_handler error: {e}")

def init_cardinal(cardinal: Cardinal):
    tg = cardinal.telegram
    tg.msg_handler(lambda m: addmail_start(m, cardinal), commands=["addmail"])
    tg.msg_handler(lambda m: delmail_start(m, cardinal), commands=["delmail"])
    tg.msg_handler(lambda m: handle_fsm_step(m, cardinal), func=lambda m: m.chat.id in user_states)
    tg.msg_handler(lambda m: listmails_handler(m, cardinal), commands=["listmails"])
    cardinal.add_telegram_commands(UUID, [
        ("addmail", "Добавить аккаунт", True),
        ("delmail", "Удалить аккаунт", True),
        ("listmails", "Список аккаунтов", True)
    ])

BIND_TO_PRE_INIT = [init_cardinal]
BIND_TO_NEW_MESSAGE = [new_message_handler]
BIND_TO_DELETE = None
