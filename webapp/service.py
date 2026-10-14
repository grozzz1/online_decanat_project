from asyncio import Lock, create_task, run_coroutine_threadsafe
from io import BytesIO
from utils import delivery
from utils.bot import current_web_user
from utils.keyboards import answer_web_student_menu
from utils.ocr import analyze_document_async
from utils.users import get_name
from .store import WebStore


class WebService:
    def __init__(self, bot, loop, dean_chat_id):
        self.bot = bot
        self.loop = loop
        self.dean_chat_id = dean_chat_id
        self.store = WebStore()
        self.locks = {}
        self.tasks = set()

    def call(self, coroutine):
        return run_coroutine_threadsafe(coroutine, self.loop).result()

    async def create_login(self, user):
        code = self.store.create_login(user)

        try:
            photos = await self.bot.get_user_profile_photos(user.id, limit=1)
            avatar = None

            if photos.photos:
                file = await self.bot.get_file(photos.photos[0][-1].file_id)
                avatar = await self.bot.download_file(file.file_path)

            self.store.set_avatar(user.id, avatar)
        except Exception as error:
            self.bot.log_error(error, "Не удалось получить фото профиля для сайта")

        self.bot.write_log(f"Создана ссылка для входа на сайт\nПользователь: {get_name(user)}\nID пользователя: {user.id}")
        return f"http://localhost:5000/login?code={code}"

    async def audit(self, user_id, action, text=""):
        user = self.store.get_user(user_id)
        self.bot.write_log(f"{action}\nИсточник: сайт\nПользователь: {get_name(user)}\nID пользователя: {user_id}\n\n{text}")

    async def report_error(self, error, user_id=None):
        if user_id is not None:
            await self.audit(user_id, "Ошибка сайта", type(error).__name__)

        self.bot.log_error(error, "Ошибка сайта")

    async def submit(self, user_id, mode, text, application_type, photo, mimetype):
        async with self.locks.setdefault(user_id, Lock()):
            snapshot = self.store.snapshot(user_id)

            if any(item["status"] == "processing" for item in snapshot["requests"]):
                raise ValueError("Подожди, пока отправится текущее обращение.")

            request_id = self.store.create_request(user_id, mode, text, application_type, photo, mimetype)
            body = text if mode == "question" else f"{application_type}\n\n{text}".strip()
            self.store.add_message(user_id, "student", body, request_id, photo=photo is not None)
            await self.audit(user_id, "Пользователь → сайт", f"Обращение: {request_id}\n{body}" + ("\nПрикреплено фото." if photo else ""))
            task = create_task(self.process(user_id, request_id))
            self.tasks.add(task)
            task.add_done_callback(self.tasks.discard)
            return request_id

    async def process(self, user_id, request_id):
        item = self.store.get_request(user_id, request_id)
        user = self.store.get_user(user_id)
        token = current_web_user.set({"user": user, "request_id": request_id, "application_type": item["application_type"]})

        try:
            markup = answer_web_student_menu(self.bot.token, request_id) if item["mode"] != "application" else None

            if item["mode"] == "question":
                await delivery.send_question(self.bot, self.dean_chat_id, user, item["text"], markup, "сайт")
            else:
                with BytesIO(item["photo"]) as photo:
                    analysis = await analyze_document_async(photo, item["application_type"])
                    recognized = analysis["recognized"]
                    self.store.update_request(request_id, recognized=recognized, visuals=analysis["visuals"],
                                              validation=analysis["validation"], previews=analysis["previews"])
                    await self.audit(user_id, "Распознан текст фото", f"Обращение: {request_id}\n\n{recognized or 'Не удалось распознать текст.'}")
                    await delivery.send_application(
                        self.bot, self.dean_chat_id, user, photo, item["application_type"], recognized,
                        item["text"] if item["mode"] == "application_question" else None,
                        markup, "сайт", visuals=analysis["visuals"], mimetype=item["mimetype"],
                    )

            self.store.mark_sent(request_id)
            text = "Заявление отправлено в деканат." if item["mode"] == "application" else "Вопрос отправлен в деканат. Ответ появится здесь, в чате."
            self.store.add_message(user_id, "bot", text, request_id)
            await self.audit(user_id, "Бот → сайт", f"Обращение: {request_id}\n\n{text}")
        except Exception as error:
            self.store.update_request(request_id, status="failed")
            self.bot.log_error(error, "Ошибка отправки обращения с сайта")
            text = "Не удалось отправить обращение. Попробуй ещё раз чуть позже."
            self.store.add_message(user_id, "bot", text, request_id)
            await self.audit(user_id, "Бот → сайт", text)
        finally:
            current_web_user.reset(token)

    async def deliver_reply(self, request_id, text):
        user_id = self.store.reply_target(request_id)

        if user_id is None:
            raise OSError("Обращение с сайта больше недоступно.")

        self.store.update_request(request_id, status="answered")
        self.store.add_message(user_id, "dean", text, request_id)
        await self.audit(user_id, "Деканат → сайт", f"Обращение: {request_id}\n\n{text}")
