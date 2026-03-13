import os
import logging
import sqlite3
import time
import random
import csv
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

# Заблокированные пользователи
BANNED_USERS = set()

# ========== БАЗА ДАННЫХ ==========
DB_PATH = '/data/users.db'

def init_db():
    """Создание базы данных"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    c.execute('DROP TABLE IF EXISTS users')
    
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
        bonus_downloads INTEGER DEFAULT 0,
        is_banned INTEGER DEFAULT 0,
        mute_until TEXT
    )''')
    
    conn.commit()
    conn.close()
    logger.info("✅ База данных создана")

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
        referral_code = f"ref{user_id}{random.randint(100, 999)}"
        c.execute('''INSERT INTO users 
            (user_id, username, first_name, last_name, first_seen, last_active, 
             last_download_date, referral_code)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
            (user_id, username, first_name, last_name, now, now, today, referral_code))
    else:
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

# ========== АДМИН-КОМАНДЫ ==========

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Общая статистика"""
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("❌ Нет прав")
        return
    
    total, active, downloads, plans_stats = get_stats()
    
    text = f"📊 *Статистика*\n\n"
    text += f"👥 Всего: {total}\n"
    text += f"📱 Актив: {active}\n"
    text += f"⬇️ Скачиваний: {downloads}\n\n"
    text += f"💎 *Тарифы:*\n"
    
    for plan, count in plans_stats:
        text += f"{PLANS[plan]['name']}: {count}\n"
    
    await update.message.reply_text(text, parse_mode='Markdown')

async def whois_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Информация о пользователе"""
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("❌ Нет прав")
        return
    
    args = context.args
    if not args:
        await update.message.reply_text("Использование: /whois <user_id или @username>")
        return
    
    target = args[0]
    
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    if target.startswith('@'):
        username = target[1:]
        c.execute('''SELECT * FROM users WHERE username = ?''', (username,))
    else:
        try:
            user_id = int(target)
            c.execute('''SELECT * FROM users WHERE user_id = ?''', (user_id,))
        except:
            await update.message.reply_text("❌ Неверный формат ID")
            conn.close()
            return
    
    user = c.fetchone()
    
    if not user:
        await update.message.reply_text("❌ Пользователь не найден")
        conn.close()
        return
    
    text = f"""👤 *Информация о пользователе*

ID: `{user[0]}`
Username: @{user[1] or 'нет'}
Имя: {user[2]} {user[3] or ''}
Первый вход: {user[4]}
Последний вход: {user[5]}

📊 *Статистика:*
Тариф: {PLANS[user[8]]['name']}
Скачиваний сегодня: {user[6]}
Всего скачиваний: {user[10]}
Бонус: +{user[14]}/день
Рефералов: {user[13]}

{'🔴 ЗАБЛОКИРОВАН' if user[15] == 1 else '🟢 Активен'}"""
    
    conn.close()
    await update.message.reply_text(text, parse_mode='Markdown')

async def ban_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Заблокировать пользователя"""
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("❌ Нет прав")
        return
    
    args = context.args
    if not args:
        await update.message.reply_text("Использование: /ban <user_id>")
        return
    
    try:
        user_id = int(args[0])
    except:
        await update.message.reply_text("❌ Неверный ID")
        return
    
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("UPDATE users SET is_banned = 1 WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()
    
    BANNED_USERS.add(user_id)
    await update.message.reply_text(f"✅ Пользователь {user_id} заблокирован")

async def unban_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Разблокировать пользователя"""
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("❌ Нет прав")
        return
    
    args = context.args
    if not args:
        await update.message.reply_text("Использование: /unban <user_id>")
        return
    
    try:
        user_id = int(args[0])
    except:
        await update.message.reply_text("❌ Неверный ID")
        return
    
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("UPDATE users SET is_banned = 0 WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()
    
    if user_id in BANNED_USERS:
        BANNED_USERS.remove(user_id)
    
    await update.message.reply_text(f"✅ Пользователь {user_id} разблокирован")

async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Рассылка сообщения всем пользователям"""
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("❌ Нет прав")
        return
    
    text = ' '.join(context.args)
    if not text:
        await update.message.reply_text("Использование: /broadcast <текст сообщения>")
        return
    
    await update.message.reply_text("📢 Начинаю рассылку...")
    
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT user_id FROM users WHERE is_banned = 0")
    users = c.fetchall()
    conn.close()
    
    sent = 0
    failed = 0
    
    for (user_id,) in users:
        try:
            await context.bot.send_message(
                user_id,
                f"📢 *Сообщение от администратора:*\n\n{text}",
                parse_mode='Markdown'
            )
            sent += 1
            time.sleep(0.05)  # Защита от флуда
        except Exception as e:
            failed += 1
            logger.error(f"Ошибка отправки {user_id}: {e}")
    
    await update.message.reply_text(f"✅ Рассылка завершена\nОтправлено: {sent}\nОшибок: {failed}")

async def setplan_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Выдать тариф пользователю"""
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("❌ Нет прав")
        return
    
    args = context.args
    if len(args) < 2:
        await update.message.reply_text("Использование: /setplan <user_id> <plan>")
        return
    
    try:
        user_id = int(args[0])
        plan = args[1].lower()
        
        if plan not in PLANS:
            await update.message.reply_text(f"❌ Тариф должен быть: {', '.join(PLANS.keys())}")
            return
        
        update_user_plan(user_id, plan)
        await update.message.reply_text(f"✅ Пользователю {user_id} выдан тариф {PLANS[plan]['name']}")
        
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка: {e}")

async def addbonus_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Добавить бонусные скачивания"""
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("❌ Нет прав")
        return
    
    args = context.args
    if len(args) < 2:
        await update.message.reply_text("Использование: /addbonus <user_id> <количество>")
        return
    
    try:
        user_id = int(args[0])
        bonus = int(args[1])
        
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("UPDATE users SET bonus_downloads = bonus_downloads + ? WHERE user_id = ?", (bonus, user_id))
        conn.commit()
        conn.close()
        
        await update.message.reply_text(f"✅ Пользователю {user_id} добавлено +{bonus} скачиваний")
        
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка: {e}")

async def resetlimit_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Сбросить дневной лимит пользователя"""
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("❌ Нет прав")
        return
    
    args = context.args
    if not args:
        await update.message.reply_text("Использование: /resetlimit <user_id>")
        return
    
    try:
        user_id = int(args[0])
        
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("UPDATE users SET downloads_today = 0 WHERE user_id = ?", (user_id,))
        conn.commit()
        conn.close()
        
        await update.message.reply_text(f"✅ Лимит пользователя {user_id} сброшен")
        
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка: {e}")

async def backup_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Создать бэкап базы данных"""
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("❌ Нет прав")
        return
    
    try:
        # Создаем копию базы
        backup_path = f'/data/backup_{datetime.now().strftime("%Y%m%d_%H%M%S")}.db'
        
        conn = sqlite3.connect(DB_PATH)
        backup_conn = sqlite3.connect(backup_path)
        conn.backup(backup_conn)
        backup_conn.close()
        conn.close()
        
        # Отправляем файл
        with open(backup_path, 'rb') as f:
            await update.message.reply_document(
                document=f,
                filename=f"backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db",
                caption="✅ Бэкап базы данных"
            )
        
        # Удаляем временный файл
        os.remove(backup_path)
        
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка создания бэкапа: {e}")

async def export_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Экспорт пользователей в CSV"""
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("❌ Нет прав")
        return
    
    try:
        csv_path = f'/data/users_{datetime.now().strftime("%Y%m%d_%H%M%S")}.csv'
        
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute('''SELECT user_id, username, first_name, last_name, first_seen, 
                    last_active, total_downloads, plan, bonus_downloads, referral_count
                    FROM users''')
        users = c.fetchall()
        conn.close()
        
        with open(csv_path, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(['ID', 'Username', 'Имя', 'Фамилия', 'Первый вход', 
                            'Последний вход', 'Всего скачано', 'Тариф', 'Бонус', 'Рефералов'])
            writer.writerows(users)
        
        with open(csv_path, 'rb') as f:
            await update.message.reply_document(
                document=f,
                filename=f"users_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
                caption="✅ Экспорт пользователей"
            )
        
        os.remove(csv_path)
        
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка экспорта: {e}")

async def log_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Получить логи"""
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("❌ Нет прав")
        return
    
    # В Amvera логи можно получить только через интерфейс
    await update.message.reply_text(
        "📋 Для получения логов:\n"
        "1. Зайди в Amvera\n"
        "2. Открой вкладку 'Лог приложения'\n"
        "3. Скопируй нужные строки"
    )

async def ping_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Проверка задержки"""
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("❌ Нет прав")
        return
    
    start = time.time()
    msg = await update.message.reply_text("🏓 Pong...")
    end = time.time()
    
    await msg.edit_text(f"🏓 Pong!\nЗадержка: {round((end - start) * 1000)}ms")

async def restart_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Перезапуск бота"""
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("❌ Нет прав")
        return
    
    await update.message.reply_text("🔄 Перезапускаюсь...")
    logger.info("Перезапуск по команде админа")
    
    # Выходим из приложения - хостинг перезапустит автоматически
    os._exit(0)

# ========== ОСНОВНЫЕ КОМАНДЫ ==========
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
        "/ref — рефералы\n"
        "/help — помощь"
    )
    await update.message.reply_text(text, parse_mode='Markdown')

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "📖 *Помощь*\n\n"
        "🔹 *Для всех:*\n"
        "/start — начало\n"
        "/profile — профиль\n"
        "/plan — тарифы\n"
        "/ref — рефералы\n\n"
        "🔹 *Как скачать:*\n"
        "1. Найди ссылку на видео\n"
        "2. Отправь её мне\n"
        "3. Получи видео"
    )
    
    if update.effective_user.id == ADMIN_ID:
        text += "\n\n🔹 *Админ-команды:*\n"
        text += "/stats — статистика\n"
        text += "/whois — инфо о пользователе\n"
        text += "/ban — заблокировать\n"
        text += "/unban — разблокировать\n"
        text += "/broadcast — рассылка\n"
        text += "/setplan — выдать тариф\n"
        text += "/addbonus — добавить бонус\n"
        text += "/resetlimit — сбросить лимит\n"
        text += "/backup — бэкап БД\n"
        text += "/export — экспорт CSV\n"
        text += "/ping — проверка\n"
        text += "/restart — перезапуск"
    
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

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id
    url = update.message.text.strip()
    
    # Проверка бана
    if user_id in BANNED_USERS:
        await update.message.reply_text("❌ Вы заблокированы")
        return
    
    save_user(user_id, user.username, user.first_name, user.last_name)
    
    can, left = check_download_limit(user_id)
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
        
        increment_downloads(user_id)
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
    
    # Основные команды
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("profile", profile_cmd))
    app.add_handler(CommandHandler("plan", plans_cmd))
    app.add_handler(CommandHandler("ref", ref_cmd))
    
    # Админ-команды
    app.add_handler(CommandHandler("stats", stats_command))
    app.add_handler(CommandHandler("whois", whois_command))
    app.add_handler(CommandHandler("ban", ban_command))
    app.add_handler(CommandHandler("unban", unban_command))
    app.add_handler(CommandHandler("broadcast", broadcast_command))
    app.add_handler(CommandHandler("setplan", setplan_command))
    app.add_handler(CommandHandler("addbonus", addbonus_command))
    app.add_handler(CommandHandler("resetlimit", resetlimit_command))
    app.add_handler(CommandHandler("backup", backup_command))
    app.add_handler(CommandHandler("export", export_command))
    app.add_handler(CommandHandler("log", log_command))
    app.add_handler(CommandHandler("ping", ping_command))
    app.add_handler(CommandHandler("restart", restart_command))
    
    # Callback-обработчики
    app.add_handler(CallbackQueryHandler(plans_cmd, pattern="^plans$"))
    app.add_handler(CallbackQueryHandler(ref_cmd, pattern="^ref$"))
    app.add_handler(CallbackQueryHandler(back_profile, pattern="^back_profile$"))
    app.add_handler(CallbackQueryHandler(buy_callback, pattern="^buy_"))
    
    # Платежи
    app.add_handler(PreCheckoutQueryHandler(pre_checkout))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, payment_success))
    
    # Сообщения
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    logger.info("✅ Бот с полным набором команд запущен")
    app.run_polling()

if __name__ == '__main__':
    main()