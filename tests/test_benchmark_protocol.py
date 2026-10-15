import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.benchmark_common import validate_document_ids
from scripts.benchmark_protocol import fingerprint


class ProtocolTests(unittest.TestCase):
    def test_document_ids_cannot_escape_output_directory_or_overwrite_each_other(self):
        for documents in (
            [{"document_id": "../outside"}],
            [{"document_id": "..\\outside"}],
            [{"document_id": ""}],
            [{"document_id": "same"}, {"document_id": "same"}],
        ):
            with self.subTest(documents=documents), self.assertRaises(ValueError):
                validate_document_ids(documents)
        self.assertEqual(validate_document_ids([{"document_id": "open-001"}]), ["open-001"])

    def test_resume_key_changes_with_questions_rules_and_model_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            rules, questions = root / "rules.md", root / "questions.json"
            rules.write_bytes(b"rule 1.3\n")
            questions.write_text('["first question"]', encoding="utf-8")
            with patch("scripts.benchmark_protocol.SOURCE_FILES", ("rules.md",)):
                initial = fingerprint([questions], {"model": "first"}, root=root)
                questions.write_text('["changed question"]', encoding="utf-8")
                self.assertNotEqual(initial, fingerprint([questions], {"model": "first"}, root=root))
                changed_questions = fingerprint([questions], {"model": "first"}, root=root)
                rules.write_bytes(b"rule 1.3 updated\n")
                self.assertNotEqual(
                    changed_questions, fingerprint([questions], {"model": "first"}, root=root)
                )
                self.assertNotEqual(
                    fingerprint([questions], {"model": "first"}, root=root),
                    fingerprint([questions], {"model": "second"}, root=root),
                )

    def test_source_line_endings_do_not_invalidate_unchanged_code(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "source.py"
            with patch("scripts.benchmark_protocol.SOURCE_FILES", ("source.py",)):
                path.write_bytes(b"value = 1\r\n")
                initial = fingerprint([], {}, root=root)
                path.write_bytes(b"value = 1\n")
                self.assertEqual(initial, fingerprint([], {}, root=root))
