import json
import re
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from uuid import uuid4

from sqlalchemy import URL, create_engine, func, literal_column, select, update
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.pool import NullPool

from utils.database import application_types, media, messages, metadata, requests, settings, users

MEDIA_EXPIRED = "Фото больше недоступно: срок хранения истёк из-за ограничения в 1000 вложений."


class Storage:
    def __init__(self, directory, *, media_limit=1000):
        self.directory = Path(directory).resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.media_directory = self.directory / "media"
        self.media_directory.mkdir(exist_ok=True)
        self.path = self.directory / "decanat.sqlite3"
        self.media_limit = max(1, min(int(media_limit), 1000))
        self.lock = RLock()
        self.engine = create_engine(
            URL.create("sqlite+pysqlite", database=str(self.path)),
            connect_args={"timeout": 30, "check_same_thread": False},
            poolclass=NullPool,
        )
        with self.engine.connect() as connection:
            connection.exec_driver_sql("PRAGMA journal_mode=WAL")
        metadata.create_all(self.engine)
        self.prune_media()

    @contextmanager
    def connection(self):
        with self.lock, self.engine.begin() as connection:
            connection.exec_driver_sql("PRAGMA busy_timeout=30000")
            yield connection

    def save_user(self, user):
        profile = {
            name: getattr(user, name, None)
            for name in ("id", "first_name", "last_name", "username", "language_code", "is_bot")
        }
        profile["is_bot"] = bool(profile["is_bot"])
        statement = insert(users).values(id=user.id, profile=json.dumps(profile, ensure_ascii=False))
        with self.connection() as connection:
            connection.execute(
                statement.on_conflict_do_update(
                    index_elements=[users.c.id], set_={"profile": statement.excluded.profile}
                )
            )

    def secret_key(self):
        from secrets import token_hex

        with self.connection() as connection:
            connection.execute(
                insert(settings).values(key="session_secret", value=token_hex(32)).on_conflict_do_nothing()
            )
            return connection.scalar(select(settings.c.value).where(settings.c.key == "session_secret"))

    def get_user(self, user_id):
        with self.connection() as connection:
            profile = connection.scalar(select(users.c.profile).where(users.c.id == user_id))
            return json.loads(profile) if profile else None

    def media_path(self, tag):
        if not isinstance(tag, str) or not re.fullmatch(r"[a-f0-9]{32}\.(jpg|png|pdf|webp|gif|bin)", tag):
            raise ValueError("Недопустимое имя вложения.")
        return self.media_directory / tag

    def save_media(self, owner_id, data, mimetype="image/jpeg"):
        suffix = {
            "image/jpeg": "jpg",
            "image/png": "png",
            "application/pdf": "pdf",
            "image/webp": "webp",
            "image/gif": "gif",
        }.get(mimetype, "bin")
        tag = f"{uuid4().hex}.{suffix}"
        with self.connection() as connection:
            connection.exec_driver_sql("BEGIN IMMEDIATE")
            path = self.media_path(tag)
            try:
                path.write_bytes(data)
                connection.execute(
                    insert(media).values(
                        tag=tag,
                        owner_id=owner_id,
                        mimetype=mimetype,
                        created_at=datetime.now(timezone.utc).isoformat(),
                    )
                )
                self._prune(connection)
            except Exception:
                path.unlink(missing_ok=True)
                raise
        return tag

    def _prune(self, connection):
        excess = (
            connection.scalar(select(func.count()).select_from(media).where(media.c.expired == 0))
            - self.media_limit
        )
        if excess > 0:
            tags = connection.scalars(
                select(media.c.tag).where(media.c.expired == 0).order_by(media.c.sequence).limit(excess)
            ).all()
            for tag in tags:
                self.media_path(tag).unlink(missing_ok=True)
                connection.execute(update(media).where(media.c.tag == tag).values(expired=1))

    def prune_media(self):
        with self.connection() as connection:
            connection.exec_driver_sql("BEGIN IMMEDIATE")
            live = set(connection.scalars(select(media.c.tag).where(media.c.expired == 0)))
            for tag in live:
                if not self.media_path(tag).is_file():
                    connection.execute(update(media).where(media.c.tag == tag).values(expired=1))
            for path in self.media_directory.iterdir():
                if (
                    path.is_file()
                    and re.fullmatch(r"[a-f0-9]{32}\.(jpg|png|pdf|webp|gif|bin)", path.name)
                    and path.name not in live
                ):
                    path.unlink(missing_ok=True)
            self._prune(connection)

    def media_info(self, tag, owner_id=None):
        with self.connection() as connection:
            row = connection.execute(select(media).where(media.c.tag == tag)).mappings().first()
            if row is None or (owner_id is not None and row["owner_id"] != owner_id):
                return None
            result = dict(row)
            result["expired"] = bool(row["expired"]) or not self.media_path(tag).is_file()
            return result

    def read_media(self, tag, owner_id=None):
        info = self.media_info(tag, owner_id) if tag else None
        if not info or info["expired"]:
            return None
        try:
            return self.media_path(tag).read_bytes()
        except FileNotFoundError:
            return None

    def add_message(
        self, user_id, role, text, *, request_id=None, media_tag=None, source="web", external_id=None
    ):
        statement = insert(messages).values(
            user_id=user_id,
            role=role,
            text=text or "",
            request_id=request_id,
            media_tag=media_tag,
            source=source,
            external_id=external_id,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        statement = statement.on_conflict_do_update(
            index_elements=[messages.c.external_id],
            set_={
                "text": statement.excluded.text,
                "media_tag": func.coalesce(statement.excluded.media_tag, messages.c.media_tag),
            },
        ).returning(messages.c.id)
        with self.connection() as connection:
            message_id = connection.scalar(statement)
        return self.get_message(user_id, message_id)

    def _message(self, row):
        item = dict(row)
        item["time"] = datetime.fromisoformat(item["created_at"]).astimezone().strftime("%d.%m.%Y %H:%M")
        item["photo"] = bool(item["media_tag"])
        expired = item.pop("stored_expired")
        item["photo_expired"] = bool(item["media_tag"]) and (
            expired is None or bool(expired) or not self.media_path(item["media_tag"]).is_file()
        )
        return item

    @staticmethod
    def _message_query(user_id):
        return (
            select(messages, media.c.mimetype, media.c.expired.label("stored_expired"))
            .outerjoin(
                media, (media.c.tag == messages.c.media_tag) & (media.c.owner_id == messages.c.user_id)
            )
            .where(messages.c.user_id == user_id)
        )

    def get_message(self, user_id, message_id):
        with self.connection() as connection:
            row = (
                connection.execute(self._message_query(user_id).where(messages.c.id == message_id))
                .mappings()
                .first()
            )
        return self._message(row) if row else None

    def messages(self, user_id, after=0):
        with self.connection() as connection:
            rows = (
                connection.execute(
                    self._message_query(user_id).where(messages.c.id > after).order_by(messages.c.id)
                )
                .mappings()
                .all()
            )
        return [self._message(row) for row in rows]

    def save_request(self, item):
        statement = insert(requests).values(
            id=item["id"], user_id=item["user_id"], payload=json.dumps(item, ensure_ascii=False)
        )
        with self.connection() as connection:
            connection.execute(
                statement.on_conflict_do_update(
                    index_elements=[requests.c.id], set_={"payload": statement.excluded.payload}
                )
            )

    def request(self, request_id, user_id=None):
        statement = select(requests.c.payload).where(requests.c.id == request_id)
        if user_id is not None:
            statement = statement.where(requests.c.user_id == user_id)
        with self.connection() as connection:
            payload = connection.scalar(statement)
        return json.loads(payload) if payload else None

    def requests(self, user_id):
        with self.connection() as connection:
            payloads = connection.scalars(
                select(requests.c.payload)
                .where(requests.c.user_id == user_id)
                .order_by(literal_column("rowid"))
            ).all()
        return [json.loads(payload) for payload in payloads]

    def recover_requests(self):
        with self.connection() as connection:
            payloads = connection.scalars(select(requests.c.payload)).all()
        for payload in payloads:
            item = json.loads(payload)
            changed = False
            if item["status"] == "processing":
                item.update(
                    status="failed",
                    retry_allowed=False,
                    failure_message="Обработка прервана перезапуском. Проверь получение в деканате перед повторной отправкой.",
                )
                changed = True
            if item.get("ai_status") == "processing":
                item["ai_status"] = "failed"
                changed = True
            if changed:
                self.save_request(item)

    def custom_types(self, *, include_deleted=False):
        statement = select(application_types).order_by(literal_column("rowid"))
        if not include_deleted:
            statement = statement.where(application_types.c.active == 1)
        with self.connection() as connection:
            rows = connection.execute(statement).mappings().all()
        return [
            {
                "key": row["id"],
                "label": row["label"],
                "modes": json.loads(row["modes"]),
                "custom": True,
                "active": bool(row["active"]),
            }
            for row in rows
        ]

    def add_type(self, label, modes, created_by):
        key = "custom_" + uuid4().hex[:16]
        with self.connection() as connection:
            connection.execute(
                insert(application_types).values(
                    id=key, label=label, modes=json.dumps(modes), created_by=created_by
                )
            )
        return key

    def delete_type(self, key):
        if not key.startswith("custom_"):
            raise ValueError("Стандартные типы документов нельзя удалить.")
        with self.connection() as connection:
            result = connection.execute(
                update(application_types)
                .where(application_types.c.id == key, application_types.c.active == 1)
                .values(active=0)
            )
            return bool(result.rowcount)
