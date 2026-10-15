from sqlalchemy.exc import IntegrityError

from utils.document_fields import DOCUMENT_SCHEMAS, normalize_document_type

MODES = ("application", "application_question")


class ApplicationTypes:
    def __init__(self, storage):
        self.storage = storage

    def list(self, mode=None):
        items = [
            {"key": key, "label": schema["label"], "modes": list(MODES), "custom": False}
            for key, schema in DOCUMENT_SCHEMAS.items()
        ]
        items.extend(self.storage.custom_types())
        return [item for item in items if mode is None or mode in item["modes"]]

    def resolve(self, value, mode=None):
        builtin = normalize_document_type(value)
        return next(
            (
                item
                for item in self.list(mode)
                if value in (item["key"], item["label"]) or (not item["custom"] and item["key"] == builtin)
            ),
            None,
        )

    def add(self, label, modes, created_by):
        label = " ".join(label.split()) if isinstance(label, str) else ""
        if not 1 <= len(label) <= 80 or label.startswith("/"):
            raise ValueError("Название должно содержать от 1 до 80 символов и не быть командой.")
        if (
            normalize_document_type(label)
            or label
            in {
                "⬅ Назад в меню",
                "❔ Задать вопрос деканату",
                "📝 Отправить заявление",
                "🔍 Задать вопрос по заявлению",
            }
            or any(ord(character) < 32 for character in label)
        ):
            raise ValueError("Это название уже используется или зарезервировано для меню.")
        modes = [mode for mode in MODES if mode in modes]
        if not modes:
            raise ValueError("Выбери хотя бы один раздел.")
        if any(item["label"].casefold() == label.casefold() for item in self.list()):
            raise ValueError("Такой тип документа уже существует.")
        try:
            return self.storage.add_type(label, modes, created_by)
        except IntegrityError:
            raise ValueError("Такое название уже использовалось. Выбери другое.") from None

    def delete(self, key):
        return self.storage.delete_type(key)
