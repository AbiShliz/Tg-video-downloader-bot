import os
import logging
import sqlite3
from datetime import datetime
import yt_dlp
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# Настройка логирования
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

# Токен из переменных окружения
BOT_TOKEN = os.environ.get('BOT_TOKEN')
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не найден!")

# ⚠️ ВАЖНО: ВСТАВЬ СВОЙ ID СЮДА
ADMIN_ID = 123456789  # 👈 ЗАМЕНИ НА СВОЙ ID!

# Настройки yt-dlp
YDL_OPTIONS = {'format': 'best[ext=mp4]/best', 'quiet': True}

# Папка для скачивания (временная)
DOWNLOAD_DIR = 'downloads'
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# ========== РАБОТА С БАЗОЙ ДАННЫХ ==========
DB_PATH = '/data/users.db'  # 👈 ВАЖНО: путь к постоянному хранилищу

def init_db():
    """Создание таблицы пользователей"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users
                 (user_id INTEGER PRIMARY KEY,
                  username TEXT,
                  first_name TEXT,
                  last_name TEXT,
                  first_seen TEXT,
                  last_active TEXT,
                  downloads_count INTEGER DEFAULT 0)''')
    conn.commit()
    conn.close()
    logging.info(f"База данных инициализирована по пути {DB_PATH}")

def save_user(user_id, username, first_name, last_name):
    """Сохранить или обновить пользователя"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    c.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
    if c.fetchone():
        c.execute('''UPDATE users SET 
                     username = ?, first_name = ?, last_name = ?, last_active = ?
                     WHERE user_id = ?''',
                  (username, first_name, last_name, now, user_id))
    else:
        c.execute('''INSERT INTO users 
                     (user_id, username, first_name, last_name, first_seen, last_active, downloads_count)
                     VALUES (?, ?, ?, ?, ?, ?, 0)''',
                  (user_id, username, first_name, last_name, now, now))
    conn.commit()
    conn.close()

def increment_downloads(user_id):
    """Увеличить счетчик скачиваний"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''UPDATE users SET downloads_count = downloads_count + 1,
                  last_active = ?
                  WHERE user_id = ?''',
              (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), user_id))
    conn.commit()
    conn.close()

def get_stats():
    """Получить статистику для админа"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    c.execute("SELECT COUNT(*) FROM users")
    total_users = c.fetchone()[0]
    
    today = datetime.now().strftime("%Y-%m-%d")
    c.execute("SELECT COUNT(*) FROM users WHERE last_active LIKE ?", (f"{today}%",))
    active_today = c.fetchone()[0]
    
    c.execute("SELECT SUM(downloads_count) FROM users")
    total_downloads = c.fetchone()[0] or 0
    
    conn.close()
    return total_users, active_today, total_downloads

def get_all_users():
    """Получить всех пользователей для рассылки"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT user_id FROM users")
    users = c.fetchall()
    conn.close()
    return users

# ========== АДМИН-КОМАНДЫ ==========
async def is_admin(user_id):
    return user_id == ADMIN_ID

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    if not await is_admin(user_id):
        await update.message.reply_text("❌ У тебя нет прав администратора.")
        return
    
    total_users, active_today, total_downloads = get_stats()
    
    stats_text = f"""📊 **Статистика бота**

👥 Всего пользователей: {total_users}
📱 Активных сегодня: {active_today}
⬇️ Всего скачиваний: {total_downloads}
💰 Баланс Amvera: ~110 ₽

🕒 {datetime.now().strftime("%d.%m.%Y %H:%M")}"""
    
    await update.message.reply_text(stats_text, parse_mode='Markdown')

async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    if not await is_admin(user_id):
        await update.message.reply_text("❌ У тебя нет прав администратора.")
        return
    
    text = update.message.text.replace('/broadcast', '', 1).strip()
    
    if not text:
        await update.message.reply_text("❌ Напиши текст после /broadcast")
        return
    
    users = get_all_users()
    await update.message.reply_text(f"📢 Начинаю рассылку {len(users)} пользователям...")
    
    sent = 0
    failed = 0
    
    for (uid,) in users:
        try:
            await context.bot.send_message(
                uid, 
                f"📢 **Сообщение от администратора:**\n\n{text}",
                parse_mode='Markdown'
            )
            sent += 1
        except Exception as e:
            failed += 1
            logging.error(f"Не удалось отправить {uid}: {e}")
    
    await update.message.reply_text(f"✅ Рассылка завершена!\nОтправлено: {sent}\nОшибок: {failed}")

# ========== ОСНОВНЫЕ ФУНКЦИИ БОТА ==========
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    save_user(user.id, user.username, user.first_name, user.last_name)
    
    welcome_text = (
        "🎬 *Привет! Я бот для скачивания видео*\n\n"
        "Просто отправь мне ссылку на видео из:\n"
        "• TikTok\n"
        "• Instagram\n"
        "• YouTube\n\n"
        "⚡️ *Для администратора:* /stats - статистика"
    )
    await update.message.reply_text(welcome_text, parse_mode='Markdown')

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📖 *Как пользоваться ботом*\n\n"
        "1. Найди ссылку на видео\n"
        "2. Отправь её мне\n"
        "3. Получи видео\n\n"
        "Поддерживаются: YouTube, TikTok, Instagram",
        parse_mode='Markdown'
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    url = update.message.text.strip()
    
    save_user(user.id, user.username, user.first_name, user.last_name)
    
    msg = await update.message.reply_text("⏳ Скачиваю...")
    
    try:
        with yt_dlp.YoutubeDL(YDL_OPTIONS) as ydl:
            info = ydl.extract_info(url, download=True)
            filename = ydl.prepare_filename(info)
        
        increment_downloads(user.id)
        
        with open(filename, 'rb') as f:
            await update.message.reply_video(f)
        
        os.remove(filename)
        await msg.delete()
    except Exception as e:
        await msg.edit_text(f"❌ Ошибка: {str(e)[:100]}")

# ========== ЗАПУСК БОТА ==========
def main():
    # Создаем папку /data если её нет (на всякий случай)
    os.makedirs('/data', exist_ok=True)
    
    # Инициализируем базу данных
    init_db()
    
    app = Application.builder().token(BOT_TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("stats", stats_command))
    app.add_handler(CommandHandler("broadcast", broadcast_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    logging.info("🚀 Бот запущен с постоянным хранилищем /data")
    app.run_polling()

if __name__ == '__main__':
    main()