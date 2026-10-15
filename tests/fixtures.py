from io import BytesIO

from PIL import Image, ImageDraw, ImageFont

TEXTS = {
    "study_certificate": """Справка № 45/26 от 02.09.2026
ФИО студента: Иванов Иван Иванович
ВУЗ: Московский технический университет
Направление подготовки: 09.03.01 Информатика и вычислительная техника
Форма обучения: очная
Период обучения с 01.09.2024 по 30.06.2028
Подпись: __________ Печать
""",
    "academic_leave_application": """ФИО: Петров Петр Петрович
Группа: ИКБО-01-24
Курс: 2
Причина отпуска: по семейным обстоятельствам
Прошу предоставить академический отпуск с 15.10.2026 по 15.10.2027
Дата заявления: 14.10.2026
Подпись студента: __________
""",
    "internship_certificate": """Номер документа: ПР-123
Дата выдачи: 20.08.2026
Студент: Сидорова Анна Сергеевна
Организация: ООО «Технологии»
Прошла практику с 01.07.2026 по 15.08.2026
Руководитель практики: Смирнов Алексей Николаевич
Оценка: отлично
Подпись: _________ Печать
""",
}


def confirmed_visuals():
    found = {"detected": True, "status": "detected", "confidence": 0.8, "role": "student"}
    return {
        "stamp": dict(found),
        "signature": dict(found),
        "student_signature": dict(found),
        "pages": [{"analysis_status": "complete"}],
        "warnings": [],
        "detections": [],
    }


FONT = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 22)


def page(stamp=False, signature=False, staff=False, multiple=False, text=True):
    image = Image.new("RGB", (800, 1100), "white")
    draw = ImageDraw.Draw(image)
    words = []
    if text:
        draw.text((70, 90), "ЗАЯВЛЕНИЕ", font=FONT, fill="black")
        for y in range(160, 410, 42):
            draw.text((70, y), "Прошу предоставить академический отпуск.", font=FONT, fill="black")
    if stamp:
        draw.ellipse((490, 720, 660, 890), outline=(25, 60, 180), width=4)
        draw.ellipse((505, 735, 645, 875), outline=(25, 60, 180), width=3)
        draw.text((515, 795), "ДЕКАНАТ", font=FONT, fill=(25, 60, 180))
    if signature:
        label = "Подпись декана" if staff else "Подпись студента"
        draw.text((70, 850), label, font=FONT, fill="black")
        offset = 70
        for token in label.split():
            box = draw.textbbox((offset, 850), token, font=FONT)
            words.append({"text": token, "confidence": 96, "bbox": list(box), "line": [1, 1, 1]})
            offset += round(draw.textlength(token + " ", font=FONT))
        draw_signature(draw, 110, 790)
        if multiple:
            draw_signature(draw, 380, 945)
    return image, words


def draw_signature(draw, x, y):
    points = [
        (x, y + 45),
        (x + 22, y),
        (x + 34, y + 50),
        (x + 10, y + 30),
        (x + 55, y + 13),
        (x + 46, y + 45),
        (x + 82, y + 7),
        (x + 69, y + 44),
        (x + 110, y + 22),
        (x + 92, y + 47),
        (x + 142, y + 10),
        (x + 125, y + 40),
        (x + 170, y + 25),
    ]
    draw.line(points, fill=(15, 40, 130), width=3)


def encoded(image, format="PNG"):
    output = BytesIO()
    image.save(output, format=format)
    return output.getvalue()
