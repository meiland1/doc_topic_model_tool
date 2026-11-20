"""Passage preview uses exactly the selected documents, without Qt or model weights."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from qualitative_themes.analysis.topics import prepare_passages
from qualitative_themes.storage.database import create_project
from qualitative_themes.storage.documents import import_document


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
                passages = prepare_passages(self.directory, "Paragraphs", [doc])
                self.assertEqual(len(passages), 2)
                self.assertEqual({p.document_id for p in passages}, {doc})

    def test_multi_selection_excludes_unselected_documents(self):
        passages = prepare_passages(self.directory, "Paragraphs", self.docs[1:])
        self.assertEqual(len(passages), 4)
        self.assertEqual({p.document_id for p in passages}, set(self.docs[1:]))
