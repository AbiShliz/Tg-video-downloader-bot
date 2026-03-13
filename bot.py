import os
import logging
import sqlite3
import time
import random
import json
import csv
import aiohttp
import asyncio
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
    raise ValueError("BOT_TOKEN не найден!")

# Твой Telegram ID (админ)
ADMIN_ID = 920343231  # ТВОЙ ID

# ========== НАСТРОЙКИ API ==========
# Для генерации изображений (бесплатные варианты)
POLLINATIONS_API = "https://image.pollinations.ai/prompt/"  # Бесплатный API без ключа [citation:10]
GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY', '')  # Опционально

# Настройки для разных моделей генерации
IMAGE_STYLES = {
    'realistic': 'фотореализм, высокое качество, 4k',
    'artistic': 'художественный стиль, арт, креативно',
    'cartoon': 'мультяшный стиль, анимация, яркие цвета',
    'sketch': 'скетч, набросок карандашом, черно-белый'
}

# Лимиты генерации для разных тарифов
IMAGE_LIMITS = {
    'basic': 2,      # 2 генерации в день
    'starter': 20,    # 20 генераций в день
    'premium': 999999  # безлимитно
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
        'features': ['3 видео/день', '5 AI-запросов/день', '2 изображения/день', '480p']
    },
    'starter': {
        'name': '🔸 Стартовый',
        'price': 25,
        'download_limit': 30,
        'ai_limit': 50,
        'image_limit': 20,
        'features': ['30 видео/день', '50 AI-запросов/день', '20 изображений/день', '720p', 'Приоритет']
    },
    'premium': {
        'name': '💎 Премиум',
        'price': 50,
        'download_limit': 999999,
        'ai_limit': 999999,
        'image_limit': 999999,
        'features': ['Безлимитные видео', 'Безлимитный AI', 'Безлимитные изображения', '4K', 'Приоритет 24/7']
    }
}

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
    logger.info("✅ База данных создана с Image-колонками")

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
    """Проверка лимита генерации изображений"""
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
    """Увеличить счетчик генераций изображений"""
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

# ========== ФУНКЦИИ ГЕНЕРАЦИИ ИЗОБРАЖЕНИЙ ==========

async def generate_image(prompt, style='realistic'):
    """Генерация изображения через бесплатный Pollinations API [citation:10]"""
    try:
        # Добавляем стиль к промпту
        style_prompt = f"{prompt}, {IMAGE_STYLES[style]}"
        
        # Кодируем промпт для URL
        import urllib.parse
        encoded_prompt = urllib.parse.quote(style_prompt)
        
        # Формируем URL с параметрами
        image_url = f"{POLLINATIONS_API}{encoded_prompt}?width=1024&height=1024&nologo=true&model=flux"
        
        # Скачиваем изображение
        async with aiohttp.ClientSession() as session:
            async with session.get(image_url) as resp:
                if resp.status == 200:
                    image_data = await resp.read()
                    return image_data
                else:
                    logger.error(f"Pollinations error: {resp.status}")
                    return None
    except Exception as e:
        logger.error(f"Image generation error: {e}")
        return None

async def generate_image_with_style_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает меню выбора стиля перед генерацией"""
    query = update.callback_query
    if query:
        await query.answer()
        prompt = context.user_data.get('pending_prompt', '')
        msg = query.message
        edit = True
    else:
        prompt = ' '.join(context.args) if context.args else None
        msg = update.message
        edit = False
    
    if not prompt:
        await msg.reply_text(
            "❓ Использование: /draw <описание>\n\n"
            "Примеры:\n"
            "/draw кот в космосе\n"
            "/draw футуристический город, неон\n"
            "Или просто отправь описание после команды!"
        )
        return
    
    # Сохраняем промпт в контексте
    context.user_data['pending_prompt'] = prompt
    
    # Создаем клавиатуру с выбором стиля
    keyboard = [
        [InlineKeyboardButton("📸 Фотореализм", callback_data="style_realistic")],
        [InlineKeyboardButton("🎨 Арт", callback_data="style_artistic")],
        [InlineKeyboardButton("🧸 Мультяшный", callback_data="style_cartoon")],
        [InlineKeyboardButton("✏️ Скетч", callback_data="style_sketch")],
        [InlineKeyboardButton("🎭 Без стиля (как есть)", callback_data="style_none")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await msg.reply_text(
        f"🎨 *Выбери стиль для:*\n\n_{prompt}_",
        parse_mode='Markdown',
        reply_markup=reply_markup
    )

async def generate_image_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка выбора стиля и генерация"""
    query = update.callback_query
    await query.answer()
    
    user = update.effective_user
    user_id = user.id
    
    # Получаем промпт из контекста
    prompt = context.user_data.get('pending_prompt', '')
    if not prompt:
        await query.edit_message_text("❌ Ошибка: промпт не найден. Попробуй /draw заново.")
        return
    
    # Определяем стиль
    style = query.data.replace('style_', '')
    if style == 'none':
        style_prompt = prompt
    else:
        style_prompt = f"{prompt}, {IMAGE_STYLES[style]}"
    
    # Проверяем лимит
    can, left = check_image_limit(user_id)
    if not can:
        await query.edit_message_text(
            "❌ Ты исчерпал лимит генераций на сегодня.\n"
            "Купи подписку /plan или приведи друзей /ref для увеличения лимита!"
        )
        return
    
    # Сообщение о начале генерации
    await query.edit_message_text("🎨 Генерирую изображение... Это займет несколько секунд.")
    
    try:
        # Генерируем изображение
        image_data = await generate_image(prompt, style if style != 'none' else 'realistic')
        
        if not image_data:
            await query.edit_message_text("❌ Не удалось сгенерировать изображение. Попробуй позже.")
            return
        
        # Отправляем изображение
        await context.bot.send_photo(
            chat_id=user_id,
            photo=BytesIO(image_data),
            caption=f"🖼️ *Твоя генерация:*\n_{prompt}_\n\n✨ Стиль: {style}",
            parse_mode='Markdown'
        )
        
        # Увеличиваем счетчик
        increment_image_generation(user_id)
        
        # Удаляем сообщение с прогрессом
        await query.delete_message()
        
        # Проверяем остаток
        _, left = check_image_limit(user_id)
        if left < 3:
            await context.bot.send_message(
                chat_id=user_id,
                text=f"⚠️ У тебя осталось {left} генераций сегодня.\nПриведи друга /ref, чтобы получить больше!"
            )
        
    except Exception as e:
        logger.error(f"Image generation error: {e}")
        await query.edit_message_text("❌ Ошибка при генерации. Попробуй другую фразу или позже.")

# ========== AI-ФУНКЦИИ ==========
async def ask_ai(prompt, user_id, model_type='chat'):
    """Запрос к AI"""
    can, left = check_ai_limit(user_id)
    if not can:
        return "❌ Ты исчерпал лимит AI-запросов на сегодня. Купи подписку /plan или приведи друзей /ref для увеличения лимита!"
    
    # Здесь можно подключить любой AI API
    # Пока возвращаем заглушку
    increment_ai_request(user_id)
    return f"🤖 *AI-ассистент:*\n\nЯ получил твой запрос: '{prompt}'\n\n(Для полноценной работы AI нужно подключить API)"

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
    """Анализ ссылки на видео"""
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
            
            return f"""📹 *Информация о видео*

**Название:** {title}
**Автор:** {uploader}
**Длительность:** {minutes}:{seconds:02d}

Хочешь скачать? Просто отправь ссылку еще раз!"""
    except:
        return None

# ========== ОСНОВНЫЕ КОМАНДЫ ==========
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    args = context.args
    
    save_user(user.id, user.username, user.first_name, user.last_name)
    
    if args and args[0].startswith('ref_'):
        ref_code = args[0].replace('ref_', '')
        referrer = process_referral(user.id, ref_code)
        if referrer:
            await update.message.reply_text(
                "🎉 Ты пришел по ссылке друга!\n"
                "✨ Ты получил +3 скачивания на сегодня!\n"
                "🤖 А твой друг получил +3 скачивания, +2 AI и +1 генерацию навсегда!"
            )
    
    text = (
        "🎬 *TikTokSavebot — Скачивай, общайся и твори*\n\n"
        "📥 *Скачивание видео:*\n"
        "Просто отправь ссылку на видео из TikTok, Instagram, YouTube\n\n"
        "🎨 *Генерация изображений:*\n"
        "/draw [описание] — создать картинку по тексту\n\n"
        "🤖 *AI-ассистент:*\n"
        "/ask [вопрос] — задай вопрос или попроси написать текст\n\n"
        "📋 *Команды:*\n"
        "/draw — генерация картинки\n"
        "/ask — спросить AI\n"
        "/plan — тарифы\n"
        "/profile — профиль\n"
        "/ref — рефералы (+3 видео, +2 AI, +1 картинка за друга)\n"
        "/help — помощь"
    )
    await update.message.reply_text(text, parse_mode='Markdown')

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    text = (
        "📖 *Помощь*\n\n"
        "🔹 *Для всех:*\n"
        "/start — начало\n"
        "/draw [описание] — создать изображение\n"
        "/ask [вопрос] — спросить AI\n"
        "/profile — профиль\n"
        "/plan — тарифы\n"
        "/ref — рефералы (+3 видео, +2 AI, +1 картинка за друга)\n\n"
        "🔹 *Как скачать видео:*\n"
        "1. Найди ссылку на видео\n"
        "2. Отправь её мне\n"
        "3. Получи видео\n\n"
        "🔹 *Как создать изображение:*\n"
        "1. Напиши /draw кот в космосе\n"
        "2. Выбери стиль\n"
        "3. Получи картинку через пару секунд\n\n"
        "🔹 *Как использовать AI:*\n"
        "• /ask Как придумать идею для видео?\n"
        "• /ask Напиши текст про котиков\n"
        "• Или просто отправь вопрос текстом!"
    )
    
    if user_id == ADMIN_ID:
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
    c.execute('''SELECT downloads_today, ai_requests_today, image_generations_today,
                 total_downloads, total_ai_requests, total_images,
                 bonus_downloads, bonus_ai, bonus_images, referral_count 
                 FROM users WHERE user_id = ?''', (user_id,))
    data = c.fetchone()
    conn.close()
    
    today_d = data[0] if data else 0
    today_ai = data[1] if data else 0
    today_img = data[2] if data else 0
    total_d = data[3] if data else 0
    total_ai = data[4] if data else 0
    total_img = data[5] if data else 0
    bonus_d = data[6] if data else 0
    bonus_ai = data[7] if data else 0
    bonus_img = data[8] if data else 0
    refs = data[9] if data else 0
    
    d_limit = PLANS[plan]['download_limit'] + bonus_d
    ai_limit = PLANS[plan]['ai_limit'] + bonus_ai
    img_limit = PLANS[plan]['image_limit'] + bonus_img
    
    expiry_text = f"до {expiry}" if expiry else "бессрочно"
    
    text = (
        f"👤 *Твой профиль*\n\n"
        f"Тариф: {plan_name}\n"
        f"Действует: {expiry_text}\n\n"
        f"📥 *Видео:* {today_d}/{d_limit} сегодня | Всего: {total_d}\n"
        f"🤖 *AI:* {today_ai}/{ai_limit} сегодня | Всего: {total_ai}\n"
        f"🎨 *Изображения:* {today_img}/{img_limit} сегодня | Всего: {total_img}\n\n"
        f"👥 Рефералов: {refs}\n"
        f"🎁 Бонусы: +{bonus_d} видео, +{bonus_ai} AI, +{bonus_img} картинок/день"
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
    
    code, count, bonus_d, bonus_ai, bonus_img = get_referral_info(user_id)
    bot_username = (await context.bot.get_me()).username
    link = f"https://t.me/{bot_username}?start=ref_{code}"
    
    text = (
        f"👥 *Реферальная программа*\n\n"
        f"🔗 *Твоя ссылка:*\n`{link}`\n\n"
        f"📊 *Статистика:*\n"
        f"• Приглашено друзей: {count}\n"
        f"• Бонус видео: +{bonus_d}/день\n"
        f"• Бонус AI: +{bonus_ai}/день\n"
        f"• Бонус картинок: +{bonus_img}/день\n\n"
        f"🎁 *Как это работает:*\n"
        f"За каждого друга ты получаешь:\n"
        f"• +3 скачивания в день навсегда\n"
        f"• +2 AI-запроса в день навсегда\n"
        f"• +1 генерацию картинок в день навсегда\n"
        f"Друзья тоже получают бонусы на первый день!"
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
        await update.message.reply_text(
            f"✅ Тариф {PLANS[plan_id]['name']} активирован на 30 дней!\n"
            f"🤖 Теперь у тебя больше AI-запросов и генераций!"
        )

async def back_profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    plan, expiry = get_user_plan(user_id)
    
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''SELECT downloads_today, ai_requests_today, image_generations_today,
                 total_downloads, total_ai_requests, total_images,
                 bonus_downloads, bonus_ai, bonus_images, referral_count 
                 FROM users WHERE user_id = ?''', (user_id,))
    data = c.fetchone()
    conn.close()
    
    today_d = data[0] if data else 0
    today_ai = data[1] if data else 0
    today_img = data[2] if data else 0
    total_d = data[3] if data else 0
    total_ai = data[4] if data else 0
    total_img = data[5] if data else 0
    bonus_d = data[6] if data else 0
    bonus_ai = data[7] if data else 0
    bonus_img = data[8] if data else 0
    refs = data[9] if data else 0
    
    d_limit = PLANS[plan]['download_limit'] + bonus_d
    ai_limit = PLANS[plan]['ai_limit'] + bonus_ai
    img_limit = PLANS[plan]['image_limit'] + bonus_img
    
    expiry_text = f"до {expiry}" if expiry else "бессрочно"
    
    text = (
        f"👤 *Твой профиль*\n\n"
        f"Тариф: {PLANS[plan]['name']}\n"
        f"Действует: {expiry_text}\n\n"
        f"📥 *Видео:* {today_d}/{d_limit} сегодня | Всего: {total_d}\n"
        f"🤖 *AI:* {today_ai}/{ai_limit} сегодня | Всего: {total_ai}\n"
        f"🎨 *Изображения:* {today_img}/{img_limit} сегодня | Всего: {total_img}\n\n"
        f"👥 Рефералов: {refs}\n"
        f"🎁 Бонусы: +{bonus_d} видео, +{bonus_ai} AI, +{bonus_img} картинок/день"
    )
    
    keyboard = [
        [InlineKeyboardButton("💎 Тарифы", callback_data="plans")],
        [InlineKeyboardButton("👥 Рефералы", callback_data="ref")]
    ]
    await query.edit_message_text(text, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(keyboard))

# ========== АДМИН-КОМАНДЫ ==========
async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("❌ Нет прав")
        return
    
    total, active, downloads, ai_requests, images, plans_stats = get_stats()
    
    text = f"📊 *Статистика*\n\n"
    text += f"👥 Всего: {total}\n"
    text += f"📱 Актив: {active}\n"
    text += f"📥 Скачиваний: {downloads}\n"
    text += f"🤖 AI-запросов: {ai_requests}\n"
    text += f"🎨 Изображений: {images}\n\n"
    text += f"💎 *Тарифы:*\n"
    
    for plan, count in plans_stats:
        text += f"{PLANS[plan]['name']}: {count}\n"
    
    await update.message.reply_text(text, parse_mode='Markdown')

async def whois_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
Тариф: {PLANS[user[12]]['name']}
Видео сегодня: {user[6]}/{PLANS[user[12]]['download_limit'] + (user[20] or 0)}
AI сегодня: {user[7]}/{PLANS[user[12]]['ai_limit'] + (user[21] or 0)}
Картинки сегодня: {user[8]}/{PLANS[user[12]]['image_limit'] + (user[22] or 0)}
Всего видео: {user[14]}
Всего AI: {user[15]}
Всего картинок: {user[16]}
Рефералов: {user[18]}
Бонус видео: +{user[20] or 0}/день
Бонус AI: +{user[21] or 0}/день
Бонус картинок: +{user[22] or 0}/день

{'🔴 ЗАБЛОКИРОВАН' if user[23] == 1 else '🟢 Активен'}"""
    
    conn.close()
    await update.message.reply_text(text, parse_mode='Markdown')

async def ban_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
    
    await update.message.reply_text(f"✅ Пользователь {user_id} заблокирован")

async def unban_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
    
    await update.message.reply_text(f"✅ Пользователь {user_id} разблокирован")

async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
            await asyncio.sleep(0.05)
        except Exception as e:
            failed += 1
    
    await update.message.reply_text(f"✅ Рассылка завершена\nОтправлено: {sent}\nОшибок: {failed}")

async def setplan_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("❌ Нет прав")
        return
    
    args = context.args
    if len(args) < 4:
        await update.message.reply_text("Использование: /addbonus <user_id> <video_bonus> <ai_bonus> <image_bonus>")
        return
    
    try:
        user_id = int(args[0])
        video_bonus = int(args[1])
        ai_bonus = int(args[2])
        image_bonus = int(args[3])
        
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("UPDATE users SET bonus_downloads = bonus_downloads + ?, bonus_ai = bonus_ai + ?, bonus_images = bonus_images + ? WHERE user_id = ?", 
                 (video_bonus, ai_bonus, image_bonus, user_id))
        conn.commit()
        conn.close()
        
        await update.message.reply_text(f"✅ Пользователю {user_id} добавлено +{video_bonus} видео, +{ai_bonus} AI, +{image_bonus} картинок/день")
        
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка: {e}")

async def resetlimit_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
        c.execute("UPDATE users SET downloads_today = 0, ai_requests_today = 0, image_generations_today = 0 WHERE user_id = ?", (user_id,))
        conn.commit()
        conn.close()
        
        await update.message.reply_text(f"✅ Лимиты пользователя {user_id} сброшены")
        
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка: {e}")

async def backup_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("❌ Нет прав")
        return
    
    try:
        backup_path = f'/data/backup_{datetime.now().strftime("%Y%m%d_%H%M%S")}.db'
        
        conn = sqlite3.connect(DB_PATH)
        backup_conn = sqlite3.connect(backup_path)
        conn.backup(backup_conn)
        backup_conn.close()
        conn.close()
        
        with open(backup_path, 'rb') as f:
            await update.message.reply_document(
                document=f,
                filename=f"backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db",
                caption="✅ Бэкап базы данных"
            )
        
        os.remove(backup_path)
        
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка создания бэкапа: {e}")

async def export_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("❌ Нет прав")
        return
    
    try:
        csv_path = f'/data/users_{datetime.now().strftime("%Y%m%d_%H%M%S")}.csv'
        
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute('''SELECT user_id, username, first_name, last_name, first_seen, 
                    last_active, total_downloads, total_ai_requests, total_images, plan, 
                    bonus_downloads, bonus_ai, bonus_images, referral_count
                    FROM users''')
        users = c.fetchall()
        conn.close()
        
        with open(csv_path, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(['ID', 'Username', 'Имя', 'Фамилия', 'Первый вход', 
                            'Последний вход', 'Всего видео', 'Всего AI', 'Всего картинок', 'Тариф', 
                            'Бонус видео', 'Бонус AI', 'Бонус картинок', 'Рефералов'])
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

async def ping_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("❌ Нет прав")
        return
    
    start = time.time()
    msg = await update.message.reply_text("🏓 Pong...")
    end = time.time()
    
    await msg.edit_text(f"🏓 Pong!\nЗадержка: {round((end - start) * 1000)}ms")

async def restart_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("❌ Нет прав")
        return
    
    await update.message.reply_text("🔄 Перезапускаюсь...")
    logger.info("Перезапуск по команде админа")
    os._exit(0)

async def ask_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда для AI-запросов"""
    user = update.effective_user
    user_id = user.id
    
    save_user(user_id, user.username, user.first_name, user.last_name)
    
    query = ' '.join(context.args) if context.args else None
    
    if not query:
        await update.message.reply_text(
            "❓ Напиши свой вопрос после /ask\n\n"
            "Примеры:\n"
            "/ask Как придумать идею для TikTok?\n"
            "/ask Напиши текст про путешествия"
        )
        return
    
    status_msg = await update.message.reply_text("🤔 Думаю...")
    response = await ask_ai(query, user_id)
    await status_msg.edit_text(response, parse_mode='Markdown')

# ========== ОБРАБОТКА СООБЩЕНИЙ ==========
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id
    text = update.message.text.strip()
    
    # Проверка бана
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT is_banned FROM users WHERE user_id = ?", (user_id,))
    result = c.fetchone()
    conn.close()
    
    if result and result[0] == 1:
        await update.message.reply_text("❌ Вы заблокированы")
        return
    
    save_user(user_id, user.username, user.first_name, user.last_name)
    
    # Проверяем, похоже ли на ссылку
    if 'http://' in text or 'https://' in text or 'www.' in text:
        # Это ссылка - пробуем скачать
        can, left = check_download_limit(user_id)
        if not can:
            await update.message.reply_text(
                "❌ Ты исчерпал лимит скачиваний на сегодня.\n"
                "Купи подписку /plan или приведи друзей /ref для увеличения лимита!"
            )
            return
        
        # Сначала пробуем проанализировать
        info = await analyze_video_url(text)
        if info:
            await update.message.reply_text(info, parse_mode='Markdown')
        
        # Скачиваем видео
        msg = await update.message.reply_text("⏳ Скачиваю видео...")
        
        try:
            filepath = await download_video(text)
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
            
            _, left = check_download_limit(user_id)
            if left < 3:
                await update.message.reply_text(
                    f"⚠️ У тебя осталось {left} скачиваний сегодня.\n"
                    f"Приведи друга /ref, чтобы получить больше!"
                )
            
        except Exception as e:
            logger.error(f"Ошибка: {e}")
            await msg.edit_text("❌ Ошибка, попробуй другую ссылку")
    
    else:
        # Не ссылка - отправляем как AI запрос
        can, left = check_ai_limit(user_id)
        if not can:
            await update.message.reply_text(
                "❌ Ты исчерпал лимит AI-запросов на сегодня.\n"
                "Купи подписку /plan или приведи друзей /ref для увеличения лимита!"
            )
            return
        
        msg = await update.message.reply_text("🤔 Думаю...")
        response = await ask_ai(text, user_id)
        await msg.edit_text(response, parse_mode='Markdown')

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
    
    logger.info("✅ Бот с генерацией изображений запущен")
    app.run_polling()

if __name__ == '__main__':
    main()