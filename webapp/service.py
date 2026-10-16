from asyncio import Lock, create_task, run_coroutine_threadsafe, to_thread
from io import BytesIO

from utils import delivery
from utils.bot import current_web_user
from utils.document_fields import document_label, empty_extraction
from utils.document_logging import log_document_failure, log_document_result
from utils.document_policy import preliminary_check
from utils.keyboards import answer_web_student_menu
from utils.ocr import analyze_document_async, reprocess_document
from utils.question_answering import answer_question_async, format_answer
from utils.users import get_name

from .store import WebStore


class WebService:
    def __init__(self, bot, loop, dean_chat_id, storage=None):
        self.bot = bot
        self.loop = loop
        self.dean_chat_id = dean_chat_id
        self.store = WebStore(storage)
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

        self.bot.write_log(
            f"Создана ссылка для входа на сайт\nПользователь: {get_name(user)}\nID пользователя: {user.id}"
        )
        from utils.config import get_public_base_url

        return f"{get_public_base_url()}/login?code={code}"

    async def audit(self, user_id, action, text=""):
        user = self.store.get_user(user_id)
        self.bot.write_log(
            f"{action}\nИсточник: сайт\nПользователь: {get_name(user)}\nID пользователя: {user_id}\n\n{text}"
        )

    async def report_error(self, error, user_id=None):
        if user_id is not None:
            await self.audit(user_id, "Ошибка сайта", type(error).__name__)

        self.bot.log_error(error, "Ошибка сайта")

    async def submit(self, user_id, mode, text, application_type, photo, mimetype, filename=""):
        if mode == "general_question":
            mode = "question"
        if mode not in ("question", "application", "application_question"):
            raise ValueError("Выбери раздел обращения.")
        if mode != "application" and (
            not isinstance(text, str) or not text.strip() or len(text) > 4096 or text.lstrip().startswith("/")
        ):
            raise ValueError("Напиши вопрос обычным текстом, до 4096 символов.")
        if mode != "question":
            if not photo:
                raise ValueError("Прикрепи один документ JPG, PNG или PDF.")
            if mode == "application":
                text = ""
            selected = self.store.types.resolve(application_type, mode)
            if selected is None:
                raise ValueError("Выбери поддерживаемый тип документа.")
            application_type = selected["label"]
        async with self.locks.setdefault(user_id, Lock()):
            snapshot = self.store.snapshot(user_id)

            if any(item["status"] == "processing" for item in snapshot["requests"]):
                raise ValueError("Подожди, пока отправится текущее обращение.")

            request_id = self.store.create_request(user_id, mode, text, application_type, photo, mimetype)
            self.store.update_request(
                request_id, filename=(filename or "document").replace("\\", "/").rsplit("/", 1)[-1][:255]
            )
            body = text if mode == "question" else f"{application_type}\n\n{text}".strip()
            if mode != "application":
                self.store.add_message(user_id, "student", body, request_id, photo=photo is not None)
            await self.audit(
                user_id,
                "Пользователь → сайт",
                f"Обращение: {request_id}\n{body}" + ("\nПрикреплено фото." if photo else ""),
            )
            task = create_task(self.process(user_id, request_id))
            self.tasks.add(task)
            task.add_done_callback(self.tasks.discard)
            return request_id

    def save_analysis(self, request_id, analysis):
        keys = (
            "recognized",
            "ocr_text",
            "visuals",
            "validation",
            "fields",
            "extraction",
            "document_type",
            "selected_document_type",
            "detected_document_type",
            "document_title",
            "document_type_matches_selection",
        )
        changes = {key: analysis[key] for key in keys}
        changes["preliminary_check"] = preliminary_check(analysis["extraction"])
        if "previews" in analysis:
            changes["previews"] = analysis["previews"]
        self.store.update_request(request_id, **changes)

    async def change_type(self, user_id, request_id, selected, mode, text):
        entry = self.store.types.resolve(selected, mode)
        if entry is None or mode not in ("application", "application_question"):
            raise ValueError("Выбери поддерживаемый тип документа и раздел.")
        selected = entry["key"]
        if not isinstance(text, str) or len(text) > 4096 or text.lstrip().startswith("/"):
            raise ValueError("Вопрос должен быть обычным текстом до 4096 символов.")
        if mode == "application_question" and not text.strip():
            raise ValueError("Напиши вопрос по заявлению.")
        async with self.locks.setdefault(user_id, Lock()):
            item = self.store.get_request(user_id, request_id)
            if item is None:
                raise LookupError("Обращение не найдено.")
            if item["status"] != "needs_review":
                raise ValueError("Изменение типа доступно только до отправки документа.")
            errors = [
                error for error in item["extraction"].get("errors", []) if error["stage"] != "extraction"
            ]
            analysis = await to_thread(
                reprocess_document,
                item["ocr_text"],
                item["visuals"],
                selected,
                errors,
                cached_extraction=item["extraction"],
            )
            self.save_analysis(request_id, analysis)
            self.store.update_request(
                request_id,
                application_type=entry["label"],
                custom_type=entry["custom"],
                mode=mode,
                type_mismatch_confirmed=False,
                text=text if mode == "application_question" else "",
            )
            log_document_result(analysis["extraction"], self.bot.write_log, "web")
            return request_id

    async def confirm(self, user_id, request_id, type_mismatch_confirmed=False):
        async with self.locks.setdefault(user_id, Lock()):
            item = self.store.get_request(user_id, request_id)
            if item is None:
                raise LookupError("Обращение не найдено.")
            if item.get("photo_tag") and item["photo"] is None:
                from utils.storage import MEDIA_EXPIRED

                raise ValueError(MEDIA_EXPIRED + " Прикрепи документ заново.")
            retry = item["status"] == "failed" and item["retry_allowed"]
            if item["status"] != "needs_review" and not retry:
                raise ValueError("Документ уже отправлен или ещё обрабатывается.")
            mismatch = item["document_type_matches_selection"] is False
            override = type_mismatch_confirmed is True or (retry and item["type_mismatch_confirmed"])
            if mismatch and not override:
                raise ValueError("Подтверди выбранный тип жёлтой кнопкой или измени его в списке.")
            if item["mode"] == "application_question" and not item["text"].strip():
                raise ValueError("Напиши вопрос по заявлению.")
            if any(request["status"] == "processing" for request in self.store.snapshot(user_id)["requests"]):
                raise ValueError("Подожди завершения текущей обработки.")
            self.store.update_request(
                request_id,
                status="processing",
                retry_allowed=False,
                type_mismatch_confirmed=mismatch and override,
                failure_message="",
            )
            task = create_task(self.process(user_id, request_id, confirmed=True))
            self.tasks.add(task)
            task.add_done_callback(self.tasks.discard)
            return request_id

    async def process(self, user_id, request_id, confirmed=False):
        item = self.store.get_request(user_id, request_id)
        user = self.store.get_user(user_id)
        token = current_web_user.set(
            {"user": user, "request_id": request_id, "application_type": item["application_type"]}
        )

        try:
            markup = (
                answer_web_student_menu(self.bot.token, request_id) if item["mode"] != "application" else None
            )

            if item["mode"] == "question":
                message_id = await delivery.send_question(
                    self.bot, self.dean_chat_id, user, item["text"], markup, "сайт"
                )
                item["delivery_progress"].update(message_id=message_id, complete=True)
            else:
                with BytesIO(item["photo"]) as photo:
                    try:
                        analysis = (
                            None
                            if confirmed
                            else await analyze_document_async(photo, item["selected_document_type"])
                        )
                        if confirmed and item["document_type"] != item["selected_document_type"]:
                            errors = [
                                error
                                for error in item["extraction"].get("errors", [])
                                if error["stage"] != "extraction"
                            ]
                            analysis = await to_thread(
                                reprocess_document,
                                item["ocr_text"],
                                item["visuals"],
                                item["selected_document_type"],
                                errors,
                                use_selected_type=True,
                                cached_extraction=item["extraction"],
                            )
                            analysis["extraction"]["type_mismatch_confirmed"] = item[
                                "type_mismatch_confirmed"
                            ]
                    except Exception as error:
                        failed = empty_extraction(
                            item["document_type"], [{"stage": "ocr", "code": type(error).__name__}]
                        )
                        failed["field_statuses"] = {name: "unknown" for name in failed["fields"]}
                        self.store.update_request(request_id, fields=failed["fields"], extraction=failed)
                        log_document_failure(item["document_type"], error, self.bot.write_log, "web")
                        raise
                    if analysis is not None:
                        self.save_analysis(request_id, analysis)
                        log_document_result(analysis["extraction"], self.bot.write_log, "web")
                    item = self.store.get_request(user_id, request_id)
                    if not confirmed and item["document_type_matches_selection"] is False:
                        self.store.update_request(request_id, status="needs_review")
                        return
                    if item["mode"] == "application_question" and not any(
                        message["request_id"] == request_id and message["role"] == "student"
                        for message in self.store.storage.messages(user_id)
                    ):
                        self.store.add_message(user_id, "student", item["text"], request_id, photo=True)
                    await delivery.send_application(
                        self.bot,
                        self.dean_chat_id,
                        user,
                        photo,
                        item["application_type"]
                        if item.get("custom_type")
                        else document_label(item["document_type"]),
                        item["extraction"],
                        item["text"] if item["mode"] == "application_question" else None,
                        markup,
                        "сайт",
                        visuals=item["visuals"],
                        mimetype=item["mimetype"],
                        progress=item["delivery_progress"],
                    )

            self.store.update_request(request_id, delivery_progress=item["delivery_progress"])
            if item["mode"] == "application_question":
                self.store.update_request(request_id, status="checked")
            self.store.mark_sent(request_id)
            if item["mode"] != "application":
                await self.answer_ai(
                    user_id, request_id, item["text"], item["delivery_progress"]["message_id"]
                )
            text = (
                "Документ отправлен в деканат."
                if item["mode"] == "application"
                else "Вопрос отправлен в деканат. Ответ появится здесь, в чате."
            )
            await self.audit(user_id, "Бот → сайт", f"Обращение: {request_id}\n\n{text}")
        except Exception as error:
            progress = item.get("delivery_progress", {})
            retry_allowed = (
                bool(item.get("visuals")) and bool(item.get("extraction")) and not progress.get("uncertain")
            )

            if progress.get("complete"):
                self.store.mark_sent(request_id)
                return
            self.store.update_request(request_id, status="failed", retry_allowed=retry_allowed)
            self.bot.log_error(error, "Ошибка отправки обращения с сайта")
            text = (
                "Не удалось отправить обращение. Можно повторить отправку сохранённого документа."
                if retry_allowed
                else "Не удалось подтвердить отправку. Проверь получение в деканате перед новой отправкой."
            )
            if item["mode"] == "question":
                text = "Не удалось отправить вопрос. Попробуй ещё раз чуть позже."
            elif not item.get("visuals"):
                text = (
                    "Не удалось обработать документ. Проверь файл и настройку OCR перед повторной загрузкой."
                )
            self.store.update_request(request_id, failure_message=text)
            await self.audit(user_id, "Бот → сайт", text)
        finally:
            self.store.update_request(request_id, delivery_progress=item.get("delivery_progress", {}))
            current_web_user.reset(token)

    async def deliver_reply(self, request_id, text):
        user_id = self.store.reply_target(request_id)

        if user_id is None:
            raise OSError("Обращение с сайта больше недоступно.")

        self.store.update_request(request_id, status="answered")
        self.store.add_message(user_id, "dean", text, request_id)
        await self.audit(user_id, "Деканат → сайт", f"Обращение: {request_id}\n\n{text}")

    async def ask_ai(self, user_id, request_id, text):
        if not isinstance(text, str) or not text.strip() or len(text) > 4096 or text.lstrip().startswith("/"):
            raise ValueError("Напиши вопрос длиной до 4096 символов.")
        async with self.locks.setdefault(user_id, Lock()):
            item = self.store.get_request(user_id, request_id)
            if item is None:
                raise LookupError("Документ не найден.")
            if item["mode"] != "application_question":
                raise ValueError("AI-вопросы доступны только в разделе «Задать вопрос по заявлению».")
            if not item.get("extraction") or item["status"] not in ("checked", "answered"):
                raise ValueError("Дождись обработки и подтверждения типа документа.")
            if item["ai_status"] == "processing":
                raise ValueError("Дождись ответа AI-помощника.")
            self.store.update_request(request_id, ai_status="processing", ai_answer=None)
            self.store.add_message(user_id, "student", text.strip(), request_id)
            task = create_task(self.followup(user_id, request_id, text.strip()))
            self.tasks.add(task)
            task.add_done_callback(self.tasks.discard)
            return request_id

    async def followup(self, user_id, request_id, text):
        item = self.store.get_request(user_id, request_id)
        message_id = item["delivery_progress"].get("message_id")
        try:
            message_id = await delivery.send_question(
                self.bot,
                self.dean_chat_id,
                self.store.get_user(user_id),
                text,
                answer_web_student_menu(self.bot.token, request_id),
                "сайт, уточнение по заявлению",
                reply_to_message_id=message_id,
            )
        except Exception as error:
            self.bot.log_error(error, "Не удалось передать уточнение в деканат")
        await self.answer_ai(user_id, request_id, text, message_id)

    async def answer_ai(self, user_id, request_id, text, dean_message_id):
        self.store.update_request(request_id, ai_status="processing", ai_answer=None)
        try:
            item = self.store.get_request(user_id, request_id)
            answer = await answer_question_async(
                {"question_id": request_id + "-question", "question": text}, item["extraction"]
            )
            self.store.update_request(request_id, ai_status="done", ai_answer=answer)
            response = format_answer(answer)
        except Exception as error:
            self.store.update_request(request_id, ai_status="failed")
            response = "AI-помощник не смог проверить ответ. Вопрос передан в деканат."
            self.bot.write_log("AI question failed: " + type(error).__name__)
        self.store.add_message(user_id, "ai", response, request_id)
        await delivery.notify_ai_answer(self.bot, self.dean_chat_id, dean_message_id, response)
