"""Synthetic regression fixtures; passing these does not measure real-document accuracy."""
from io import BytesIO
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from utils.document_cv import analyze_document_visuals, find_signature_anchors
from utils.documents import MAX_PAGES, load_document_pages, validate_document
from utils.ocr import analyze_document


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
    points = [(x, y + 45), (x + 22, y), (x + 34, y + 50), (x + 10, y + 30),
              (x + 55, y + 13), (x + 46, y + 45), (x + 82, y + 7), (x + 69, y + 44),
              (x + 110, y + 22), (x + 92, y + 47), (x + 142, y + 10), (x + 125, y + 40),
              (x + 170, y + 25)]
    draw.line(points, fill=(15, 40, 130), width=3)


def encoded(image, format="PNG"):
    output = BytesIO()
    image.save(output, format=format)
    return output.getvalue()


class VisualTests(unittest.TestCase):
    def test_signature_bounds_include_separate_strokes_without_blank_rule_or_label(self):
        image, words = page(signature=True)
        draw = ImageDraw.Draw(image)
        draw.rectangle((65, 850, 300, 880), fill="white")
        draw.text((70, 900), "Подпись студента", font=FONT, fill="black")
        words = [{**word, "bbox": [word["bbox"][0], word["bbox"][1] + 50,
                                   word["bbox"][2], word["bbox"][3] + 50]} for word in words]
        draw.line((60, 820, 700, 820), fill="gray", width=1)
        # Detached side stroke, ascender and descender with gaps from the main body.
        draw.line([(284, 814), (290, 782), (296, 819)], fill=(15, 40, 130), width=3)
        draw.line([(145, 844), (150, 856)], fill=(15, 40, 130), width=3)
        draw.line([(340, 775), (355, 780)], fill="black", width=3)  # Unrelated nearby note.
        result = analyze_document_visuals(image, words)
        x1, y1, x2, y2 = result["student_signature"]["bbox"]
        self.assertLessEqual(x1, 110)
        self.assertLessEqual(y1, 782)
        self.assertGreaterEqual(x2, 297)
        self.assertGreaterEqual(y2, 857)
        self.assertGreater(x1, 70)
        self.assertLess(x2, 340)
        self.assertLess(y2, 900)  # Printed label must remain outside the box.

    def test_signature_original_scale_covers_full_handwriting(self):
        image, words = page(signature=True)
        for factor in (1, 3):
            scaled_words = [{**word, "bbox": [v * factor for v in word["bbox"]]} for word in words]
            result = analyze_document_visuals(image.resize((800 * factor, 1100 * factor)), scaled_words)
            box = result["student_signature"]["bbox"]
            self.assertLessEqual(box[0], 110 * factor)
            self.assertLessEqual(box[1], 790 * factor)
            self.assertGreaterEqual(box[2], 280 * factor)
            self.assertGreaterEqual(box[3], 840 * factor)

    def test_blank_signature_field_is_not_a_signature(self):
        image, words = page(signature=True)
        draw = ImageDraw.Draw(image)
        draw.rectangle((100, 775, 300, 845), fill="white")
        draw.line((320, 870, 700, 870), fill="gray", width=1)
        self.assertFalse(analyze_document_visuals(image, words)["signature"]["detected"])

    def test_ocr_word_inside_a_crossing_handwriting_stroke_is_not_erased(self):
        image, words = page(signature=True)
        words.append({"text": "lll", "confidence": 80, "bbox": [160, 805, 240, 825], "line": [9, 1, 1]})
        result = analyze_document_visuals(image, words)
        box = result["student_signature"]["bbox"]
        self.assertLessEqual(box[0], 110)
        self.assertGreaterEqual(box[2], 280)

    def test_explicit_specimen_is_not_a_confirmed_student_signature(self):
        image, words = page(signature=True)
        words.insert(0, {"text": "Образец", "confidence": 95, "bbox": [5, 850, 65, 874], "line": [1, 1, 1]})
        result = analyze_document_visuals(image, words)
        self.assertNotEqual(result["student_signature"]["status"], "detected")

    def test_handwriting_cut_by_page_boundary_is_uncertain(self):
        image, words = page(signature=True)
        draw = ImageDraw.Draw(image)
        draw.rectangle((100, 775, 300, 845), fill="white")
        draw_signature(draw, -5, 790)
        result = analyze_document_visuals(image, words)
        self.assertEqual(result["signature"]["bbox"][0], 0)
        self.assertEqual(result["signature"]["status"], "uncertain")

    def test_stamp_and_student_signature(self):
        image, words = page(stamp=True, signature=True)
        result = analyze_document_visuals(image, words)
        self.assertTrue(result["stamp"]["detected"])
        self.assertTrue(result["student_signature"]["detected"])
        self.assertFalse(result["authenticity_verified"])

    def test_stamp_only(self):
        image, words = page(stamp=True)
        result = analyze_document_visuals(image, words)
        self.assertTrue(result["stamp"]["detected"])
        self.assertFalse(result["student_signature"]["detected"])

    def test_signature_only(self):
        image, words = page(signature=True)
        result = analyze_document_visuals(image, words)
        self.assertFalse(result["stamp"]["detected"])
        self.assertTrue(result["student_signature"]["detected"])

    def test_no_elements_printed_text_and_rules(self):
        image, words = page()
        draw = ImageDraw.Draw(image)
        draw.line((60, 800, 700, 800), fill="black", width=2)
        draw.text((70, 850), "Обычный печатный текст без подписи", font=FONT, fill="black")
        result = analyze_document_visuals(image, words)
        self.assertFalse(result["stamp"]["detected"])
        self.assertFalse(result["signature"]["detected"])

    def test_multiple_signatures(self):
        image, words = page(signature=True, multiple=True)
        result = analyze_document_visuals(image, words)
        self.assertGreaterEqual(len([d for d in result["detections"] if d["type"] == "signature"]), 2)

    def test_staff_signature_is_not_student(self):
        image, words = page(signature=True, staff=True)
        result = analyze_document_visuals(image, words)
        self.assertTrue(result["signature"]["detected"])
        self.assertFalse(result["student_signature"]["detected"])
        self.assertEqual(result["student_signature"]["status"], "unknown")

    def test_no_ocr_keeps_role_unknown(self):
        image, _ = page(signature=True, text=False)
        result = analyze_document_visuals(image)
        self.assertTrue(result["signature"]["detected"])
        self.assertFalse(result["student_signature"]["detected"])

    def test_monochrome(self):
        image, words = page(stamp=True, signature=True)
        result = analyze_document_visuals(image.convert("L"), words)
        self.assertTrue(result["stamp"]["detected"])
        self.assertTrue(result["signature"]["detected"])

    def test_noise(self):
        image, words = page(stamp=True, signature=True)
        values = np.asarray(image).astype(np.int16)
        noise = np.random.default_rng(42).normal(0, 5, values.shape)
        image = Image.fromarray(np.clip(values + noise, 0, 255).astype(np.uint8))
        result = analyze_document_visuals(image, words)
        self.assertTrue(result["stamp"]["detected"])
        self.assertTrue(result["student_signature"]["detected"])

    def test_rotated_document(self):
        image, _ = page(stamp=True, signature=True)
        result = analyze_document_visuals(image.rotate(7, fillcolor="white"))
        self.assertTrue(result["stamp"]["detected"])
        self.assertTrue(result["signature"]["detected"])
        self.assertFalse(result["student_signature"]["detected"])

    def test_scale_boxes_cover_known_stamp(self):
        image, words = page(stamp=True)
        large = image.resize((2400, 3300))
        result = analyze_document_visuals(large, words)
        x1, y1, x2, y2 = result["stamp"]["bbox"]
        self.assertLess(x1, 575 * 3)
        self.assertGreater(x2, 575 * 3)
        self.assertLess(y1, 805 * 3)
        self.assertGreater(y2, 805 * 3)
        self.assertEqual(result["pages"][0]["width"], 2400)

    def test_other_stamp_colours_and_ellipse(self):
        for colour in ((180, 15, 30), (30, 140, 40), (130, 20, 160)):
            image = Image.new("RGB", (800, 1100), "white")
            draw = ImageDraw.Draw(image)
            draw.ellipse((420, 730, 660, 890), outline=colour, width=4)
            draw.ellipse((436, 742, 644, 878), outline=colour, width=3)
            draw.text((460, 790), "ДЕКАНАТ", font=FONT, fill=colour)
            with self.subTest(colour=colour):
                self.assertTrue(analyze_document_visuals(image)["stamp"]["detected"])

    def test_ocr_typo_context_and_generic_label(self):
        words = [{"text": "Подпнсь", "confidence": 75, "bbox": [70, 850, 155, 874], "line": [1, 1, 1]},
                 {"text": "заявителя", "confidence": 80, "bbox": [160, 850, 260, 874], "line": [1, 1, 1]}]
        self.assertTrue(any(a["role"] == "student" for a in find_signature_anchors(words, 800, 1100)))
        words[1]["text"] = "Дата"
        self.assertTrue(all(a["role"] == "unknown" for a in find_signature_anchors(words, 800, 1100)))

    def test_debug_image_and_input_unchanged(self):
        image, words = page(stamp=True)
        before = image.tobytes()
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "проверка.png"
            analyze_document_visuals(image, words, debug_path=target)
            self.assertTrue(target.is_file())
            with Image.open(target) as debug:
                self.assertEqual(debug.size, image.size)
        self.assertEqual(before, image.tobytes())


class DocumentTests(unittest.TestCase):
    def test_multipage_pdf_pipeline(self):
        first, _ = page(stamp=True)
        second, _ = page(signature=True)
        buffer = BytesIO()
        first.save(buffer, format="PDF", save_all=True, append_images=[second], resolution=100)
        self.assertEqual(validate_document(buffer.getvalue()), "application/pdf")
        with patch("utils.ocr.pytesseract.image_to_string", return_value="Текст"), \
             patch("utils.ocr.pytesseract.image_to_osd", return_value={"rotate": 0}), \
             patch("utils.ocr._ocr_words", return_value=[]):
            result = analyze_document(buffer, "Заявление на академический отпуск")
        self.assertEqual(len(result["previews"]), 2)
        self.assertEqual([p["page"] for p in result["visuals"]["pages"]], [1, 2])
        self.assertTrue(any(d["type"] == "stamp" and d["page"] == 1 for d in result["visuals"]["detections"]))
        self.assertTrue(any(d["type"] == "signature" and d["page"] == 2 for d in result["visuals"]["detections"]))
        self.assertFalse(result["validation"]["blocking"])

    def test_osd_rotation_has_matching_preview_coordinates(self):
        image, _ = page(stamp=True)
        sideways = image.rotate(90, expand=True)
        with patch("utils.ocr.pytesseract.image_to_string", return_value="Текст"), \
             patch("utils.ocr.pytesseract.image_to_osd", return_value={"rotate": 90, "orientation_conf": 12}), \
             patch("utils.ocr._ocr_words", return_value=[]):
            result = analyze_document(BytesIO(encoded(sideways)))
        preview = Image.open(BytesIO(result["previews"][0]))
        metadata = result["visuals"]["pages"][0]
        self.assertEqual(preview.size, image.size)
        self.assertEqual((metadata["width"], metadata["height"]), preview.size)
        self.assertTrue(result["visuals"]["stamp"]["detected"])

    def test_cv_failure_does_not_lose_ocr(self):
        image, _ = page()
        with patch("utils.ocr.pytesseract.image_to_string", return_value="Текст"), \
             patch("utils.ocr.pytesseract.image_to_osd", return_value={}), \
             patch("utils.ocr._ocr_words", return_value=[]), \
             patch("utils.ocr.analyze_document_visuals", side_effect=RuntimeError("CV unavailable")):
            result = analyze_document(BytesIO(encoded(image)))
        self.assertEqual(result["recognized"], "Текст")
        self.assertEqual(result["visuals"]["stamp"]["status"], "unknown")
        self.assertFalse(result["validation"]["blocking"])

    def test_corrupt_unsupported_and_oversized_documents(self):
        for data in (b"not an image", b"%PDF-invalid", b"x" * (10 * 1024 * 1024 + 1), encoded(Image.new("RGB", (50, 50)), "GIF")):
            with self.subTest(size=len(data)), self.assertRaises(ValueError):
                validate_document(data)

    def test_pdf_page_limit(self):
        image = Image.new("RGB", (32, 32), "white")
        buffer = BytesIO()
        image.save(buffer, format="PDF", save_all=True, append_images=[image] * MAX_PAGES)
        with self.assertRaises(ValueError):
            validate_document(buffer.getvalue())

    def test_exif_and_transparency(self):
        image = Image.new("RGB", (100, 200), "white")
        exif = image.getexif()
        exif[274] = 6
        buffer = BytesIO()
        image.save(buffer, "JPEG", exif=exif)
        self.assertEqual(load_document_pages(buffer.getvalue())[0].size, (200, 100))
        transparent = encoded(Image.new("RGBA", (100, 100), (0, 0, 0, 0)))
        loaded = load_document_pages(transparent)[0]
        self.assertEqual(loaded.getpixel((0, 0)), (255, 255, 255))


if __name__ == "__main__":
    unittest.main()
