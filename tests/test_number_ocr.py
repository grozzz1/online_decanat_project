import unittest
from unittest.mock import patch

from PIL import Image

from utils.ocr import _refine_number_ocr


class NumberOCRTests(unittest.TestCase):
    def refine(self, original, cropped):
        text = f"Номер справки: {original}\nДругой текст: {original}"
        words = [{"text": original, "bbox": [20, 20, 100, 40]}]
        with Image.new("RGB", (150, 80), "white") as image:
            with patch("utils.ocr.pytesseract.image_to_string", return_value=cropped) as ocr:
                result = _refine_number_ocr(image, text, words)
                ocr.assert_called_once()
        return result

    def test_crop_confirms_confusable_letters_only_in_labelled_number(self):
        self.assertEqual(self.refine("OB-4826", "ОБ-4826"), "Номер справки: ОБ-4826\nДругой текст: OB-4826")

    def test_real_latin_number_is_preserved(self):
        self.assertIn("Номер справки: AB-4826", self.refine("AB-4826", "AB-4826"))

    def test_changed_digits_or_unrelated_letters_are_rejected(self):
        for cropped in ("ОБ-4828", "ЖБ-4826", "ОБ/4826"):
            with self.subTest(cropped=cropped):
                self.assertIn("Номер справки: OB-4826", self.refine("OB-4826", cropped))

    def test_crop_failure_preserves_original(self):
        with Image.new("RGB", (150, 80), "white") as image:
            with patch("utils.ocr.pytesseract.image_to_string", side_effect=RuntimeError("timeout")):
                self.assertEqual(
                    _refine_number_ocr(image, "№ AB-48", [{"text": "AB-48", "bbox": [20, 20, 100, 40]}]),
                    "№ AB-48",
                )
