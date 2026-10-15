from datetime import datetime
from secrets import token_urlsafe
from threading import RLock

from utils.document_fields import normalize_document_type


class WebStore:
    def __init__(self):
        self.lock = RLock()
        self.codes = {}
        self.users = {}
        self.sessions = {}
        self.messages = {}
        self.requests = {}

    def create_login(self, user):
        with self.lock:
            self.codes = {code: user_id for code, user_id in self.codes.items() if user_id != user.id}
            code = token_urlsafe(24)
            self.codes[code] = user.id
            self.users.setdefault(user.id, {"user": user, "avatar": None})["user"] = user
            self.messages.setdefault(user.id, [])
            return code

    def set_avatar(self, user_id, avatar):
        with self.lock:
            self.users[user_id]["avatar"] = avatar

    def login(self, code):
        with self.lock:
            user_id = self.codes.pop(code, None)

            if user_id is None:
                return None

            token = token_urlsafe(32)
            self.sessions[token] = {"user_id": user_id, "csrf": token_urlsafe(24)}
            return token

    def get_session(self, token):
        with self.lock:
            session = self.sessions.get(token)
            return dict(session) if session is not None else None

    def logout(self, token):
        with self.lock:
            self.sessions.pop(token, None)

    def get_user(self, user_id):
        with self.lock:
            return self.users[user_id]["user"]

    def get_avatar(self, user_id):
        with self.lock:
            return self.users[user_id]["avatar"]

    def add_message(self, user_id, role, text, request_id=None, photo=False):
        with self.lock:
            messages = self.messages[user_id]
            message = {
                "id": len(messages) + 1,
                "role": role,
                "text": text,
                "time": datetime.now().strftime("%H:%M"),
                "request_id": request_id,
                "photo": photo,
            }
            messages.append(message)
            return dict(message)

    def create_request(self, user_id, mode, text, application_type, photo, mimetype):
        with self.lock:
            request_id = token_urlsafe(12)
            self.requests[request_id] = {
                "id": request_id,
                "user_id": user_id,
                "mode": mode,
                "text": text,
                "application_type": application_type,
                "document_type": normalize_document_type(application_type),
                "selected_document_type": normalize_document_type(application_type),
                "detected_document_type": None,
                "document_title": None,
                "document_type_matches_selection": None,
                "type_mismatch_confirmed": False,
                "retry_allowed": False,
                "failure_message": "",
                "delivery_progress": {},
                "ocr_text": "",
                "photo": photo,
                "mimetype": mimetype,
                "status": "processing",
                "recognized": "",
                "visuals": None,
                "validation": None,
                "previews": [],
                "fields": {},
                "extraction": None,
                "ai_status": "idle",
                "ai_answer": None,
            }
            return request_id

    def update_request(self, request_id, **changes):
        with self.lock:
            self.requests[request_id].update(changes)

    def mark_sent(self, request_id):
        with self.lock:
            item = self.requests[request_id]

            if item["status"] == "processing":
                item["status"] = "sent"

    def get_request(self, user_id, request_id):
        with self.lock:
            item = self.requests.get(request_id)
            return dict(item) if item is not None and item["user_id"] == user_id else None

    def reply_target(self, request_id):
        with self.lock:
            item = self.requests.get(request_id)

            if item is None or item["mode"] == "application" or item["status"] == "failed":
                return None

            return item["user_id"]

    def snapshot(self, user_id, after=0):
        with self.lock:
            return {
                "messages": [dict(message) for message in self.messages[user_id] if message["id"] > after],
                "requests": [
                    {
                        key: item[key]
                        for key in (
                            "id",
                            "mode",
                            "application_type",
                            "document_type",
                            "status",
                            "recognized",
                            "visuals",
                            "validation",
                            "mimetype",
                            "fields",
                            "extraction",
                            "selected_document_type",
                            "detected_document_type",
                            "document_title",
                            "document_type_matches_selection",
                            "type_mismatch_confirmed",
                            "retry_allowed",
                            "failure_message",
                            "ai_status",
                            "ai_answer",
                        )
                    }
                    for item in self.requests.values()
                    if item["user_id"] == user_id
                ],
            }
