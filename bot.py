import os
import logging
from telegram import Update
from telegram.ext import Application, CommandHandler

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

BOT_TOKEN = os.environ.get('BOT_TOKEN')
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не найден!")

async def start(update: Update, context):
    await update.message.reply_text("✅ БОТ РАБОТАЕТ! Монетизация скоро будет.")

async def plan(update: Update, context):
    await update.message.reply_text("💰 Тестовая команда plan")

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("plan", plan))
    logging.info("🚀 ТЕСТОВЫЙ БОТ ЗАПУЩЕН")
    app.run_polling()

if __name__ == '__main__':
    main()