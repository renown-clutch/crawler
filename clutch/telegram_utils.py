from decouple import config
from telegram import Bot

TELEGRAM_CHAT_ID = config("TELEGRAM_CHAT_ID")
TELEGRAM_BOT_TOKEN = config("TELEGRAM_BOT_TOKEN")

bot = Bot(token=TELEGRAM_BOT_TOKEN)


async def send_alert(message: str) -> None:
    """Send an alert message to the configured Telegram chat.

    Args:
        message: The message to send
    """
    await bot.send_message(
        chat_id=TELEGRAM_CHAT_ID,
        text=message,
    )
