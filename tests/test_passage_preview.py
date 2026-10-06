"""Preview corpus selection and repeat previews, without Qt or model weights."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from qualitative_themes.storage.database import connect, create_project
from qualitative_themes.storage.documents import import_document
from qualitative_themes.storage.segmentation import create_segmentation_run, list_segments


class PassagePreviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.directory = create_project(self.root / "project", "Previews").directory
        self.docs = []
        for number in range(3):
            path = self.root / f"document{number}.txt"
            path.write_text(f"Document {number} first Café 😀 passage.\n\nDocument {number} second passage.", encoding="utf-8")
            self.docs.append(import_document(self.directory, path).document_id)

    def test_repeated_selection_never_defaults_to_first_import(self):
        for doc in [self.docs[2], self.docs[1], self.docs[0], self.docs[2]]:
            with self.subTest(document=doc):
                run, count = create_segmentation_run(self.directory, "Paragraphs", [doc])
                self.assertEqual(count, 2)
                self.assertEqual({row[0] for row in list_segments(self.directory, run)}, {doc})
                with connect(self.directory) as conn:
                    settings = json.loads(conn.execute("SELECT settings_json FROM analysis_runs WHERE id=?", (run,)).fetchone()[0])
                self.assertEqual(settings["document_ids"], [doc])

    def test_multi_selection_excludes_unselected_documents(self):
        run, count = create_segmentation_run(self.directory, "Paragraphs", self.docs[1:])
        self.assertEqual(count, 4)
        self.assertEqual({row[0] for row in list_segments(self.directory, run)}, set(self.docs[1:]))
