import asyncio
from threading import Thread
from handlers import register_handlers
from utils.bot import Bot
from utils.config import get_admin_log_chat_id, get_dean_chat_id, get_token
from webapp import create_app, run_server


async def main():
    bot = Bot(get_token(), get_admin_log_chat_id())
    dean_chat_id = get_dean_chat_id()
    loop = asyncio.get_running_loop()
    register_handlers(bot, dean_chat_id)
    loop.set_exception_handler(bot.handle_loop_error)
    bot.start_logs()
    try:
        identity = await bot.get_me()
        bot.write_log(f"Подключение к Telegram установлено: @{identity.username}")
    except Exception as error:
        bot.log_error(error, "Не удалось проверить подключение к Telegram")
    app = create_app(bot, loop, dean_chat_id)
    Thread(target=run_server, args=(app, bot, loop), daemon=True).start()

    await bot.infinity_polling(allowed_updates=["message", "callback_query"])


if __name__ == "__main__":
    asyncio.run(main())
