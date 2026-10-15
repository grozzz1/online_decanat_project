from secrets import token_urlsafe
from threading import RLock
from time import monotonic

from telebot.types import User

from utils.application_types import ApplicationTypes
from utils.config import get_data_directory
from utils.document_fields import normalize_document_type
from utils.storage import Storage


class WebStore:
    def __init__(self, storage=None):
        self.lock = RLock()
        self.storage = storage if storage is not None else Storage(get_data_directory())
        self.types = ApplicationTypes(self.storage)
        self.codes = {}
        self.sessions = {}
        self.users = {}
        self.storage.recover_requests()

    def register_user(self, user):
        with self.lock:
            self.storage.save_user(user)
            self.users.setdefault(user.id, {"user": user, "avatar": None})["user"] = user

    def create_login(self, user):
        with self.lock:
            self.register_user(user)
            self.codes = {
                code: entry
                for code, entry in self.codes.items()
                if entry[0] != user.id and entry[1] > monotonic()
            }
            code = token_urlsafe(24)
            self.codes[code] = (user.id, monotonic() + 600)
            return code

    def create_session(self, user):
        with self.lock:
            self.register_user(user)
            token = token_urlsafe(32)
            self.sessions[token] = {"user_id": user.id, "csrf": token_urlsafe(24)}
            return token

    def set_avatar(self, user_id, avatar):
        with self.lock:
            self.users[user_id]["avatar"] = avatar

    def login(self, code):
        with self.lock:
            entry = self.codes.pop(code, None)
            if entry is None or entry[1] <= monotonic():
                return None
            return self.create_session(self.get_user(entry[0]))

    def get_session(self, token):
        with self.lock:
            item = self.sessions.get(token)
            return dict(item) if item is not None else None

    def logout(self, token):
        with self.lock:
            self.sessions.pop(token, None)

    def get_user(self, user_id):
        with self.lock:
            if user_id not in self.users:
                profile = self.storage.get_user(user_id)
                if profile is None:
                    raise LookupError("Пользователь не найден.")
                self.users[user_id] = {"user": User.de_json(profile), "avatar": None}
            return self.users[user_id]["user"]

    def get_avatar(self, user_id):
        with self.lock:
            self.get_user(user_id)
            return self.users[user_id]["avatar"]

    def add_message(
        self,
        user_id,
        role,
        text,
        request_id=None,
        photo=False,
        *,
        media_tag=None,
        source="web",
        external_id=None,
    ):
        if photo and request_id and media_tag is None:
            item = self.storage.request(request_id, user_id)
            media_tag = item.get("photo_tag") if item else None
        return self.storage.add_message(
            user_id,
            role,
            text,
            request_id=request_id,
            media_tag=media_tag,
            source=source,
            external_id=external_id,
        )

    def create_request(self, user_id, mode, text, application_type, photo, mimetype):
        with self.lock:
            selected = self.types.resolve(application_type)
            selected_key = selected["key"] if selected else normalize_document_type(application_type)
            item = {
                "id": token_urlsafe(12),
                "user_id": user_id,
                "mode": mode,
                "text": text,
                "application_type": application_type,
                "filename": "",
                "document_type": selected_key,
                "selected_document_type": selected_key,
                "detected_document_type": None,
                "custom_type": bool(selected and selected["custom"]),
                "document_title": None,
                "document_type_matches_selection": None,
                "type_mismatch_confirmed": False,
                "retry_allowed": False,
                "failure_message": "",
                "delivery_progress": {},
                "ocr_text": "",
                "photo_tag": None,
                "mimetype": mimetype,
                "status": "processing",
                "recognized": "",
                "visuals": None,
                "validation": None,
                "preliminary_check": None,
                "preview_count": 0,
                "fields": {},
                "extraction": None,
                "ai_status": "idle",
                "ai_answer": None,
            }
            if photo is not None:
                item["photo_tag"] = self.storage.save_media(user_id, photo, mimetype)
            self.storage.save_request(item)
            return item["id"]

    def update_request(self, request_id, **changes):
        with self.lock:
            item = self.storage.request(request_id)
            if item is None:
                raise LookupError("Обращение не найдено.")
            if "previews" in changes:
                item["preview_count"] = len(changes.pop("previews"))
            if "photo" in changes:
                data = changes.pop("photo")
                item["photo_tag"] = (
                    self.storage.save_media(item["user_id"], data, changes.get("mimetype", item["mimetype"]))
                    if data
                    else None
                )
            item.update(changes)
            self.storage.save_request(item)

    def mark_sent(self, request_id):
        with self.lock:
            item = self.storage.request(request_id)
            if item and item["status"] == "processing":
                self.update_request(request_id, status="sent")

    def get_request(self, user_id, request_id):
        with self.lock:
            item = self.storage.request(request_id, user_id)
            if item is None:
                return None
            item["photo"] = self.storage.read_media(item.get("photo_tag"), user_id)
            item["previews"] = [None] * item.get("preview_count", len(item.get("preview_tags", [])))
            return item

    def reply_target(self, request_id):
        item = self.storage.request(request_id)
        if not item or item["mode"] != "question" or item["status"] == "failed":
            return None
        return item["user_id"]

    def snapshot(self, user_id, after=0):
        requests = []
        for item in self.storage.requests(user_id):
            item.pop("user_id", None)
            item.pop("delivery_progress", None)
            item.pop("ocr_text", None)
            item.pop("preview_tags", None)
            media = self.storage.media_info(item.get("photo_tag"), user_id) if item.get("photo_tag") else None
            item["photo_expired"] = bool(item.get("photo_tag")) and (not media or media["expired"])
            requests.append(item)
        return {
            "messages": self.storage.messages(user_id, after),
            "requests": requests,
            "document_types": self.types.list(),
        }
