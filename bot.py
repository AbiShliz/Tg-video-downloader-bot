import os
import logging
import sqlite3
import time
from datetime import datetime, timedelta
import yt_dlp
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler, PreCheckoutQueryHandler
from telegram.constants import ParseMode

# Небольшая задержка перед запуском (чтобы избежать конфликтов)
time.sleep(3)

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Токен из переменных окружения
BOT_TOKEN = os.environ.get('BOT_TOKEN')
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не найден!")

# Твой Telegram ID (админ)
ADMIN_ID = 920343231  # Замени на свой, если нужно

# Настройки yt-dlp для скачивания
YDL_OPTIONS = {
    'format': 'best[ext=mp4]/best',
    'quiet': True,
    'no_warnings': True,
    'extract_flat': False,
}

# Папка для временных файлов
DOWNLOAD_DIR = 'downloads'
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# ========== ТАРИФЫ ==========
PLANS = {
    'basic': {
        'name': '🔹 Базовый',
        'price': 0,
        'period_days': 30,
        'limit': 3,
        'features': ['3 видео в день', '480p качество']
    },
    'starter': {
        'name': '🔸 Стартовый',
        'price': 25,
        'period_days': 30,
        'limit': 30,
        'features': ['30 видео в день', '720p качество', 'Приоритетная обработка']
    },
    'premium': {
        'name': '💎 Премиум',
        'price': 50,
        'period_days': 30,
        'limit': 999999,
        'features': ['Безлимитно', '4K качество', 'Без рекламы', 'Приоритет 24/7']
    }
}

# ========== БАЗА ДАННЫХ ==========
DB_PATH = '/data/users.db'

def init_db():
    """Создание или обновление таблицы пользователей"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    # Создаем таблицу, если её нет
    c.execute('''CREATE TABLE IF NOT EXISTS users
                 (user_id INTEGER PRIMARY KEY,
                  username TEXT,
                  first_name TEXT,
                  last_name TEXT,
                  first_seen TEXT,
                  last_active TEXT)''')
    
    # Добавляем новые колонки (если их нет)
    columns_to_add = [
        ('downloads_today', 'INTEGER DEFAULT 0'),
        ('last_download_date', 'TEXT'),
        ('plan', 'TEXT DEFAULT "basic"'),
        ('plan_expiry', 'TEXT'),
        ('total_downloads', 'INTEGER DEFAULT 0')
    ]
    
    for col_name, col_type in columns_to_add:
        try:
            c.execute(f"ALTER TABLE users ADD COLUMN {col_name} {col_type}")
            logger.info(f"Колонка '{col_name}' добавлена")
        except sqlite3.OperationalError:
            pass  # колонка уже есть
    
    conn.commit()
    conn.close()
    logger.info("База данных инициализирована")

def get_user(user_id):
    """Получить данные пользователя"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
    user = c.fetchone()
    conn.close()
    return user

def save_user(user_id, username, first_name, last_name):
    """Сохранить или обновить пользователя"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    today = datetime.now().strftime("%Y-%m-%d")
    
    user = get_user(user_id)
    
    if user:
        # Сброс счетчика, если новый день
        last_date = user[7] if len(user) > 7 else None
        downloads_today = user[6] if len(user) > 6 else 0
        
        if last_date != today:
            downloads_today = 0
        
        c.execute('''UPDATE users SET 
                     username = ?, first_name = ?, last_name = ?, last_active = ?,
                     downloads_today = ?, last_download_date = ?
                     WHERE user_id = ?''',
                  (username, first_name, last_name, now, downloads_today, today, user_id))
    else:
        c.execute('''INSERT INTO users 
                     (user_id, username, first_name, last_name, first_seen, last_active, 
                      downloads_today, last_download_date, plan, total_downloads)
                     VALUES (?, ?, ?, ?, ?, ?, 0, ?, 'basic', 0)''',
                  (user_id, username, first_name, last_name, now, now, today))
    
    conn.commit()
    conn.close()

def update_user_plan(user_id, plan):
    """Обновить тариф пользователя"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    expiry = (datetime.now() + timedelta(days=PLANS[plan]['period_days'])).strftime("%Y-%m-%d")
    c.execute("UPDATE users SET plan = ?, plan_expiry = ? WHERE user_id = ?", (plan, expiry, user_id))
    conn.commit()
    conn.close()

def get_user_plan(user_id):
    """Получить тариф пользователя"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT plan, plan_expiry FROM users WHERE user_id = ?", (user_id,))
    result = c.fetchone()
    conn.close()
    
    if result and result[0]:
        return result[0], result[1]
    return 'basic', None

def check_download_limit(user_id):
    """Проверка лимита скачиваний"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT plan, downloads_today FROM users WHERE user_id = ?", (user_id,))
    result = c.fetchone()
    conn.close()
    
    if not result:
        return True, 3
    
    plan, downloads_today = result
    limit = PLANS.get(plan, PLANS['basic'])['limit']
    
    return downloads_today < limit, limit - downloads_today

def increment_downloads(user_id):
    """Увеличить счетчик скачиваний"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    today = datetime.now().strftime("%Y-%m-%d")
    
    c.execute('''UPDATE users SET 
                 downloads_today = downloads_today + 1,
                 total_downloads = total_downloads + 1,
                 last_active = ?,
                 last_download_date = ?
                 WHERE user_id = ?''',
              (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), today, user_id))
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
    
    c.execute("SELECT SUM(total_downloads) FROM users")
    total_downloads = c.fetchone()[0] or 0
    
    c.execute("SELECT plan, COUNT(*) FROM users GROUP BY plan")
    plans_stats = c.fetchall()
    
    conn.close()
    return total_users, active_today, total_downloads, plans_stats

# ========== ФУНКЦИЯ СКАЧИВАНИЯ ==========
async def download_video(url):
    """Скачать видео по ссылке"""
    try:
        ydl_opts = {
            **YDL_OPTIONS,
            'outtmpl': os.path.join(DOWNLOAD_DIR, '%(title)s_%(id)s.%(ext)s'),
        }
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            filename = ydl.prepare_filename(info)
            
            # Проверяем, что файл существует
            if os.path.exists(filename):
                return filename
            return None
    except Exception as e:
        logger.error(f"Ошибка скачивания: {e}")
        return None

# ========== ОБРАБОТЧИКИ КОМАНД ==========
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик /start"""
    user = update.effective_user
    save_user(user.id, user.username, user.first_name, user.last_name)
    
    welcome_text = (
        "🎬 *Привет! Я бот для скачивания видео*\n\n"
        "Просто отправь мне ссылку на видео из:\n"
        "• TikTok\n"
        "• Instagram\n"
        "• YouTube\n\n"
        "🔹 *Команды:*\n"
        "/plan — посмотреть тарифы\n"
        "/profile — мой профиль\n"
        "/help — помощь"
    )
    await update.message.reply_text(welcome_text, parse_mode='Markdown')

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик /help"""
    await update.message.reply_text(
        "📖 *Как пользоваться ботом*\n\n"
        "1. Найди ссылку на видео\n"
        "2. Отправь её мне\n"
        "3. Получи видео\n\n"
        "Поддерживаются: YouTube, TikTok, Instagram\n\n"
        "Для просмотра тарифов: /plan",
        parse_mode='Markdown'
    )

async def profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик /profile"""
    user = update.effective_user
    user_id = user.id
    
    save_user(user_id, user.username, user.first_name, user.last_name)
    
    plan, expiry = get_user_plan(user_id)
    plan_name = PLANS[plan]['name']
    
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT downloads_today, total_downloads FROM users WHERE user_id = ?", (user_id,))
    result = c.fetchone()
    conn.close()
    
    downloads_today = result[0] if result else 0
    total_downloads = result[1] if result else 0
    
    limit = PLANS[plan]['limit']
    limit_display = '∞' if limit > 9999 else limit
    
    expiry_text = f"до {expiry}" if expiry else "бессрочно"
    
    profile_text = (
        f"👤 *Твой профиль*\n\n"
        f"ID: `{user_id}`\n"
        f"Имя: {user.first_name}\n\n"
        f"💎 *Тариф:* {plan_name}\n"
        f"⏳ Действует: {expiry_text}\n"
        f"📊 Сегодня: {downloads_today}/{limit_display}\n"
        f"📥 Всего скачано: {total_downloads}"
    )
    
    keyboard = [[InlineKeyboardButton("🔝 Выбрать тариф", callback_data="show_plans")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(profile_text, parse_mode='Markdown', reply_markup=reply_markup)

async def show_plans(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать тарифы"""
    # Проверяем, откуда пришел вызов (из callback или команды)
    if update.callback_query:
        query = update.callback_query
        await query.answer()
        user_id = query.from_user.id
        message = query.message
        edit = True
    else:
        user_id = update.effective_user.id
        message = update.message
        edit = False
    
    current_plan, _ = get_user_plan(user_id)
    
    text = "💎 *Выбери свой тариф*\n\n"
    keyboard = []
    
    for plan_id, plan in PLANS.items():
        if plan_id == 'basic':
            continue  # Базовый тариф не продаем
        
        features = "\n".join([f"  • {f}" for f in plan['features']])
        text += f"{plan['name']}\n{plan['price']} ★ / месяц\n{features}\n\n"
        
        if plan_id != current_plan:
            keyboard.append([InlineKeyboardButton(
                f"✅ Купить {plan['name']}", 
                callback_data=f"buy_{plan_id}"
            )])
    
    keyboard.append([InlineKeyboardButton("◀️ Назад в профиль", callback_data="back_to_profile")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    if edit:
        await query.edit_message_text(text, parse_mode='Markdown', reply_markup=reply_markup)
    else:
        await message.reply_text(text, parse_mode='Markdown', reply_markup=reply_markup)

async def buy_plan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка покупки тарифа"""
    query = update.callback_query
    await query.answer()
    
    plan_id = query.data.replace('buy_', '')
    plan = PLANS[plan_id]
    
    title = f"Покупка {plan['name']}"
    description = "\n".join(plan['features'])
    
    await context.bot.send_invoice(
        chat_id=query.from_user.id,
        title=title,
        description=description[:255],  # Telegram ограничение
        payload=f"subscription_{plan_id}",
        provider_token="",  # Пусто для Telegram Stars
        currency="XTR",
        prices=[{"label": plan['name'], "amount": plan['price']}]
    )

async def pre_checkout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Подтверждение платежа"""
    query = update.pre_checkout_query
    await query.answer(ok=True)

async def successful_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Успешная оплата"""
    user_id = update.effective_user.id
    payload = update.message.successful_payment.invoice_payload
    
    if payload.startswith('subscription_'):
        plan_id = payload.replace('subscription_', '')
        update_user_plan(user_id, plan_id)
        
        plan = PLANS[plan_id]
        
        await update.message.reply_text(
            f"✅ *Оплата прошла успешно!*\n\n"
            f"Тебе активирован тариф {plan['name']} на 30 дней.\n"
            f"Спасибо за поддержку! 🙏",
            parse_mode='Markdown'
        )

async def back_to_profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Вернуться в профиль"""
    query = update.callback_query
    await query.answer()
    
    user = query.from_user
    user_id = user.id
    
    plan, expiry = get_user_plan(user_id)
    plan_name = PLANS[plan]['name']
    
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT downloads_today, total_downloads FROM users WHERE user_id = ?", (user_id,))
    result = c.fetchone()
    conn.close()
    
    downloads_today = result[0] if result else 0
    total_downloads = result[1] if result else 0
    
    limit = PLANS[plan]['limit']
    limit_display = '∞' if limit > 9999 else limit
    
    expiry_text = f"до {expiry}" if expiry else "бессрочно"
    
    profile_text = (
        f"👤 *Твой профиль*\n\n"
        f"ID: `{user_id}`\n"
        f"Имя: {user.first_name}\n\n"
        f"💎 *Тариф:* {plan_name}\n"
        f"⏳ Действует: {expiry_text}\n"
        f"📊 Сегодня: {downloads_today}/{limit_display}\n"
        f"📥 Всего скачано: {total_downloads}"
    )
    
    keyboard = [[InlineKeyboardButton("🔝 Выбрать тариф", callback_data="show_plans")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(profile_text, parse_mode='Markdown', reply_markup=reply_markup)

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Статистика для админа"""
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ У тебя нет прав администратора.")
        return
    
    total_users, active_today, total_downloads, plans_stats = get_stats()
    
    plans_text = ""
    for plan, count in plans_stats:
        plans_text += f"{PLANS[plan]['name']}: {count}\n"
    
    stats_text = f"""📊 **Статистика бота**

👥 Всего пользователей: {total_users}
📱 Активных сегодня: {active_today}
⬇️ Всего скачиваний: {total_downloads}

**Тарифы:**
{plans_text}

💰 Баланс Amvera: ~110 ₽

🕒 {datetime.now().strftime("%d.%m.%Y %H:%M")}"""
    
    await update.message.reply_text(stats_text, parse_mode='Markdown')

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка ссылок на видео"""
    user = update.effective_user
    url = update.message.text.strip()
    
    # Сохраняем пользователя
    save_user(user.id, user.username, user.first_name, user.last_name)
    
    # Проверка лимита
    can_download, remaining = check_download_limit(user.id)
    if not can_download:
        await update.message.reply_text(
            f"❌ Ты исчерпал лимит на сегодня.\n"
            f"Купи подписку /plan, чтобы скачивать больше!"
        )
        return
    
    status_msg = await update.message.reply_text("⏳ Скачиваю видео...")
    
    try:
        # Скачиваем видео
        filepath = await download_video(url)
        
        if not filepath or not os.path.exists(filepath):
            await status_msg.edit_text(
                "❌ Не удалось скачать видео.\n"
                "Проверь ссылку или попробуй позже."
            )
            return
        
        # Проверяем размер файла
        file_size = os.path.getsize(filepath)
        if file_size > 50 * 1024 * 1024:  # 50MB
            await status_msg.edit_text("❌ Видео слишком большое (больше 50MB)")
            os.remove(filepath)
            return
        
        # Отправляем видео
        await status_msg.edit_text("📤 Отправляю видео...")
        
        with open(filepath, 'rb') as f:
            await update.message.reply_video(
                video=f,
                caption="✅ Готово!",
                supports_streaming=True
            )
        
        # Увеличиваем счетчик
        increment_downloads(user.id)
        
        # Удаляем временный файл
        os.remove(filepath)
        await status_msg.delete()
        
    except Exception as e:
        logger.error(f"Ошибка обработки: {e}")
        await status_msg.edit_text(
            "❌ Произошла ошибка.\n"
            "Попробуй другую ссылку или позже."
        )

# ========== ЗАПУСК БОТА ==========
def main():
    """Главная функция"""
    # Инициализируем базу данных
    os.makedirs('/data', exist_ok=True)
    init_db()
    
    # Создаем приложение
    app = Application.builder().token(BOT_TOKEN).build()
    
    # Добавляем обработчики команд
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("profile", profile))
    app.add_handler(CommandHandler("plan", show_plans))
    app.add_handler(CommandHandler("stats", stats_command))
    
    # Добавляем обработчики callback-запросов
    app.add_handler(CallbackQueryHandler(show_plans, pattern="^show_plans$"))
    app.add_handler(CallbackQueryHandler(back_to_profile, pattern="^back_to_profile$"))
    app.add_handler(CallbackQueryHandler(buy_plan, pattern="^buy_"))
    
    # Добавляем обработчики платежей
    app.add_handler(PreCheckoutQueryHandler(pre_checkout))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment))
    
    # Добавляем обработчик сообщений (ссылки)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    logger.info("🚀 Бот с монетизацией успешно запущен!")
    
    # Запускаем бота
    app.run_polling()

if __name__ == '__main__':
    main()