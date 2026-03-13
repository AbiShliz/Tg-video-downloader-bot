import os
import logging
import sqlite3
import time
import random
from datetime import datetime, timedelta
import yt_dlp
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler, PreCheckoutQueryHandler

# Задержка перед запуском
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
ADMIN_ID = 920343231  # ТВОЙ ID

# Настройки скачивания
YDL_OPTIONS = {
    'format': 'best[ext=mp4]/best',
    'quiet': True,
    'no_warnings': True,
}
DOWNLOAD_DIR = 'downloads'
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# ========== ТАРИФЫ ==========
PLANS = {
    'basic': {
        'name': '🔹 Базовый',
        'price': 0,
        'limit': 3,
        'features': ['3 видео в день', '480p']
    },
    'starter': {
        'name': '🔸 Стартовый',
        'price': 25,
        'limit': 30,
        'features': ['30 видео в день', '720p', 'Приоритет']
    },
    'premium': {
        'name': '💎 Премиум',
        'price': 50,
        'limit': 999999,
        'features': ['Безлимитно', '4K', 'Приоритет 24/7']
    }
}

# ========== БАЗА ДАННЫХ ==========
DB_PATH = '/data/users.db'

def init_db():
    """СОЗДАНИЕ БАЗЫ - ГАРАНТИРОВАННО РАБОТАЕТ"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    # УДАЛЯЕМ старую таблицу если есть (чтобы гарантировать чистоту)
    c.execute('DROP TABLE IF EXISTS users')
    
    # СОЗДАЕМ новую таблицу со ВСЕМИ колонками сразу
    c.execute('''CREATE TABLE users (
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        first_name TEXT,
        last_name TEXT,
        first_seen TEXT,
        last_active TEXT,
        downloads_today INTEGER DEFAULT 0,
        last_download_date TEXT,
        plan TEXT DEFAULT 'basic',
        plan_expiry TEXT,
        total_downloads INTEGER DEFAULT 0,
        referrer_id INTEGER DEFAULT NULL,
        referral_code TEXT UNIQUE,
        referral_count INTEGER DEFAULT 0,
        bonus_downloads INTEGER DEFAULT 0
    )''')
    
    conn.commit()
    conn.close()
    logger.info("✅ База данных создана заново со всеми колонками")

def get_user(user_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
    user = c.fetchone()
    conn.close()
    return user

def save_user(user_id, username, first_name, last_name):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    today = datetime.now().strftime("%Y-%m-%d")
    
    user = get_user(user_id)
    
    if not user:
        # Новый пользователь
        referral_code = f"ref{user_id}{random.randint(100, 999)}"
        c.execute('''INSERT INTO users 
            (user_id, username, first_name, last_name, first_seen, last_active, 
             last_download_date, referral_code)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
            (user_id, username, first_name, last_name, now, now, today, referral_code))
    else:
        # Обновляем существующего
        c.execute('''UPDATE users SET 
            username = ?, first_name = ?, last_name = ?, last_active = ?
            WHERE user_id = ?''',
            (username, first_name, last_name, now, user_id))
    
    conn.commit()
    conn.close()

def check_download_limit(user_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT plan, downloads_today, bonus_downloads FROM users WHERE user_id = ?", (user_id,))
    result = c.fetchone()
    conn.close()
    
    if not result:
        return True, 3
    
    plan, today, bonus = result
    bonus = bonus or 0
    limit = PLANS[plan]['limit'] + bonus
    
    return today < limit, limit - today

def increment_downloads(user_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    today = datetime.now().strftime("%Y-%m-%d")
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    c.execute('''UPDATE users SET 
        downloads_today = downloads_today + 1,
        total_downloads = total_downloads + 1,
        last_active = ?,
        last_download_date = ?
        WHERE user_id = ?''', (now, today, user_id))
    
    conn.commit()
    conn.close()

def get_user_plan(user_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT plan, plan_expiry FROM users WHERE user_id = ?", (user_id,))
    result = c.fetchone()
    conn.close()
    return result if result else ('basic', None)

def update_user_plan(user_id, plan):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    expiry = (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d")
    c.execute("UPDATE users SET plan = ?, plan_expiry = ? WHERE user_id = ?", (plan, expiry, user_id))
    conn.commit()
    conn.close()

def process_referral(new_user_id, ref_code):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    c.execute("SELECT user_id FROM users WHERE referral_code = ?", (ref_code,))
    referrer = c.fetchone()
    
    if referrer and referrer[0] != new_user_id:
        referrer_id = referrer[0]
        c.execute("UPDATE users SET referrer_id = ? WHERE user_id = ?", (referrer_id, new_user_id))
        c.execute('''UPDATE users SET 
            referral_count = referral_count + 1,
            bonus_downloads = bonus_downloads + 3
            WHERE user_id = ?''', (referrer_id,))
        conn.commit()
        conn.close()
        return referrer_id
    
    conn.close()
    return None

def get_referral_info(user_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT referral_code, referral_count, bonus_downloads FROM users WHERE user_id = ?", (user_id,))
    result = c.fetchone()
    conn.close()
    return result or (None, 0, 0)

def get_stats():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    c.execute("SELECT COUNT(*) FROM users")
    total = c.fetchone()[0]
    
    today = datetime.now().strftime("%Y-%m-%d")
    c.execute("SELECT COUNT(*) FROM users WHERE last_active LIKE ?", (f"{today}%",))
    active = c.fetchone()[0]
    
    c.execute("SELECT SUM(total_downloads) FROM users")
    downloads = c.fetchone()[0] or 0
    
    c.execute("SELECT plan, COUNT(*) FROM users GROUP BY plan")
    plans_stats = c.fetchall()
    
    conn.close()
    return total, active, downloads, plans_stats

# ========== СКАЧИВАНИЕ ВИДЕО ==========
async def download_video(url):
    try:
        ydl_opts = {
            **YDL_OPTIONS,
            'outtmpl': os.path.join(DOWNLOAD_DIR, '%(title)s_%(id)s.%(ext)s'),
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            filename = ydl.prepare_filename(info)
            return filename if os.path.exists(filename) else None
    except Exception as e:
        logger.error(f"Ошибка скачивания: {e}")
        return None

# ========== КОМАНДЫ ==========
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    args = context.args
    
    save_user(user.id, user.username, user.first_name, user.last_name)
    
    if args and args[0].startswith('ref_'):
        ref_code = args[0].replace('ref_', '')
        referrer = process_referral(user.id, ref_code)
        if referrer:
            await update.message.reply_text("🎉 Ты пришел по ссылке друга! +3 скачивания на сегодня!")
    
    text = (
        "🎬 *Бот для скачивания видео*\n\n"
        "Отправь ссылку на видео из:\n"
        "• TikTok • Instagram • YouTube\n\n"
        "Команды:\n"
        "/plan — тарифы\n"
        "/profile — профиль\n"
        "/ref — рефералы"
    )
    await update.message.reply_text(text, parse_mode='Markdown')

async def profile_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id
    
    save_user(user_id, user.username, user.first_name, user.last_name)
    
    plan, expiry = get_user_plan(user_id)
    plan_name = PLANS[plan]['name']
    
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT downloads_today, total_downloads, bonus_downloads, referral_count FROM users WHERE user_id = ?", (user_id,))
    data = c.fetchone()
    conn.close()
    
    today = data[0] if data else 0
    total = data[1] if data else 0
    bonus = data[2] if data else 0
    refs = data[3] if data else 0
    
    limit = PLANS[plan]['limit'] + bonus
    expiry_text = f"до {expiry}" if expiry else "бессрочно"
    
    text = (
        f"👤 *Профиль*\n\n"
        f"Тариф: {plan_name}\n"
        f"Действует: {expiry_text}\n"
        f"Сегодня: {today}/{limit}\n"
        f"Всего: {total}\n"
        f"Рефералы: {refs} (+{bonus}/день)"
    )
    
    keyboard = [
        [InlineKeyboardButton("💎 Тарифы", callback_data="plans")],
        [InlineKeyboardButton("👥 Рефералы", callback_data="ref")]
    ]
    await update.message.reply_text(text, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(keyboard))

async def plans_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.callback_query:
        query = update.callback_query
        await query.answer()
        user_id = query.from_user.id
        msg = query.message
        edit = True
    else:
        user_id = update.effective_user.id
        msg = update.message
        edit = False
    
    current, _ = get_user_plan(user_id)
    
    text = "💎 *Тарифы*\n\n"
    keyboard = []
    
    for pid, plan in PLANS.items():
        if pid == 'basic':
            continue
        text += f"{plan['name']}\n{plan['price']}★/мес\n"
        text += "\n".join([f"• {f}" for f in plan['features']]) + "\n\n"
        if pid != current:
            keyboard.append([InlineKeyboardButton(f"✅ Купить {plan['name']}", callback_data=f"buy_{pid}")])
    
    keyboard.append([InlineKeyboardButton("◀️ Назад", callback_data="back_profile")])
    
    if edit:
        await query.edit_message_text(text, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        await msg.reply_text(text, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(keyboard))

async def ref_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.callback_query:
        query = update.callback_query
        await query.answer()
        user_id = query.from_user.id
        msg = query.message
        edit = True
    else:
        user_id = update.effective_user.id
        msg = update.message
        edit = False
    
    code, count, bonus = get_referral_info(user_id)
    bot_username = (await context.bot.get_me()).username
    link = f"https://t.me/{bot_username}?start=ref_{code}"
    
    text = (
        f"👥 *Реферальная программа*\n\n"
        f"Твоя ссылка:\n`{link}`\n\n"
        f"Приглашено: {count}\n"
        f"Бонус: +{bonus}/день\n\n"
        f"За каждого друга +3 скачивания навсегда!"
    )
    
    keyboard = [[InlineKeyboardButton("◀️ Назад", callback_data="back_profile")]]
    
    if edit:
        await query.edit_message_text(text, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        await msg.reply_text(text, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(keyboard))

async def buy_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    plan_id = query.data.replace('buy_', '')
    plan = PLANS[plan_id]
    
    await context.bot.send_invoice(
        chat_id=query.from_user.id,
        title=plan['name'],
        description=", ".join(plan['features']),
        payload=f"sub_{plan_id}",
        provider_token="",
        currency="XTR",
        prices=[{"label": plan['name'], "amount": plan['price']}]
    )

async def pre_checkout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.pre_checkout_query.answer(ok=True)

async def payment_success(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    payload = update.message.successful_payment.invoice_payload
    
    if payload.startswith('sub_'):
        plan_id = payload.replace('sub_', '')
        update_user_plan(user_id, plan_id)
        await update.message.reply_text(f"✅ Тариф {PLANS[plan_id]['name']} активирован на 30 дней!")

async def back_profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    plan, expiry = get_user_plan(user_id)
    
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT downloads_today, total_downloads, bonus_downloads, referral_count FROM users WHERE user_id = ?", (user_id,))
    data = c.fetchone()
    conn.close()
    
    today = data[0] if data else 0
    total = data[1] if data else 0
    bonus = data[2] if data else 0
    refs = data[3] if data else 0
    
    limit = PLANS[plan]['limit'] + bonus
    expiry_text = f"до {expiry}" if expiry else "бессрочно"
    
    text = (
        f"👤 *Профиль*\n\n"
        f"Тариф: {PLANS[plan]['name']}\n"
        f"Действует: {expiry_text}\n"
        f"Сегодня: {today}/{limit}\n"
        f"Всего: {total}\n"
        f"Рефералы: {refs} (+{bonus}/день)"
    )
    
    keyboard = [
        [InlineKeyboardButton("💎 Тарифы", callback_data="plans")],
        [InlineKeyboardButton("👥 Рефералы", callback_data="ref")]
    ]
    await query.edit_message_text(text, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(keyboard))

async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("❌ Нет прав")
        return
    
    total, active, downloads, plans_stats = get_stats()
    
    text = f"📊 *Статистика*\n\n👤 Всего: {total}\n📱 Актив: {active}\n⬇️ Скачиваний: {downloads}\n\n"
    for plan, count in plans_stats:
        text += f"{PLANS[plan]['name']}: {count}\n"
    
    await update.message.reply_text(text, parse_mode='Markdown')

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    url = update.message.text.strip()
    
    save_user(user.id, user.username, user.first_name, user.last_name)
    
    can, left = check_download_limit(user.id)
    if not can:
        await update.message.reply_text("❌ Лимит на сегодня. Купи подписку /plan или приведи друзей /ref")
        return
    
    msg = await update.message.reply_text("⏳ Скачиваю...")
    
    try:
        filepath = await download_video(url)
        if not filepath:
            await msg.edit_text("❌ Не могу скачать. Проверь ссылку.")
            return
        
        if os.path.getsize(filepath) > 50 * 1024 * 1024:
            await msg.edit_text("❌ Видео больше 50MB")
            os.remove(filepath)
            return
        
        await msg.edit_text("📤 Отправляю...")
        with open(filepath, 'rb') as f:
            await update.message.reply_video(f)
        
        increment_downloads(user.id)
        os.remove(filepath)
        await msg.delete()
        
    except Exception as e:
        logger.error(f"Ошибка: {e}")
        await msg.edit_text("❌ Ошибка, попробуй другую ссылку")

# ========== ЗАПУСК ==========
def main():
    os.makedirs('/data', exist_ok=True)
    init_db()
    
    app = Application.builder().token(BOT_TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("profile", profile_cmd))
    app.add_handler(CommandHandler("plan", plans_cmd))
    app.add_handler(CommandHandler("ref", ref_cmd))
    app.add_handler(CommandHandler("stats", stats_cmd))
    
    app.add_handler(CallbackQueryHandler(plans_cmd, pattern="^plans$"))
    app.add_handler(CallbackQueryHandler(ref_cmd, pattern="^ref$"))
    app.add_handler(CallbackQueryHandler(back_profile, pattern="^back_profile$"))
    app.add_handler(CallbackQueryHandler(buy_callback, pattern="^buy_"))
    
    app.add_handler(PreCheckoutQueryHandler(pre_checkout))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, payment_success))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    logger.info("✅ Бот запущен")
    app.run_polling()

if __name__ == '__main__':
    main()