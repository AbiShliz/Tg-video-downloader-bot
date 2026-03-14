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
    format='%(asime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Токен из переменных окружения
BOT_TOKEN = os.environ.get('BOT_TOKEN')
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не найден!")

# Твой Telegram ID (админ)
ADMIN_ID = 920343231  # ТВОЙ ID

# ========== API КЛЮЧИ ==========
OPENROUTER_API_KEY = os.environ.get('OPENROUTER_API_KEY')
PROXYAPI_KEY = os.environ.get('PROXYAPI_KEY')
DEEPSEEK_API_KEY = os.environ.get('DEEPSEEK_API_KEY')
FUSIONBRAIN_KEY = os.environ.get('FUSIONBRAIN_KEY')
FUSIONBRAIN_SECRET = os.environ.get('FUSIONBRAIN_SECRET')
PRODIA_KEY = os.environ.get('PRODIA_KEY')
FICHI_API_KEY = os.environ.get('FICHI_API_KEY')

if OPENROUTER_API_KEY:
    logger.info(f"✅ OpenRouter ключ найден")
if DEEPSEEK_API_KEY:
    logger.info(f"✅ DeepSeek ключ найден")
if FUSIONBRAIN_KEY:
    logger.info(f"✅ FusionBrain ключ найден")

# ========== НАСТРОЙКИ API ==========
POLLINATIONS_API = "https://image.pollinations.ai/prompt/"
POLLINATIONS_FALLBACK = "https://pollinations.ai/p/"

# Настройки для разных моделей генерации
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

# ========== УЛУЧШЕННАЯ ФУНКЦИЯ ГЕНЕРАЦИИ ИЗОБРАЖЕНИЙ ==========
async def generate_image(prompt, style='realistic', max_retries=2):
    """Генерация изображения с несколькими API (все работают без ключей!)"""
    try:
        style_prompt = f"{prompt}, {IMAGE_STYLES[style]}"
        logger.info(f"Генерация изображения: {style_prompt[:50]}...")
        
        # 1. DeepAI (бесплатный, быстрый, без ключа)
        try:
            async with aiohttp.ClientSession() as session:
                url = "https://api.deepai.org/api/text2img"
                headers = {"api-key": "quickstart-QUdJIGlzIGNvbWluZy4uLi4K"}  # Публичный ключ
                data = {"text": style_prompt}
                
                async with session.post(url, data=data, headers=headers, timeout=60) as resp:
                    if resp.status == 200:
                        result = await resp.json()
                        if result.get('output_url'):
                            # Скачиваем картинку
                            async with session.get(result['output_url']) as img_resp:
                                if img_resp.status == 200:
                                    image_data = await img_resp.read()
                                    logger.info("✅ DeepAI успешно сгенерировал изображение")
                                    return image_data
        except Exception as e:
            logger.warning(f"DeepAI error: {e}")
        
        # 2. Craiyon (бесплатный, без ключа)
        try:
            async with aiohttp.ClientSession() as session:
                url = "https://api.craiyon.com/v3"
                payload = {
                    "prompt": style_prompt,
                    "version": "35s9hf7d",
                    "negative_prompt": ""
                }
                async with session.post(url, json=payload, timeout=60) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        if data.get('images'):
                            image_data = base64.b64decode(data['images'][0])
                            logger.info("✅ Craiyon успешно сгенерировал изображение")
                            return image_data
        except Exception as e:
            logger.warning(f"Craiyon error: {e}")
        
        # 3. FusionBrain (российский, если есть ключи)
        if FUSIONBRAIN_KEY and FUSIONBRAIN_SECRET:
            try:
                async with aiohttp.ClientSession() as session:
                    pipeline_url = "https://api-key.fusionbrain.ai/key/api/v1/pipelines"
                    headers = {
                        "X-Key": FUSIONBRAIN_KEY,
                        "X-Secret": FUSIONBRAIN_SECRET
                    }
                    
                    async with session.get(pipeline_url, headers=headers, timeout=30) as resp:
                        if resp.status == 200:
                            pipelines = await resp.json()
                            if pipelines and len(pipelines) > 0:
                                pipeline_id = pipelines[0]['id']
                                
                                gen_url = f"https://api-key.fusionbrain.ai/key/api/v1/pipeline/{pipeline_id}/run"
                                data = {
                                    "type": "GENERATE",
                                    "style": "DEFAULT",
                                    "width": 1024,
                                    "height": 1024,
                                    "num_images": 1,
                                    "generate_params": {
                                        "query": style_prompt
                                    }
                                }
                                
                                async with session.post(gen_url, json=data, headers=headers, timeout=60) as gen_resp:
                                    if gen_resp.status == 200:
                                        result = await gen_resp.json()
                                        if result.get('status') == 'DONE' and result.get('images'):
                                            image_data = base64.b64decode(result['images'][0])
                                            logger.info("✅ FusionBrain успешно сгенерировал изображение")
                                            return image_data
            except Exception as e:
                logger.warning(f"FusionBrain error: {e}")
        
        # 4. Prodia (если есть ключ)
        if PRODIA_KEY:
            try:
                async with aiohttp.ClientSession() as session:
                    url = "https://api.prodia.com/v1/sd/generate"
                    params = {
                        "model": "sdv1_4.ckpt",
                        "prompt": style_prompt,
                        "negative_prompt": "nsfw, bad quality",
                        "steps": 20,
                        "cfg_scale": 7,
                        "width": 512,
                        "height": 512
                    }
                    headers = {"X-Prodia-Key": PRODIA_KEY}
                    
                    async with session.post(url, json=params, headers=headers, timeout=30) as resp:
                        if resp.status == 200:
                            job = await resp.json()
                            job_id = job.get('job')
                            
                            for _ in range(10):
                                await asyncio.sleep(2)
                                status_url = f"https://api.prodia.com/v1/job/{job_id}"
                                async with session.get(status_url, headers=headers) as status_resp:
                                    if status_resp.status == 200:
                                        result = await status_resp.json()
                                        if result.get('status') == 'succeeded' and result.get('imageUrl'):
                                            async with session.get(result['imageUrl']) as img_resp:
                                                image_data = await img_resp.read()
                                                logger.info("✅ Prodia успешно сгенерировал изображение")
                                                return image_data
            except Exception as e:
                logger.warning(f"Prodia error: {e}")
        
        # 5. Pollinations (запасной)
        try:
            encoded_prompt = urllib.parse.quote(style_prompt)
            api_url = f"{POLLINATIONS_API}{encoded_prompt}?width=1024&height=1024&nologo=true&model=flux"
            
            async with aiohttp.ClientSession() as session:
                async with session.get(api_url, timeout=30) as resp:
                    if resp.status == 200:
                        image_data = await resp.read()
                        if len(image_data) > 1000:
                            logger.info("✅ Pollinations успешно сгенерировал изображение")
                            return image_data
        except Exception as e:
            logger.warning(f"Pollinations error: {e}")
        
        logger.error("❌ Все API генерации изображений не сработали")
        return None
        
    except Exception as e:
        logger.error(f"Image generation error: {e}")
        return None

# ========== УЛУЧШЕННАЯ ФУНКЦИЯ AI-АССИСТЕНТА ==========
async def ask_ai(prompt, user_id):
    """Запрос к AI через несколько бесплатных API (оптимизировано для России)"""
    
    can, left = check_ai_limit(user_id)
    if not can:
        return "❌ Ты исчерпал лимит AI-запросов на сегодня."

    logger.info(f"AI запрос от пользователя {user_id}: {prompt[:50]}...")

    # 1. OpenRouter (бесплатные модели)
    if OPENROUTER_API_KEY:
        models_to_try = [
            'google/gemini-1.5-flash:free',
            'mistralai/mistral-7b-instruct:free',
            'microsoft/phi-3-mini-128k-instruct:free',
            'meta-llama/llama-3.2-3b-instruct:free'
        ]
        
        for model in models_to_try:
            try:
                async with aiohttp.ClientSession() as session:
                    url = "https://openrouter.ai/api/v1/chat/completions"
                    headers = {
                        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                        "Content-Type": "application/json",
                        "HTTP-Referer": "https://t.me/TikTokSavebot",
                        "X-Title": "TikTokSavebot"
                    }
                    
                    payload = {
                        "model": model,
                        "messages": [
                            {"role": "system", "content": "Ты полезный ассистент. Отвечай кратко и по делу. Используй язык пользователя."},
                            {"role": "user", "content": prompt}
                        ],
                        "temperature": 0.7,
                        "max_tokens": 1000
                    }
                    
                    async with session.post(url, json=payload, headers=headers, timeout=60) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            if 'choices' in data and len(data['choices']) > 0:
                                result = data['choices'][0]['message']['content']
                                increment_ai_request(user_id)
                                logger.info(f"✅ OpenRouter успешно с моделью {model}")
                                return result
                        else:
                            logger.warning(f"OpenRouter модель {model} вернула статус {resp.status}")
            except Exception as e:
                logger.warning(f"OpenRouter модель {model} ошибка: {e}")
                continue

    # 2. ProxyAPI (российский прокси-сервис)
    if PROXYAPI_KEY:
        try:
            async with aiohttp.ClientSession() as session:
                url = "https://api.proxyapi.ru/deepseek/v1/chat/completions"
                headers = {
                    "Authorization": f"Bearer {PROXYAPI_KEY}",
                    "Content-Type": "application/json"
                }
                payload = {
                    "model": "deepseek-chat",
                    "messages": [
                        {"role": "system", "content": "Ты полезный ассистент. Отвечай кратко и по делу."},
                        {"role": "user", "content": prompt}
                    ]
                }
                
                async with session.post(url, json=payload, headers=headers, timeout=60) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        result = data['choices'][0]['message']['content']
                        increment_ai_request(user_id)
                        logger.info("✅ ProxyAPI успешно")
                        return result
        except Exception as e:
            logger.warning(f"ProxyAPI error: {e}")

    # 3. DeepSeek (китайская модель, отлично работает в РФ)
    if DEEPSEEK_API_KEY:
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
                    "max_tokens": 1000
                }
                
                async with session.post(url, json=payload, headers=headers, timeout=60) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        result = data['choices'][0]['message']['content']
                        increment_ai_request(user_id)
                        logger.info("✅ DeepSeek успешно")
                        return result
        except Exception as e:
            logger.warning(f"DeepSeek error: {e}")

    # 4. MatrixHub (бесплатный доступ к Gemini через Россию)
    try:
        async with aiohttp.ClientSession() as session:
            url = "https://api.matrixhub.ai/v1/chat/completions"
            payload = {
                "model": "gemini-3-flash-lite",
                "messages": [
                    {"role": "user", "content": prompt}
                ],
                "max_tokens": 1000
            }
            headers = {"Content-Type": "application/json"}
            
            async with session.post(url, json=payload, headers=headers, timeout=60) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    result = data['choices'][0]['message']['content']
                    increment_ai_request(user_id)
                    logger.info("✅ MatrixHub Gemini успешно")
                    return result
    except Exception as e:
        logger.warning(f"MatrixHub error: {e}")

    # 5. FICHI.AI (российская платформа)
    if FICHI_API_KEY:
        try:
            async with aiohttp.ClientSession() as session:
                url = "https://api.fichi.ai/v1/chat/completions"
                headers = {
                    "Authorization": f"Bearer {FICHI_API_KEY}",
                    "Content-Type": "application/json"
                }
                payload = {
                    "model": "gemini-3-flash",
                    "messages": [
                        {"role": "system", "content": "Ты полезный ассистент."},
                        {"role": "user", "content": prompt}
                    ]
                }
                
                async with session.post(url, json=payload, headers=headers, timeout=60) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        result = data['choices'][0]['message']['content']
                        increment_ai_request(user_id)
                        logger.info("✅ FICHI успешно")
                        return result
        except Exception as e:
            logger.warning(f"FICHI error: {e}")

    # 6. Если все провалилось, возвращаем заглушку
    logger.error("❌ Все AI API не сработали")
    return "🤖 *AI временно недоступен*\n\nПопробуй позже или используй /draw для генерации картинок."

# ========== ФУНКЦИИ ГЕНЕРАЦИИ ИЗОБРАЖЕНИЙ ==========
async def generate_image_with_style_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает меню выбора стиля перед генерацией"""
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
    """Обработка выбора стиля и генерация"""
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
            f"⚠️ *Не удалось сгенерировать*\n\nСервис временно перегружен. Попробуй позже.",
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

# ========== AI-ФУНКЦИИ ==========
async def ask_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда для AI-запросов"""
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
        text += "/restart — перезапуск"
        text += "\n/testapi — тест API"

    await update.message.reply_text(text, parse_mode='Markdown')

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
    text += f"🎨 Картинок: {images}\n\n"
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
Видео сегодня: {user[6]}
AI сегодня: {user[7]}
Картинки сегодня: {user[8]}
Всего видео: {user[14]}
Всего AI: {user[15]}
Всего картинок: {user[16]}
Рефералов: {user[18]}
Бонусы: +{user[20]} видео, +{user[21]} AI, +{user[22]} картинок/день

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
            await context.bot.send_message(
                user_id,
                f"📢 *Сообщение от администратора:*\n\n{text}",
                parse_mode='Markdown'
            )
            sent += 1
            await asyncio.sleep(0.05)
        except:
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
        await update.message.reply_text("Использование: /addbonus <user_id> <video> <ai> <image>")
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
        await update.message.reply_text(f"❌ Ошибка: {e}")

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
        await update.message.reply_text(f"❌ Ошибка: {e}")

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

async def test_api_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Тестирование API"""
    if update.effective_user.id != ADMIN_ID:
        return
    
    msg = await update.message.reply_text("🔄 Тестирую API генерации картинок...")
    
    # Тест генерации картинки
    result = await generate_image("красивый закат над морем", "realistic")
    if result:
        await context.bot.send_photo(
            chat_id=update.effective_user.id,
            photo=BytesIO(result),
            caption="✅ API картинок работает!"
        )
    else:
        await update.message.reply_text("❌ API картинок не работает")
    
    # Тест AI
    await msg.edit_text("🔄 Тестирую AI API...")
    response = await ask_ai("Напиши короткое приветствие", update.effective_user.id)
    await update.message.reply_text(f"🤖 AI ответ: {response}")

# ========== ПРОФИЛЬ И ТАРИФЫ ==========
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
        f"💎 *Тариф:* {plan_name}\n"
        f"⏳ Действует: {expiry_text}\n\n"
        f"📥 *Видео:* {today_d}/{d_limit} сегодня | Всего: {total_d}\n"
        f"🤖 *AI:* {today_ai}/{ai_limit} сегодня | Всего: {total_ai}\n"
        f"🎨 *Картинки:* {today_img}/{img_limit} сегодня | Всего: {total_img}\n\n"
        f"👥 *Рефералов:* {refs}\n"
        f"🎁 *Бонусы:* +{bonus_d} видео, +{bonus_ai} AI, +{bonus_img} картинок/день"
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
        text += f"{plan['name']}\n"
        text += f"💰 {plan['price']} ★ / месяц\n"
        text += "▸ " + "\n▸ ".join(plan['features']) + "\n\n"
        if pid != current:
            keyboard.append([InlineKeyboardButton(f"✅ Купить {plan['name']}", callback_data=f"buy_{pid}")])

    keyboard.append([InlineKeyboardButton("◀️ Назад", callback_data="back_profile")])
    reply_markup = InlineKeyboardMarkup(keyboard)

    if edit:
        await query.edit_message_text(text, parse_mode='Markdown', reply_markup=reply_markup)
    else:
        await msg.reply_text(text, parse_mode='Markdown', reply_markup=reply_markup)

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
        f"• +1 генерацию картинок в день навсегда"
    )

    keyboard = [[InlineKeyboardButton("◀️ Назад", callback_data="back_profile")]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    if edit:
        await query.edit_message_text(text, parse_mode='Markdown', reply_markup=reply_markup)
    else:
        await msg.reply_text(text, parse_mode='Markdown', reply_markup=reply_markup)

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
        f"💎 *Тариф:* {PLANS[plan]['name']}\n"
        f"⏳ Действует: {expiry_text}\n\n"
        f"📥 *Видео:* {today_d}/{d_limit} сегодня | Всего: {total_d}\n"
        f"🤖 *AI:* {today_ai}/{ai_limit} сегодня | Всего: {total_ai}\n"
        f"🎨 *Картинки:* {today_img}/{img_limit} сегодня | Всего: {total_img}\n\n"
        f"👥 *Рефералов:* {refs}\n"
        f"🎁 *Бонусы:* +{bonus_d} видео, +{bonus_ai} AI, +{bonus_img} картинок/день"
    )

    keyboard = [
        [InlineKeyboardButton("💎 Тарифы", callback_data="plans")],
        [InlineKeyboardButton("👥 Рефералы", callback_data="ref")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text(text, parse_mode='Markdown', reply_markup=reply_markup)

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

            return f"""📹 *Информация о видео*

**Название:** {title}
**Автор:** {uploader}
**Длительность:** {minutes}:{seconds:02d}"""
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
            await update.message.reply_text(
                "❌ *Лимит скачиваний исчерпан*\n\nКупи подписку /plan или приведи друзей /ref",
                parse_mode='Markdown'
            )
            return

        info = await analyze_video_url(text)
        if info:
            await update.message.reply_text(info, parse_mode='Markdown')

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

        except Exception as e:
            logger.error(f"Ошибка: {e}")
            await msg.edit_text("❌ Ошибка, попробуй другую ссылку")

    else:
        can, left = check_ai_limit(user_id)
        if not can:
            await update.message.reply_text(
                "❌ *Лимит AI-запросов исчерпан*\n\nКупи подписку /plan или приведи друзей /ref",
                parse_mode='Markdown'
            )
            return

        msg = await update.message.reply_text("🤔 Думаю...")
        response = await ask_ai(text, user_id)
        await msg.edit_text(f"🤖 *AI-ассистент:*\n\n{response}", parse_mode='Markdown')

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
    app.add_handler(CommandHandler("testapi", test_api_command))

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

    logger.info("✅ Бот с AI и генерацией изображений запущен")
    app.run_polling()

if __name__ == '__main__':
    main()
