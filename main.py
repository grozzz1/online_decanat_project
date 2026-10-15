import asyncio
import logging
from threading import Thread

from handlers import register_handlers
from utils.bot import Bot
from utils.config import get_admin_log_chat_id, get_dean_chat_id, get_public_base_url, get_token
from webapp import create_app, create_server


async def main() -> None:
    bot = Bot(get_token(), get_admin_log_chat_id())
    dean_chat_id = get_dean_chat_id()
    loop = asyncio.get_running_loop()
    register_handlers(bot, dean_chat_id)
    loop.set_exception_handler(bot.handle_loop_error)
    bot.start_logs()
    server = None
    try:
        try:
            identity = await asyncio.wait_for(bot.get_me(), timeout=15)
            bot.write_log(f"Подключение к Telegram установлено: @{identity.username}")
        except Exception as error:
            bot.log_error(error, "Не удалось проверить подключение к Telegram")
        app = create_app(bot, loop, dean_chat_id)
        server = create_server(app)
        Thread(target=server.run, daemon=True).start()
        bot.write_log(f"Сайт запущен: {get_public_base_url()}")
        await bot.infinity_polling(allowed_updates=["message", "callback_query"])
    finally:
        if server is not None:
            await asyncio.to_thread(server.close)
        tasks = list(bot.web.tasks) if bot.web is not None else []
        if bot.log_task is not None:
            tasks.append(bot.log_task)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await bot.close_session()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    asyncio.run(main())
