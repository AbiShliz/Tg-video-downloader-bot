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
import subprocess
from datetime import datetime, timedelta
import yt_dlp
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler, PreCheckoutQueryHandler
from telegram.constants import ParseMode
from io import BytesIO
from PIL import Image, ImageDraw, ImageFont
import textwrap

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

# ========== ПРОВЕРКА НАЛИЧИЯ FFMPEG ==========
FFMPEG_AVAILABLE = False
try:
    subprocess.run(['ffmpeg', '-version'], capture_output=True, check=True)
    FFMPEG_AVAILABLE = True
    logger.info("✅ FFmpeg установлен")
except:
    logger.warning("⚠️ FFmpeg не найден! Функции конвертации будут недоступны")

# ========== НАСТРОЙКИ ==========
DOWNLOAD_DIR = 'downloads'
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# Путь к шрифту для мемов
FONT_PATH = 'impact.ttf'  # лежит в корне

# Базовые опции для yt-dlp
YDL_OPTIONS = {
    'format': 'best[ext=mp4]/bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
    'merge_output_format': 'mp4',
    'postprocessors': [{
        'key': 'FFmpegVideoConvertor',
        'preferedformat': 'mp4',
    }] if FFMPEG_AVAILABLE else [],
    'quiet': True,
    'no_warnings': True,
}

# Поддерживаемые платформы
PLATFORMS = {
    'youtube': {'name': 'YouTube', 'patterns': ['youtube.com', 'youtu.be'], 'enabled': True},
    'tiktok': {'name': 'TikTok', 'patterns': ['tiktok.com'], 'enabled': True},
    'instagram': {'name': 'Instagram', 'patterns': ['instagram.com'], 'enabled': True},
    'vk': {'name': 'VK', 'patterns': ['vk.com', 'vkontakte.ru'], 'enabled': True},
    'pinterest': {'name': 'Pinterest', 'patterns': ['pinterest.com', 'pin.it'], 'enabled': True},
    'twitter': {'name': 'Twitter/X', 'patterns': ['twitter.com', 'x.com'], 'enabled': True},
    'reddit': {'name': 'Reddit', 'patterns': ['reddit.com'], 'enabled': True},
    'rutube': {'name': 'Rutube', 'patterns': ['rutube.ru'], 'enabled': True},
    'dzen': {'name': 'Дзен', 'patterns': ['dzen.ru', 'zen.yandex.ru'], 'enabled': True}
}

# ========== ТАРИФЫ ==========
PLANS = {
    'basic': {
        'name': '🔹 Базовый',
        'price': 0,
        'daily_limit': 3,
        'max_size_mb': 50,
        'features': ['3 видео/день', 'MP4', 'до 50 МБ']
    },
    'starter': {
        'name': '🔸 Стартовый',
        'price': 25,
        'daily_limit': 30,
        'max_size_mb': 500,
        'features': ['30 видео/день', 'MP4 со звуком', 'до 500 МБ', 'Поиск музыки', 'Мемы', 'Приоритет']
    },
    'premium': {
        'name': '💎 Премиум',
        'price': 50,
        'daily_limit': 999999,
        'max_size_mb': 2000,
        'features': ['Безлимитные видео', 'MP4 со звуком', 'до 2 ГБ', 'Все функции', 'Приоритет 24/7']
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
        last_download_date TEXT,
        plan TEXT DEFAULT 'basic',
        plan_expiry TEXT,
        total_downloads INTEGER DEFAULT 0,
        referrer_id INTEGER DEFAULT NULL,
        referral_code TEXT UNIQUE,
        referral_count INTEGER DEFAULT 0,
        bonus_downloads INTEGER DEFAULT 0,
        is_banned INTEGER DEFAULT 0
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

def check_daily_limit(user_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT plan, downloads_today, bonus_downloads FROM users WHERE user_id = ?", (user_id,))
    result = c.fetchone()
    conn.close()
    
    if not result:
        return True, 3
    
    plan, today, bonus = result
    bonus = bonus or 0
    limit = PLANS[plan]['daily_limit'] + bonus
    today = today or 0
    
    return today < limit, limit - today

def increment_downloads(user_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    today = datetime.now().strftime("%Y-%m-%d")
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    c.execute("SELECT last_download_date FROM users WHERE user_id = ?", (user_id,))
    last_date = c.fetchone()
    
    if last_date and last_date[0] != today:
        c.execute("UPDATE users SET downloads_today = 0 WHERE user_id = ?", (user_id,))
    
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

# ========== ФУНКЦИИ ДЛЯ ОПРЕДЕЛЕНИЯ ПЛАТФОРМЫ ==========
def detect_platform(url):
    url_lower = url.lower()
    for platform_id, platform in PLATFORMS.items():
        if not platform['enabled']:
            continue
        for pattern in platform['patterns']:
            if pattern in url_lower:
                return platform_id, platform['name']
    return None, "Неизвестная платформа"

# ========== ФУНКЦИИ СКАЧИВАНИЯ ==========
def get_ydl_opts_for_platform(platform):
    base_opts = YDL_OPTIONS.copy()
    if platform == 'vk':
        base_opts['extractor_args'] = {'vk': {'prefer_mp4': True}}
    elif platform == 'twitter':
        base_opts['format'] = 'best[ext=mp4]/best'
    elif platform == 'reddit':
        base_opts['format'] = 'best[ext=mp4]/best'
    return base_opts

async def download_video(url):
    try:
        platform_id, platform_name = detect_platform(url)
        if not platform_id:
            return None, "❌ Платформа не поддерживается"
        
        logger.info(f"📥 Скачиваю с {platform_name}: {url[:50]}...")
        
        timestamp = int(time.time())
        random_id = random.randint(1000, 9999)
        output_template = os.path.join(DOWNLOAD_DIR, f'video_{timestamp}_{random_id}.%(ext)s')
        
        ydl_opts = get_ydl_opts_for_platform(platform_id)
        ydl_opts['outtmpl'] = output_template
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            filename = ydl.prepare_filename(info)
            
            base = os.path.splitext(filename)[0]
            mp4_file = f"{base}.mp4"
            
            if os.path.exists(mp4_file):
                final_file = mp4_file
            elif os.path.exists(filename):
                final_file = filename
            else:
                return None, "❌ Не удалось найти скачанный файл"
            
            title = info.get('title', 'Без названия')
            duration = info.get('duration', 0)
            uploader = info.get('uploader', 'Неизвестно')
            
            return final_file, {
                'title': title,
                'duration': duration,
                'uploader': uploader,
                'platform': platform_name
            }
            
    except Exception as e:
        logger.error(f"Ошибка скачивания: {e}")
        return None, f"❌ Ошибка: {str(e)[:100]}"

# ========== ФУНКЦИИ ДЛЯ ПОИСКА И СКАЧИВАНИЯ МУЗЫКИ ==========
async def search_youtube(query, max_results=5):
    """Поиск видео на YouTube по запросу"""
    try:
        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'extract_flat': True,
            'force_generic_extractor': False,
        }
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            search_query = f"ytsearch{max_results}:{query}"
            info = ydl.extract_info(search_query, download=False)
            
            results = []
            if 'entries' in info:
                for entry in info['entries']:
                    # Пропускаем shorts
                    if entry.get('title') and not entry.get('title', '').startswith('#shorts'):
                        results.append({
                            'id': entry.get('id'),
                            'title': entry.get('title'),
                            'duration': entry.get('duration'),
                            'url': f"https://youtu.be/{entry.get('id')}",
                            'channel': entry.get('channel', 'Неизвестно')
                        })
            return results[:max_results]
    except Exception as e:
        logger.error(f"Ошибка поиска: {e}")
        return []

async def download_audio(url, user_id):
    """Скачивание аудио с YouTube в MP3"""
    if not FFMPEG_AVAILABLE:
        return None, "❌ FFmpeg не установлен на сервере. Скачивание музыки временно недоступно."
    
    try:
        timestamp = int(time.time())
        output_template = os.path.join(DOWNLOAD_DIR, f'audio_{timestamp}_{user_id}.%(ext)s')
        
        ydl_opts = {
            'format': 'bestaudio/best',
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }],
            'outtmpl': output_template,
            'quiet': True,
            'no_warnings': True,
        }
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            filename = ydl.prepare_filename(info)
            
            base = os.path.splitext(filename)[0]
            mp3_file = f"{base}.mp3"
            
            if os.path.exists(mp3_file):
                # Получаем информацию для тегов
                title = info.get('title', 'Аудио')
                uploader = info.get('uploader', 'Неизвестно')
                return mp3_file, {'title': title, 'performer': uploader}
            return None, "❌ Не удалось создать MP3"
            
    except Exception as e:
        logger.error(f"Ошибка скачивания аудио: {e}")
        return None, f"❌ Ошибка: {str(e)[:100]}"

# ========== ФУНКЦИИ ДЛЯ СОЗДАНИЯ МЕМОВ ==========
async def create_meme(image_path, top_text, bottom_text, output_path):
    """Создание мема с текстом сверху и снизу"""
    try:
        img = Image.open(image_path)
        draw = ImageDraw.Draw(img)
        
        try:
            font = ImageFont.truetype(FONT_PATH, int(img.height * 0.08))
        except:
            font = ImageFont.load_default()
            logger.warning("Шрифт не найден, используется стандартный")
        
        text_color = (255, 255, 255)
        stroke_color = (0, 0, 0)
        
        def draw_text_with_outline(text, y_position):
            wrapper = textwrap.TextWrapper(width=25)
            lines = wrapper.wrap(text)
            
            for i, line in enumerate(lines):
                bbox = draw.textbbox((0, 0), line, font=font)
                text_width = bbox[2] - bbox[0]
                
                x = (img.width - text_width) // 2
                y = y_position + i * int(font.size * 1.2)
                
                for dx, dy in [(-2,-2), (-2,2), (2,-2), (2,2)]:
                    draw.text((x+dx, y+dy), line, font=font, fill=stroke_color)
                
                draw.text((x, y), line, font=font, fill=text_color)
        
        if top_text:
            draw_text_with_outline(top_text, int(img.height * 0.05))
        
        if bottom_text:
            draw_text_with_outline(bottom_text, int(img.height * 0.8))
        
        img.save(output_path, quality=95)
        return True
        
    except Exception as e:
        logger.error(f"Ошибка создания мема: {e}")
        return False

# ========== КОМАНДЫ ==========
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
        "🔍 *Поиск музыки:* /search запрос\n"
        "🎭 *Создание мемов:* /meme текст (ответом на фото)\n\n"
        "🔹 *Поддерживаемые платформы:*\n"
        "YouTube, TikTok, Instagram, VK, Pinterest, Twitter/X, Reddit, Rutube, Дзен\n\n"
        "📋 /plan — тарифы\n"
        "👤 /profile — профиль\n"
        "👥 /ref — рефералы\n"
        "❓ /help — помощь"
    )
    await update.message.reply_text(text, parse_mode='Markdown')

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "📖 *Помощь*\n\n"
        "🔹 *Скачивание видео:*\n"
        "Просто отправь ссылку на видео\n\n"
        "🔹 *Поиск музыки:*\n"
        "/search <название> — найти и скачать MP3\n"
        "Пример: /search Queen Bohemian Rhapsody\n\n"
        "🔹 *Создание мемов:*\n"
        "/meme текст — ответом на картинку\n"
        "Формат: /meme Текст сверху | Текст снизу\n\n"
        "🔹 *Команды:*\n"
        "/start — начало\n"
        "/plan — тарифы\n"
        "/profile — профиль\n"
        "/ref — рефералы\n"
        "/help — помощь"
    )
    
    user_id = update.effective_user.id
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
    c.execute('''SELECT downloads_today, total_downloads, bonus_downloads, referral_count
                 FROM users WHERE user_id = ?''', (user_id,))
    data = c.fetchone()
    conn.close()
    
    today = data[0] if data else 0
    total = data[1] if data else 0
    bonus = data[2] if data else 0
    refs = data[3] if data else 0
    
    limit = PLANS[plan]['daily_limit'] + bonus
    expiry_text = f"до {expiry}" if expiry else "бессрочно"
    
    text = (
        f"👤 *Твой профиль*\n\n"
        f"💎 *Тариф:* {plan_name}\n"
        f"⏳ Действует: {expiry_text}\n\n"
        f"📥 *Сегодня:* {today}/{limit} скачиваний\n"
        f"📊 *Всего:* {total} скачиваний\n"
        f"👥 *Рефералов:* {refs}\n"
        f"🎁 *Бонус:* +{bonus} скачиваний/день"
    )
    
    keyboard = [
        [InlineKeyboardButton("💎 Тарифы", callback_data="plans")],
        [InlineKeyboardButton("👥 Рефералы", callback_data="ref")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(text, parse_mode='Markdown', reply_markup=reply_markup)

async def plans_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.callback_query:
        query = update.callback_query
        await query.answer()
        msg = query.message
        edit = True
    else:
        msg = update.message
        edit = False
    
    text = "💎 *Тарифы*\n\n"
    keyboard = []
    
    for pid, plan in PLANS.items():
        text += f"{plan['name']}\n"
        text += f"💰 {plan['price']} ★ / месяц\n"
        text += "▸ " + "\n▸ ".join(plan['features']) + "\n\n"
        if pid != 'basic' and edit:
            keyboard.append([InlineKeyboardButton(f"✅ Купить {plan['name']}", callback_data=f"buy_{pid}")])
    
    if edit:
        keyboard.append([InlineKeyboardButton("◀️ Назад", callback_data="back_profile")])
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(text, parse_mode='Markdown', reply_markup=reply_markup)
    else:
        await msg.reply_text(text, parse_mode='Markdown')

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
        f"🔗 *Твоя ссылка:*\n`{link}`\n\n"
        f"📊 *Статистика:*\n"
        f"• Приглашено друзей: {count}\n"
        f"• Бонус: +{bonus} скачиваний/день\n\n"
        f"🎁 *Как это работает:*\n"
        f"За каждого друга ты получаешь:\n"
        f"• +3 скачивания в день навсегда"
    )
    
    keyboard = []
    if edit:
        keyboard.append([InlineKeyboardButton("◀️ Назад", callback_data="back_profile")])
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(text, parse_mode='Markdown', reply_markup=reply_markup)
    else:
        await msg.reply_text(text, parse_mode='Markdown')

async def back_profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    plan, expiry = get_user_plan(user_id)
    plan_name = PLANS[plan]['name']
    
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''SELECT downloads_today, total_downloads, bonus_downloads, referral_count
                 FROM users WHERE user_id = ?''', (user_id,))
    data = c.fetchone()
    conn.close()
    
    today = data[0] if data else 0
    total = data[1] if data else 0
    bonus = data[2] if data else 0
    refs = data[3] if data else 0
    
    limit = PLANS[plan]['daily_limit'] + bonus
    expiry_text = f"до {expiry}" if expiry else "бессрочно"
    
    text = (
        f"👤 *Твой профиль*\n\n"
        f"💎 *Тариф:* {plan_name}\n"
        f"⏳ Действует: {expiry_text}\n\n"
        f"📥 *Сегодня:* {today}/{limit} скачиваний\n"
        f"📊 *Всего:* {total} скачиваний\n"
        f"👥 *Рефералов:* {refs}\n"
        f"🎁 *Бонус:* +{bonus} скачиваний/день"
    )
    
    keyboard = [
        [InlineKeyboardButton("💎 Тарифы", callback_data="plans")],
        [InlineKeyboardButton("👥 Рефералы", callback_data="ref")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(text, parse_mode='Markdown', reply_markup=reply_markup)

# ========== НОВАЯ КОМАНДА ДЛЯ ПОИСКА МУЗЫКИ ==========
async def search_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Поиск музыки по текстовому запросу"""
    user_id = update.effective_user.id
    
    # Проверка тарифа
    plan, _ = get_user_plan(user_id)
    if plan == 'basic':
        await update.message.reply_text(
            "❌ *Функция доступна только с тарифом Стартовый и выше*\n\n"
            "Купи подписку /plan чтобы искать и скачивать музыку",
            parse_mode='Markdown'
        )
        return
    
    # Проверка FFmpeg
    if not FFMPEG_AVAILABLE:
        await update.message.reply_text(
            "❌ *FFmpeg не установлен на сервере*\n\n"
            "Скачивание музыки временно недоступно. Администратор уже знает о проблеме.",
            parse_mode='Markdown'
        )
        return
    
    query = ' '.join(context.args) if context.args else None
    
    if not query:
        await update.message.reply_text(
            "❓ Использование: /search <название песни>\n\n"
            "Пример: /search Queen Bohemian Rhapsody"
        )
        return
    
    status_msg = await update.message.reply_text(f"🔍 Ищу: {query}...")
    
    # Выполняем поиск
    results = await search_youtube(query, max_results=5)
    
    if not results:
        await status_msg.edit_text("❌ Ничего не найдено. Попробуй другой запрос.")
        return
    
    # Создаем клавиатуру с результатами
    keyboard = []
    for i, result in enumerate(results):
        duration = result['duration']
        if duration:
            minutes = duration // 60
            seconds = duration % 60
            duration_str = f"{minutes}:{seconds:02d}"
        else:
            duration_str = "?"
        
        button_text = f"{i+1}. {result['title'][:45]} ({duration_str})"
        keyboard.append([InlineKeyboardButton(
            button_text, 
            callback_data=f"music_{result['id']}"
        )])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await status_msg.edit_text(
        f"🔍 *Результаты поиска* для: {query}\n\nВыбери трек для скачивания:",
        parse_mode='Markdown',
        reply_markup=reply_markup
    )
    
    # Сохраняем результаты в контексте
    context.user_data['search_results'] = results

# ========== ОБРАБОТЧИК ВЫБОРА ТРЕКА ==========
async def music_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Скачивание выбранного трека"""
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    
    # Проверка тарифа
    plan, _ = get_user_plan(user_id)
    if plan == 'basic':
        await query.edit_message_text(
            "❌ *Функция доступна только с тарифом Стартовый и выше*\n\n"
            "Купи подписку /plan чтобы искать и скачивать музыку",
            parse_mode='Markdown'
        )
        return
    
    video_id = query.data.replace('music_', '')
    
    status_msg = await query.edit_message_text("🎵 Скачиваю аудио...")
    
    try:
        url = f"https://youtu.be/{video_id}"
        
        result, info = await download_audio(url, user_id)
        
        if result and os.path.exists(result):
            with open(result, 'rb') as f:
                await context.bot.send_audio(
                    chat_id=user_id,
                    audio=f,
                    title=info.get('title', 'Аудио'),
                    performer=info.get('performer', 'Неизвестно'),
                    caption="✅ Готово!"
                )
            await status_msg.delete()
        else:
            await status_msg.edit_text(f"❌ {info if isinstance(info, str) else 'Не удалось скачать аудио'}")
            
    except Exception as e:
        logger.error(f"Ошибка: {e}")
        await status_msg.edit_text("❌ Ошибка при скачивании")
    finally:
        if result and os.path.exists(result):
            os.remove(result)

# ========== КОМАНДА ДЛЯ МЕМОВ ==========
async def meme_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Создание мема: /meme Текст сверху | Текст снизу"""
    user_id = update.effective_user.id
    plan, _ = get_user_plan(user_id)
    
    if plan == 'basic':
        await update.message.reply_text(
            "❌ *Функция доступна только с тарифом Стартовый и выше*\n\n"
            "Купи подписку /plan чтобы создавать мемы",
            parse_mode='Markdown'
        )
        return
    
    text = ' '.join(context.args) if context.args else ""
    if not text:
        await update.message.reply_text(
            "❓ Использование: /meme текст\n\n"
            "Пример: /meme Когда увидел баг | Но это фича\n\n"
            "Ответь этой командой на картинку"
        )
        return
    
    if '|' in text:
        top, bottom = text.split('|', 1)
        top = top.strip()
        bottom = bottom.strip()
    else:
        top = text
        bottom = ""
    
    if not update.message.reply_to_message or not update.message.reply_to_message.photo:
        await update.message.reply_text("❌ Ответь этой командой на картинку")
        return
    
    status_msg = await update.message.reply_text("🎭 Создаю мем...")
    
    try:
        photo = update.message.reply_to_message.photo[-1]
        file = await context.bot.get_file(photo.file_id)
        
        input_path = f"temp_input_{user_id}.jpg"
        output_path = f"meme_output_{user_id}.jpg"
        await file.download_to_drive(input_path)
        
        success = await create_meme(input_path, top, bottom, output_path)
        
        if success and os.path.exists(output_path):
            with open(output_path, 'rb') as f:
                await update.message.reply_photo(
                    photo=f,
                    caption=f"🎭 *Мем готов!*",
                    parse_mode='Markdown'
                )
            await status_msg.delete()
        else:
            await status_msg.edit_text("❌ Не удалось создать мем")
        
        if os.path.exists(input_path):
            os.remove(input_path)
        if os.path.exists(output_path):
            os.remove(output_path)
            
    except Exception as e:
        logger.error(f"Ошибка создания мема: {e}")
        await status_msg.edit_text("❌ Ошибка при создании мема")

# ========== ПЛАТЕЖИ ==========
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
            f"✅ *Тариф активирован!*\n\nТариф {PLANS[plan_id]['name']} активирован на 30 дней.",
            parse_mode='Markdown'
        )

# ========== АДМИН-КОМАНДЫ (сокращенно) ==========
async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    total, active, downloads, plans_stats = get_stats()
    text = f"📊 *Статистика*\n\n👥 Всего: {total}\n📱 Активных: {active}\n📥 Скачиваний: {downloads}\n\n💎 *Тарифы:*\n"
    for plan, count in plans_stats:
        text += f"{PLANS[plan]['name']}: {count}\n"
    await update.message.reply_text(text, parse_mode='Markdown')

async def whois_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
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
        c.execute("SELECT * FROM users WHERE username = ?", (username,))
    else:
        try:
            user_id = int(target)
            c.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
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
Рефералов: {user[12]}
Бонус: +{user[13]} скачиваний/день

{'🔴 ЗАБЛОКИРОВАН' if user[14] == 1 else '🟢 Активен'}"""
    
    conn.close()
    await update.message.reply_text(text, parse_mode='Markdown')

async def ban_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    args = context.args
    if not args:
        await update.message.reply_text("Использование: /ban <user_id>")
        return
    try:
        user_id = int(args[0])
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("UPDATE users SET is_banned = 1 WHERE user_id = ?", (user_id,))
        conn.commit()
        conn.close()
        await update.message.reply_text(f"✅ Пользователь {user_id} заблокирован")
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка: {e}")

async def unban_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    args = context.args
    if not args:
        await update.message.reply_text("Использование: /unban <user_id>")
        return
    try:
        user_id = int(args[0])
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("UPDATE users SET is_banned = 0 WHERE user_id = ?", (user_id,))
        conn.commit()
        conn.close()
        await update.message.reply_text(f"✅ Пользователь {user_id} разблокирован")
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка: {e}")

async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    text = ' '.join(context.args)
    if not text:
        await update.message.reply_text("Использование: /broadcast <текст>")
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
            await context.bot.send_message(user_id, f"📢 *Сообщение от администратора:*\n\n{text}", parse_mode='Markdown')
            sent += 1
            await asyncio.sleep(0.05)
        except:
            failed += 1
    await update.message.reply_text(f"✅ Рассылка завершена\nОтправлено: {sent}\nОшибок: {failed}")

async def setplan_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
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
        return
    args = context.args
    if len(args) < 2:
        await update.message.reply_text("Использование: /addbonus <user_id> <bonus>")
        return
    try:
        user_id = int(args[0])
        bonus = int(args[1])
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("UPDATE users SET bonus_downloads = bonus_downloads + ? WHERE user_id = ?", (bonus, user_id))
        conn.commit()
        conn.close()
        await update.message.reply_text(f"✅ Пользователю {user_id} добавлено +{bonus} скачиваний/день")
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка: {e}")

async def resetlimit_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
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
        await update.message.reply_text(f"✅ Лимиты пользователя {user_id} сброшены")
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка: {e}")

async def backup_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    try:
        backup_path = f'/data/backup_{datetime.now().strftime("%Y%m%d_%H%M%S")}.db'
        conn = sqlite3.connect(DB_PATH)
        backup_conn = sqlite3.connect(backup_path)
        conn.backup(backup_conn)
        backup_conn.close()
        conn.close()
        with open(backup_path, 'rb') as f:
            await update.message.reply_document(document=f, filename=f"backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db", caption="✅ Бэкап базы данных")
        os.remove(backup_path)
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка: {e}")

async def export_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
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
                            'Последний вход', 'Всего скачиваний', 'Тариф', 'Бонус', 'Рефералов'])
            writer.writerows(users)
        with open(csv_path, 'rb') as f:
            await update.message.reply_document(document=f, filename=f"users_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv", caption="✅ Экспорт пользователей")
        os.remove(csv_path)
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка: {e}")

async def ping_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    start = time.time()
    msg = await update.message.reply_text("🏓 Pong...")
    end = time.time()
    await msg.edit_text(f"🏓 Pong!\nЗадержка: {round((end - start) * 1000)}ms")

async def restart_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    await update.message.reply_text("🔄 Перезапускаюсь...")
    logger.info("Перезапуск по команде админа")
    os._exit(0)

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
    
    # Проверяем, является ли сообщение ссылкой
    if 'http://' in text or 'https://' in text or 'www.' in text:
        
        can, left = check_daily_limit(user_id)
        if not can:
            await update.message.reply_text(
                "❌ *Лимит скачиваний исчерпан*\n\nКупи подписку /plan или приведи друзей /ref",
                parse_mode='Markdown'
            )
            return
        
        platform_id, platform_name = detect_platform(text)
        if not platform_id:
            await update.message.reply_text(
                f"❌ Платформа не поддерживается\n\nПоддерживаемые платформы: YouTube, TikTok, Instagram, VK, Pinterest, Twitter/X, Reddit, Rutube, Дзен"
            )
            return
        
        msg = await update.message.reply_text(f"📥 Скачиваю с {platform_name}...")
        
        result, info = await download_video(text)
        
        if not result:
            await msg.edit_text(f"❌ {info}")
            return
        
        file_size = os.path.getsize(result) / (1024 * 1024)
        max_size = PLANS[get_user_plan(user_id)[0]]['max_size_mb']
        
        if file_size > max_size:
            await msg.edit_text(
                f"❌ Видео слишком большое ({file_size:.1f} МБ)\n"
                f"Твой тариф позволяет до {max_size} МБ"
            )
            os.remove(result)
            return
        
        try:
            with open(result, 'rb') as f:
                caption = f"📹 *{info['title'][:50]}*" if info['title'] else None
                await update.message.reply_video(
                    video=f,
                    caption=caption,
                    parse_mode='Markdown' if caption else None,
                    supports_streaming=True
                )
            
            increment_downloads(user_id)
            await msg.delete()
            
        except Exception as e:
            logger.error(f"Ошибка отправки: {e}")
            await msg.edit_text("❌ Ошибка при отправке видео")
        
        finally:
            if os.path.exists(result):
                os.remove(result)
    
    else:
        await update.message.reply_text(
            "📤 Отправь ссылку на видео, чтобы скачать его\n\n"
            "Доступные команды:\n"
            "/search — поиск музыки\n"
            "/meme — создать мем из картинки\n"
            "/profile — профиль\n"
            "/plan — тарифы\n"
            "/ref — рефералы"
        )

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
    
    # Новые команды
    app.add_handler(CommandHandler("search", search_command))
    app.add_handler(CommandHandler("meme", meme_command))
    
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
    app.add_handler(CallbackQueryHandler(music_callback, pattern="^music_"))
    
    # Платежи
    app.add_handler(PreCheckoutQueryHandler(pre_checkout))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, payment_success))
    
    # Сообщения
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    logger.info("✅ Бот с функциями скачивания, поиска музыки и мемов запущен")
    app.run_polling()

if __name__ == '__main__':
    main()