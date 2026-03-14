import os
import logging
import sqlite3
import time
import random
import json
import csv
import aiohttp
import asyncio
import urllib.parse
import base64
from datetime import datetime, timedelta
import yt_dlp
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler, PreCheckoutQueryHandler
from telegram.constants import ParseMode
from io import BytesIO

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
    raise ValueError("❌ BOT_TOKEN не найден!")

# Твой Telegram ID (админ)
ADMIN_ID = 920343231

# ========== API КЛЮЧИ ==========
DEEPSEEK_API_KEY = os.environ.get('DEEPSEEK_API_KEY')

# Логируем наличие ключей
if DEEPSEEK_API_KEY:
    logger.info(f"✅ DeepSeek ключ найден: {DEEPSEEK_API_KEY[:10]}...")
else:
    logger.error("❌ DeepSeek ключ НЕ НАЙДЕН!")

# ========== НАСТРОЙКИ API ==========
IMAGE_STYLES = {
    'realistic': 'фотореализм, высокое качество, 4k, детализировано',
    'artistic': 'художественный стиль, арт, креативно, абстрактно',
    'cartoon': 'мультяшный стиль, анимация, яркие цвета, дисней',
    'sketch': 'скетч, набросок карандашом, черно-белый, эскиз'
}

# ========== НАСТРОЙКИ СКАЧИВАНИЯ ==========
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
        'download_limit': 3,
        'ai_limit': 5,
        'image_limit': 2,
        'features': ['3 видео/день', '5 AI-запросов/день', '2 изображения/день']
    },
    'starter': {
        'name': '🔸 Стартовый',
        'price': 25,
        'download_limit': 30,
        'ai_limit': 50,
        'image_limit': 20,
        'features': ['30 видео/день', '50 AI-запросов/день', '20 изображений/день', 'Приоритет']
    },
    'premium': {
        'name': '💎 Премиум',
        'price': 50,
        'download_limit': 999999,
        'ai_limit': 999999,
        'image_limit': 999999,
        'features': ['Безлимитные видео', 'Безлимитный AI', 'Безлимитные изображения', 'Приоритет 24/7']
    }
}

# ========== БАЗА ДАННЫХ ==========
DB_PATH = '/data/users.db'

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        first_name TEXT,
        last_name TEXT,
        first_seen TEXT,
        last_active TEXT,
        downloads_today INTEGER DEFAULT 0,
        ai_requests_today INTEGER DEFAULT 0,
        image_generations_today INTEGER DEFAULT 0,
        last_download_date TEXT,
        last_ai_date TEXT,
        last_image_date TEXT,
        plan TEXT DEFAULT 'basic',
        plan_expiry TEXT,
        total_downloads INTEGER DEFAULT 0,
        total_ai_requests INTEGER DEFAULT 0,
        total_images INTEGER DEFAULT 0,
        referrer_id INTEGER DEFAULT NULL,
        referral_code TEXT UNIQUE,
        referral_count INTEGER DEFAULT 0,
        bonus_downloads INTEGER DEFAULT 0,
        bonus_ai INTEGER DEFAULT 0,
        bonus_images INTEGER DEFAULT 0,
        is_banned INTEGER DEFAULT 0,
        mute_until TEXT
    )''')
    conn.commit()
    conn.close()
    logger.info("✅ База данных создана/проверена")

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
             last_download_date, last_ai_date, last_image_date, referral_code)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
            (user_id, username, first_name, last_name, now, now, today, today, today, referral_code))
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
    limit = PLANS[plan]['download_limit'] + bonus
    return today < limit, limit - today

def check_ai_limit(user_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT plan, ai_requests_today, bonus_ai FROM users WHERE user_id = ?", (user_id,))
    result = c.fetchone()
    conn.close()
    if not result:
        return True, 5
    plan, today, bonus = result
    bonus = bonus or 0
    limit = PLANS[plan]['ai_limit'] + bonus
    return today < limit, limit - today

def check_image_limit(user_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT plan, image_generations_today, bonus_images FROM users WHERE user_id = ?", (user_id,))
    result = c.fetchone()
    conn.close()
    if not result:
        return True, 2
    plan, today, bonus = result
    bonus = bonus or 0
    limit = PLANS[plan]['image_limit'] + bonus
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

def increment_ai_request(user_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    today = datetime.now().strftime("%Y-%m-%d")
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    c.execute('''UPDATE users SET 
        ai_requests_today = ai_requests_today + 1,
        total_ai_requests = total_ai_requests + 1,
        last_active = ?,
        last_ai_date = ?
        WHERE user_id = ?''', (now, today, user_id))
    conn.commit()
    conn.close()

def increment_image_generation(user_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    today = datetime.now().strftime("%Y-%m-%d")
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    c.execute('''UPDATE users SET 
        image_generations_today = image_generations_today + 1,
        total_images = total_images + 1,
        last_active = ?,
        last_image_date = ?
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
            bonus_downloads = bonus_downloads + 3,
            bonus_ai = bonus_ai + 2,
            bonus_images = bonus_images + 1
            WHERE user_id = ?''', (referrer_id,))
        conn.commit()
        conn.close()
        return referrer_id
    conn.close()
    return None

def get_referral_info(user_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT referral_code, referral_count, bonus_downloads, bonus_ai, bonus_images FROM users WHERE user_id = ?", (user_id,))
    result = c.fetchone()
    conn.close()
    return result or (None, 0, 0, 0, 0)

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
    c.execute("SELECT SUM(total_ai_requests) FROM users")
    ai_requests = c.fetchone()[0] or 0
    c.execute("SELECT SUM(total_images) FROM users")
    images = c.fetchone()[0] or 0
    c.execute("SELECT plan, COUNT(*) FROM users GROUP BY plan")
    plans_stats = c.fetchall()
    conn.close()
    return total, active, downloads, ai_requests, images, plans_stats

# ========== ФУНКЦИЯ ГЕНЕРАЦИИ КАРТИНОК ==========
async def generate_image(prompt, style='realistic'):
    """Генерация изображения через Pollinations"""
    try:
        style_prompt = f"{prompt}, {IMAGE_STYLES[style]}"
        logger.info(f"🎨 Генерирую: {style_prompt[:50]}...")
        
        encoded_prompt = urllib.parse.quote(style_prompt)
        url = f"https://image.pollinations.ai/prompt/{encoded_prompt}?width=1024&height=1024&nologo=true"
        
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=60) as resp:
                if resp.status == 200:
                    image_data = await resp.read()
                    if len(image_data) > 1000:
                        logger.info(f"✅ Pollinations успешно")
                        return image_data
                    else:
                        logger.warning("Слишком маленький ответ")
                        return None
                else:
                    logger.error(f"Pollinations ошибка {resp.status}")
                    return None
    except Exception as e:
        logger.error(f"Ошибка генерации: {e}")
        return None

# ========== ФУНКЦИЯ AI-АССИСТЕНТА (ТОЛЬКО DEEPSEEK) ==========
async def ask_ai(prompt, user_id):
    """Запрос к DeepSeek API"""
    can, left = check_ai_limit(user_id)
    if not can:
        return "❌ Ты исчерпал лимит AI-запросов на сегодня."
    
    if not DEEPSEEK_API_KEY:
        logger.error("❌ Ключ DeepSeek не найден")
        return "❌ Ошибка: ключ DeepSeek не найден. Добавь его в переменные окружения."
    
    logger.info(f"🤖 Отправляю запрос в DeepSeek: {prompt[:50]}...")
    
    try:
        async with aiohttp.ClientSession() as session:
            url = "https://api.deepseek.com/v1/chat/completions"
            headers = {
                "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
                "Content-Type": "application/json"
            }
            
            payload = {
                "model": "deepseek-chat",
                "messages": [
                    {"role": "system", "content": "Ты полезный ассистент. Отвечай кратко и по делу."},
                    {"role": "user", "content": prompt}
                ],
                "temperature": 0.7,
                "max_tokens": 800
            }
            
            async with session.post(url, json=payload, headers=headers, timeout=30) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if 'choices' in data and len(data['choices']) > 0:
                        result = data['choices'][0]['message']['content']
                        increment_ai_request(user_id)
                        logger.info("✅ DeepSeek успешно ответил")
                        return result
                    else:
                        return "❌ Ошибка формата ответа от DeepSeek"
                elif resp.status == 401:
                    return "❌ Ошибка 401: Неверный ключ DeepSeek"
                elif resp.status == 402:
                    return "❌ Ошибка 402: Недостаточно средств на балансе DeepSeek"
                elif resp.status == 429:
                    return "❌ Слишком много запросов к DeepSeek. Попробуй позже."
                else:
                    error_text = await resp.text()
                    logger.error(f"DeepSeek ошибка {resp.status}: {error_text}")
                    return f"❌ Ошибка DeepSeek: {resp.status}"
                    
    except asyncio.TimeoutError:
        logger.error("Таймаут DeepSeek")
        return "❌ Таймаут при обращении к DeepSeek"
    except Exception as e:
        logger.error(f"DeepSeek ошибка: {e}")
        return f"❌ Ошибка соединения: {str(e)[:100]}"

# ========== КОМАНДА ДЛЯ ПРОВЕРКИ КЛЮЧА ==========
async def check_key_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Проверка наличия ключа DeepSeek"""
    if update.effective_user.id != ADMIN_ID:
        return
    
    if DEEPSEEK_API_KEY:
        await update.message.reply_text(
            f"✅ DeepSeek ключ найден!\n"
            f"Начинается с: {DEEPSEEK_API_KEY[:10]}...\n"
            f"Длина: {len(DEEPSEEK_API_KEY)} символов"
        )
    else:
        await update.message.reply_text(
            "❌ DeepSeek ключ НЕ НАЙДЕН!\n"
            "Проверь переменные окружения в Averma"
        )

# ========== ОБРАБОТЧИКИ КОМАНД ==========
async def generate_image_with_style_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает меню выбора стиля"""
    if update.callback_query:
        query = update.callback_query
        await query.answer()
        prompt = context.user_data.get('pending_prompt', '')
        msg = query.message
    else:
        prompt = ' '.join(context.args) if context.args else None
        msg = update.message
    
    if not prompt:
        await msg.reply_text(
            "❓ Использование: /draw <описание>\n\nПримеры:\n/draw кот в космосе\n/draw футуристический город",
            parse_mode='Markdown'
        )
        return
    
    context.user_data['pending_prompt'] = prompt
    keyboard = [
        [InlineKeyboardButton("📸 Фотореализм", callback_data="style_realistic")],
        [InlineKeyboardButton("🎨 Арт", callback_data="style_artistic")],
        [InlineKeyboardButton("🧸 Мультяшный", callback_data="style_cartoon")],
        [InlineKeyboardButton("✏️ Скетч", callback_data="style_sketch")],
        [InlineKeyboardButton("🎭 Без стиля", callback_data="style_none")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await msg.reply_text(
        f"🎨 *Выбери стиль для:*\n\n«{prompt}»",
        parse_mode='Markdown',
        reply_markup=reply_markup
    )

async def generate_image_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка выбора стиля"""
    query = update.callback_query
    await query.answer()
    user = update.effective_user
    user_id = user.id
    prompt = context.user_data.get('pending_prompt', '')
    
    if not prompt:
        await query.edit_message_text("❌ Ошибка: промпт не найден. Попробуй /draw заново.")
        return
    
    style = query.data.replace('style_', '')
    can, left = check_image_limit(user_id)
    if not can:
        await query.edit_message_text(
            "❌ *Лимит исчерпан*\n\nКупи подписку /plan или приведи друзей /ref для увеличения лимита!",
            parse_mode='Markdown'
        )
        return
    
    status_msg = await query.edit_message_text(
        f"🎨 *Генерирую...*\n\n«{prompt}»\n⏳ Это займет несколько секунд",
        parse_mode='Markdown'
    )
    
    image_data = await generate_image(prompt, style if style != 'none' else 'realistic')
    
    if not image_data:
        await status_msg.edit_text(
            "⚠️ *Не удалось сгенерировать*\n\nСервис временно перегружен. Попробуй позже.",
            parse_mode='Markdown'
        )
        return
    
    try:
        await context.bot.send_photo(
            chat_id=user_id,
            photo=BytesIO(image_data),
            caption=f"🖼️ *Готово!*\n\n«{prompt}»",
            parse_mode='Markdown'
        )
        increment_image_generation(user_id)
        await status_msg.delete()
    except Exception as e:
        logger.error(f"Ошибка отправки: {e}")
        await status_msg.edit_text("❌ Ошибка при отправке.")

async def ask_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда для AI"""
    user = update.effective_user
    user_id = user.id
    save_user(user_id, user.username, user.first_name, user.last_name)
    
    query = ' '.join(context.args) if context.args else None
    if not query:
        await update.message.reply_text(
            "❓ Напиши вопрос после /ask\n\nПример: /ask как придумать идею для видео?",
            parse_mode='Markdown'
        )
        return
    
    status_msg = await update.message.reply_text("🤔 Думаю...")
    response = await ask_ai(query, user_id)
    await status_msg.edit_text(f"🤖 *AI-ассистент:*\n\n{response}", parse_mode='Markdown')

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Стартовая команда"""
    user = update.effective_user
    args = context.args
    save_user(user.id, user.username, user.first_name, user.last_name)
    
    if args and args[0].startswith('ref_'):
        ref_code = args[0].replace('ref_', '')
        referrer = process_referral(user.id, ref_code)
        if referrer:
            await update.message.reply_text(
                "🎉 *Ты пришел по ссылке друга!*\n\n✨ Ты получил +3 скачивания на сегодня!",
                parse_mode='Markdown'
            )
    
    text = (
        "🎬 *TikTokSavebot*\n\n"
        "📥 *Скачивание видео:* просто отправь ссылку\n"
        "🎨 *Генерация картинок:* /draw описание\n"
        "🤖 *AI-ассистент:* /ask вопрос или просто текст\n"
        "📋 /plan — тарифы\n"
        "/profile — профиль\n"
        "/ref — рефералы\n"
        "/help — помощь"
    )
    await update.message.reply_text(text, parse_mode='Markdown')

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Помощь"""
    user_id = update.effective_user.id
    text = (
        "📖 *Помощь*\n\n"
        "🔹 *Основные команды:*\n"
        "/start — начало\n"
        "/help — эта справка\n"
        "/profile — твой профиль\n"
        "/plan — тарифы\n"
        "/ref — рефералы\n\n"
        "🔹 *Генерация картинок:*\n"
        "/draw кот в космосе — создать картинку\n\n"
        "🔹 *AI-ассистент:*\n"
        "/ask вопрос — задай вопрос\n"
        "Или просто отправь текст\n\n"
        "🔹 *Скачивание видео:*\n"
        "Отправь ссылку на видео из TikTok, Instagram, YouTube"
    )
    
    if user_id == ADMIN_ID:
        text += "\n\n🔹 *Админ-команды:*\n"
        text += "/stats — статистика\n"
        text += "/whois — инфо о пользователе\n"
        text += "/ban /unban — блокировка\n"
        text += "/broadcast — рассылка\n"
        text += "/setplan — выдать тариф\n"
        text += "/addbonus — добавить бонусы\n"
        text += "/resetlimit — сбросить лимиты\n"
        text += "/backup — бэкап\n"
        text += "/export — экспорт CSV\n"
        text += "/ping — проверка\n"
        text += "/restart — перезапуск\n"
        text += "/checkkey — проверить ключ"
    
    await update.message.reply_text(text, parse_mode='Markdown')

# ========== АДМИН-КОМАНДЫ (сокращенно) ==========
async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    total, active, downloads, ai_requests, images, plans_stats = get_stats()
    text = f"📊 Статистика\nВсего: {total}\nАктивных: {active}\nСкачиваний: {downloads}\nAI: {ai_requests}\nКартинок: {images}"
    await update.message.reply_text(text)

async def whois_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    await update.message.reply_text("Команда whois")

async def ban_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    await update.message.reply_text("Команда ban")

async def unban_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    await update.message.reply_text("Команда unban")

async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    await update.message.reply_text("Команда broadcast")

async def setplan_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    await update.message.reply_text("Команда setplan")

async def addbonus_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    await update.message.reply_text("Команда addbonus")

async def resetlimit_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    await update.message.reply_text("Команда resetlimit")

async def backup_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    await update.message.reply_text("Команда backup")

async def export_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    await update.message.reply_text("Команда export")

async def ping_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    start = time.time()
    msg = await update.message.reply_text("🏓 Pong...")
    end = time.time()
    await msg.edit_text(f"🏓 Pong! {round((end-start)*1000)}ms")

async def restart_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    await update.message.reply_text("🔄 Перезапуск...")
    os._exit(0)

# ========== ПРОФИЛЬ И ТАРИФЫ (упрощенно) ==========
async def profile_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id
    save_user(user_id, user.username, user.first_name, user.last_name)
    plan, expiry = get_user_plan(user_id)
    await update.message.reply_text(f"👤 Профиль\nТариф: {PLANS[plan]['name']}")

async def plans_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.callback_query:
        query = update.callback_query
        await query.answer()
        await query.edit_message_text("💎 Тарифы\n\nБазовый - 0★\nСтартовый - 25★\nПремиум - 50★")
    else:
        await update.message.reply_text("💎 Тарифы\n\nБазовый - 0★\nСтартовый - 25★\nПремиум - 50★")

async def ref_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.callback_query:
        query = update.callback_query
        await query.answer()
    user_id = update.effective_user.id
    code, count, bonus_d, bonus_ai, bonus_img = get_referral_info(user_id)
    await update.message.reply_text(f"👥 Рефералы\nПриглашено: {count}\nБонусы: +{bonus_d} видео, +{bonus_ai} AI, +{bonus_img} картинок")

async def buy_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer("Покупка временно недоступна")

async def pre_checkout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.pre_checkout_query.answer(ok=True)

async def payment_success(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("✅ Спасибо за покупку!")

async def back_profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await profile_cmd(update, context)

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

async def analyze_video_url(url):
    try:
        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'extract_flat': True,
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            title = info.get('title', 'Название не найдено')
            duration = info.get('duration', 0)
            uploader = info.get('uploader', 'Неизвестный автор')
            minutes = duration // 60
            seconds = duration % 60
            return f"""📹 Информация о видео\n\nНазвание: {title}\nАвтор: {uploader}\nДлительность: {minutes}:{seconds:02d}"""
    except:
        return None

# ========== ОБРАБОТКА СООБЩЕНИЙ ==========
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id
    text = update.message.text.strip()
    
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT is_banned FROM users WHERE user_id = ?", (user_id,))
    result = c.fetchone()
    conn.close()
    
    if result and result[0] == 1:
        await update.message.reply_text("❌ Вы заблокированы")
        return
    
    save_user(user_id, user.username, user.first_name, user.last_name)
    
    if 'http://' in text or 'https://' in text or 'www.' in text:
        can, left = check_download_limit(user_id)
        if not can:
            await update.message.reply_text("❌ Лимит скачиваний исчерпан")
            return
        
        info = await analyze_video_url(text)
        if info:
            await update.message.reply_text(info)
        
        msg = await update.message.reply_text("⏳ Скачиваю видео...")
        
        try:
            filepath = await download_video(text)
            if not filepath:
                await msg.edit_text("❌ Не могу скачать")
                return
            
            if os.path.getsize(filepath) > 50 * 1024 * 1024:
                await msg.edit_text("❌ Видео больше 50MB")
                os.remove(filepath)
                return
            
            with open(filepath, 'rb') as f:
                await update.message.reply_video(f)
            
            increment_downloads(user_id)
            os.remove(filepath)
            await msg.delete()
            
        except Exception as e:
            logger.error(f"Ошибка: {e}")
            await msg.edit_text("❌ Ошибка")
    
    else:
        can, left = check_ai_limit(user_id)
        if not can:
            await update.message.reply_text("❌ Лимит AI исчерпан")
            return
        
        msg = await update.message.reply_text("🤔 Думаю...")
        response = await ask_ai(text, user_id)
        await msg.edit_text(f"🤖 {response}")

# ========== ЗАПУСК ==========
def main():
    os.makedirs('/data', exist_ok=True)
    init_db()
    app = Application.builder().token(BOT_TOKEN).build()
    
    # Основные команды
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("draw", generate_image_with_style_selection))
    app.add_handler(CommandHandler("ask", ask_command))
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
    app.add_handler(CommandHandler("ping", ping_command))
    app.add_handler(CommandHandler("restart", restart_command))
    app.add_handler(CommandHandler("checkkey", check_key_command))
    
    # Callback-обработчики
    app.add_handler(CallbackQueryHandler(plans_cmd, pattern="^plans$"))
    app.add_handler(CallbackQueryHandler(ref_cmd, pattern="^ref$"))
    app.add_handler(CallbackQueryHandler(back_profile, pattern="^back_profile$"))
    app.add_handler(CallbackQueryHandler(buy_callback, pattern="^buy_"))
    app.add_handler(CallbackQueryHandler(generate_image_callback, pattern="^style_"))
    
    # Платежи
    app.add_handler(PreCheckoutQueryHandler(pre_checkout))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, payment_success))
    
    # Сообщения
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    logger.info("✅ Бот запущен")
    app.run_polling()

if __name__ == '__main__':
    main()