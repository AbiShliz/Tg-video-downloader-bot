import os
import logging
import sqlite3
from datetime import datetime, timedelta
import yt_dlp
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler, PreCheckoutQueryHandler
from telegram.constants import ParseMode

# Настройка логирования - ИСПРАВЛЕНО (asctime)
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

# Токен из переменных окружения
BOT_TOKEN = os.environ.get('BOT_TOKEN')
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не найден!")

# Твой Telegram ID (ЗАМЕНИ НА СВОЙ, ЕСЛИ НУЖНО)
ADMIN_ID = 920343231  # Здесь твой ID

# Настройки yt-dlp
YDL_OPTIONS = {'format': 'best[ext=mp4]/best', 'quiet': True}

# Папка для скачивания
DOWNLOAD_DIR = 'downloads'
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# ========== ТАРИФЫ ==========
PLANS = {
    'basic': {
        'name': '🔹 Базовый',
        'price': 0,
        'period_days': 30,
        'features': ['3 видео в день', '480p качество']
    },
    'starter': {
        'name': '🔸 Стартовый',
        'price': 25,
        'period_days': 30,
        'features': ['30 видео в день', '720p качество', 'Приоритетная обработка']
    },
    'premium': {
        'name': '🔹 Премиум',
        'price': 50,
        'period_days': 30,
        'features': ['Безлимитно', '4K качество', 'Без рекламы', 'Приоритет 24/7']
    }
}

# ========== БАЗА ДАННЫХ ==========
DB_PATH = '/data/users.db'

def init_db():
    """Создание или обновление таблицы пользователей"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    # Создаем таблицу, если её нет (только базовые колонки)
    c.execute('''CREATE TABLE IF NOT EXISTS users
                 (user_id INTEGER PRIMARY KEY,
                  username TEXT,
                  first_name TEXT,
                  last_name TEXT,
                  first_seen TEXT,
                  last_active TEXT)''')

    # --- СПИСОК НОВЫХ КОЛОНОК, КОТОРЫЕ НУЖНО ДОБАВИТЬ ---
    # Пытаемся добавить каждую колонку. Если она уже есть - просто пропускаем ошибку.
    try:
        c.execute("ALTER TABLE users ADD COLUMN downloads_today INTEGER DEFAULT 0")
        logging.info("Колонка 'downloads_today' добавлена")
    except sqlite3.OperationalError:
        pass  # колонка уже есть

    try:
        c.execute("ALTER TABLE users ADD COLUMN last_download_date TEXT")
        logging.info("Колонка 'last_download_date' добавлена")
    except sqlite3.OperationalError:
        pass

    try:
        c.execute("ALTER TABLE users ADD COLUMN plan TEXT DEFAULT 'basic'")
        logging.info("Колонка 'plan' добавлена")
    except sqlite3.OperationalError:
        pass

    try:
        c.execute("ALTER TABLE users ADD COLUMN plan_expiry TEXT")
        logging.info("Колонка 'plan_expiry' добавлена")
    except sqlite3.OperationalError:
        pass

    try:
        c.execute("ALTER TABLE users ADD COLUMN total_downloads INTEGER DEFAULT 0")
        logging.info("Колонка 'total_downloads' добавлена")
    except sqlite3.OperationalError:
        pass

    conn.commit()
    conn.close()
    logging.info(f"База данных проверена/обновлена по пути {DB_PATH}")

# --- Здесь идут все остальные функции (save_user, check_download_limit и т.д.) ---
# Их я не привожу целиком в этом ответе, чтобы не занимать место,
# но ты должен скопировать их ИЗ ТОГО КОДА, КОТОРЫЙ Я СКИДЫВАЛ РАНЬШЕ.
# ВАЖНО: функции save_user, get_user_plan, check_download_limit и др. должны быть ниже.
# Убедись, что в твоем коде на GitHub они есть.

# (Здесь должен быть весь остальной твой код с функциями и хендлерами)

# ========== ЗАПУСК БОТА ==========
def main():
    os.makedirs('/data', exist_ok=True)
    init_db()  # <-- Теперь эта функция безопасно обновит базу

    app = Application.builder().token(BOT_TOKEN).build()
    # ... (все хендлеры) ...
    app.run_polling()

if __name__ == '__main__':
    main()