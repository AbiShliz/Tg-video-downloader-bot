import os
import logging
import sqlite3
from datetime import datetime, timedelta
import yt_dlp
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler, PreCheckoutQueryHandler
from telegram.constants import ParseMode

# Настройка логирования

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
# Токен из переменных окружения
BOT_TOKEN = os.environ.get('BOT_TOKEN')
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не найден!")

# Твой Telegram ID
ADMIN_ID =920343231   # 👈 ТВОЙ ID

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
    },
    'vip': {
        'name': '💎 VIP',
        'price': 100,
        'period_days': 30,
        'features': ['Всё из Премиум', 'Подарок другу', 'Личный менеджер']
    }
}

# ========== БАЗА ДАННЫХ ==========
DB_PATH = '/data/users.db'

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users
                 (user_id INTEGER PRIMARY KEY,
                  username TEXT,
                  first_name TEXT,
                  last_name TEXT,
                  first_seen TEXT,
                  last_active TEXT,
                  downloads_today INTEGER DEFAULT 0,
                  last_download_date TEXT,
                  plan TEXT DEFAULT 'basic',
                  plan_expiry TEXT,
                  total_downloads INTEGER DEFAULT 0)''')
    conn.commit()
    conn.close()

def save_user(user_id, username, first_name, last_name):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    today = datetime.now().strftime("%Y-%m-%d")
    
    c.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
    user = c.fetchone()
    
    if user:
        # Сброс счетчика, если новый день
        last_date = user[8] if len(user) > 8 else None
        downloads_today = user[7] if len(user) > 7 else 0
        
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

def check_download_limit(user_id):
    """Проверка лимита скачиваний для пользователя"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT plan, downloads_today FROM users WHERE user_id = ?", (user_id,))
    result = c.fetchone()
    conn.close()
    
    if not result:
        return True, 0
    
    plan, downloads_today = result
    
    limits = {
        'basic': 3,
        'starter': 30,
        'premium': 999999,
        'vip': 999999
    }
    
    limit = limits.get(plan, 3)
    return downloads_today < limit, limit - downloads_today

def increment_downloads(user_id):
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

def update_user_plan(user_id, plan):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    expiry = (datetime.now() + timedelta(days=PLANS[plan]['period_days'])).strftime("%Y-%m-%d")
    c.execute("UPDATE users SET plan = ?, plan_expiry = ? WHERE user_id = ?", (plan, expiry, user_id))
    conn.commit()
    conn.close()

def get_user_plan(user_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT plan, plan_expiry FROM users WHERE user_id = ?", (user_id,))
    result = c.fetchone()
    conn.close()
    
    if result and result[0]:
        return result[0], result[1]
    return 'basic', None

def get_stats():
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

# ========== КОМАНДЫ БОТА ==========
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
    user = update.effective_user
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
    
    limits = {'basic': 3, 'starter': 30, 'premium': '∞', 'vip': '∞'}
    limit = limits.get(plan, 3)
    
    expiry_text = f"до {expiry}" if expiry else "бессрочно"
    
    profile_text = (
        f"👤 *Твой профиль*\n\n"
        f"ID: `{user_id}`\n"
        f"Имя: {user.first_name}\n\n"
        f"💎 *Тариф:* {plan_name}\n"
        f"⏳ Действует: {expiry_text}\n"
        f"📊 Сегодня: {downloads_today}/{limit}\n"
        f"📥 Всего скачано: {total_downloads}"
    )
    
    keyboard = [[InlineKeyboardButton("🔝 Выбрать тариф", callback_data="show_plans")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(profile_text, parse_mode='Markdown', reply_markup=reply_markup)

async def show_plans(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    current_plan, _ = get_user_plan(user_id)
    
    text = "💎 *Выбери свой тариф*\n\n"
    
    keyboard = []
    
    for plan_id, plan in PLANS.items():
        features = "\n".join([f"  • {f}" for f in plan['features']])
        price_text = "Бесплатно" if plan['price'] == 0 else f"{plan['price']} ★ / месяц"
        
        plan_text = f"{plan['name']}\n{price_text}\n{features}"
        
        if plan_id != current_plan and plan_id != 'basic':
            callback_data = f"buy_{plan_id}"
            keyboard.append([InlineKeyboardButton(f"✅ Купить {plan['name']}", callback_data=callback_data)])
    
    keyboard.append([InlineKeyboardButton("◀️ Назад", callback_data="back_to_profile")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(text, parse_mode='Markdown', reply_markup=reply_markup)

async def buy_plan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    plan_id = query.data.replace('buy_', '')
    plan = PLANS[plan_id]
    
    title = f"Покупка {plan['name']}"
    description = "\n".join(plan['features'])
    
    # Создаем счет в Telegram Stars
    await context.bot.send_invoice(
        chat_id=query.from_user.id,
        title=title,
        description=description,
        payload=f"subscription_{plan_id}",
        provider_token="",  # Пусто для Telegram Stars
        currency="XTR",  # Telegram Stars
        prices=[{"label": plan['name'], "amount": plan['price']}],
        start_parameter="create_subscription"
    )

async def pre_checkout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обязательный обработчик для платежей"""
    query = update.pre_checkout_query
    await query.answer(ok=True)

async def successful_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка успешного платежа"""
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
    
    limits = {'basic': 3, 'starter': 30, 'premium': '∞', 'vip': '∞'}
    limit = limits.get(plan, 3)
    
    expiry_text = f"до {expiry}" if expiry else "бессрочно"
    
    profile_text = (
        f"👤 *Твой профиль*\n\n"
        f"ID: `{user_id}`\n"
        f"Имя: {user.first_name}\n\n"
        f"💎 *Тариф:* {plan_name}\n"
        f"⏳ Действует: {expiry_text}\n"
        f"📊 Сегодня: {downloads_today}/{limit}\n"
        f"📥 Всего скачано: {total_downloads}"
    )
    
    keyboard = [[InlineKeyboardButton("🔝 Выбрать тариф", callback_data="show_plans")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(profile_text, parse_mode='Markdown', reply_markup=reply_markup)

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
    user = update.effective_user
    url = update.message.text.strip()
    
    save_user(user.id, user.username, user.first_name, user.last_name)
    
    # Проверка лимита
    can_download, remaining = check_download_limit(user.id)
    if not can_download:
        await update.message.reply_text(
            f"❌ Ты исчерпал лимит на сегодня.\n"
            f"Купи подписку /plan, чтобы скачивать больше!"
        )
        return
    
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
    os.makedirs('/data', exist_ok=True)
    init_db()
    
    app = Application.builder().token(BOT_TOKEN).build()
    
    # Обработчики команд
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("profile", profile))
    app.add_handler(CommandHandler("plan", show_plans))
    app.add_handler(CommandHandler("stats", stats_command))
    
    # Обработчики колбэков
    app.add_handler(CallbackQueryHandler(show_plans, pattern="^show_plans$"))
    app.add_handler(CallbackQueryHandler(back_to_profile, pattern="^back_to_profile$"))
    app.add_handler(CallbackQueryHandler(buy_plan, pattern="^buy_"))
    
    # Обработчики платежей
    app.add_handler(PreCheckoutQueryHandler(pre_checkout))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment))
    
    # Обработчик ссылок
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    logging.info("🚀 Бот запущен с тарифами!")
    app.run_polling()

if __name__ == '__main__':
    main()