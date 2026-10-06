"""Reversible document exclusion preserves saved research and source evidence."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from qualitative_themes.analysis.topics import AnalysisSettings, TopicOutput, analyze, prepare_passages
from qualitative_themes.analysis.refinement import propose_refinement, review_proposal, cancel_proposal
from qualitative_themes.storage.coding import add_coding, add_note, create_theme, list_notes, occurrences
from qualitative_themes.storage.database import connect, create_project, open_project
from qualitative_themes.storage.documents import (
    get_document, import_document, list_documents, remove_documents, restore_documents,
)
from qualitative_themes.storage.segmentation import create_segmentation_run, list_segments


class DocumentRemovalTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.directory = create_project(self.root / "project", "Documents").directory
        self.docs, self.paths = [], []
        for number in range(2):
            path = self.root / f"source{number}.txt"
            path.write_text("\n\n".join(f"Document {number} Café 😀 observation {i} about research." for i in range(6)), encoding="utf-8")
            self.paths.append(path)
            self.docs.append(import_document(self.directory, path).document_id)
        self.settings = AnalysisSettings(min_topic_size=2)
        def fake(passages, *args):
            return TopicOutput([i % 2 for i in range(len(passages))], {0: [("research", .8)], 1: [("evidence", .7)]})
        self.run, _ = analyze(self.directory, "Paragraphs", self.docs, self.settings, backend=fake)
        self.theme = create_theme(self.directory, "Manual evidence")
        coding = add_coding(self.directory, self.docs[0], 0, 10, self.theme)
        add_note(self.directory, "Keep this passage memo", coding_id=coding)
        add_note(self.directory, "Keep this document memo", document_id=self.docs[0])

    def saved_research(self):
        with connect(self.directory) as conn:
            return {table: conn.execute(f"SELECT * FROM {table} ORDER BY id").fetchall()
                    for table in ["document_pages", "analysis_runs", "segments", "model_suggestions", "themes", "codings", "notes"]}

    def test_remove_hides_document_but_preserves_all_evidence(self):
        before = self.saved_research()
        source_files = {p: p.read_bytes() for p in (self.directory / "sources").iterdir()}
        text = get_document(self.directory, self.docs[0])
        self.assertEqual(remove_documents(self.directory, [self.docs[0]]), 1)
        self.assertEqual([row[0] for row in list_documents(self.directory)], [self.docs[1]])
        self.assertEqual(list_documents(self.directory, include_removed=True)[0][2], "removed")
        self.assertEqual(self.saved_research(), before)
        self.assertEqual(get_document(self.directory, self.docs[0]), text)
        self.assertEqual({p: p.read_bytes() for p in source_files}, source_files)
        self.assertEqual(occurrences(self.directory, self.theme)[0][1], self.docs[0])
        self.assertEqual(list_notes(self.directory, document_id=self.docs[0])[0][1], "Keep this document memo")
        open_project(self.directory)
        self.assertEqual(len(list_documents(self.directory)), 1)

    def test_future_preview_all_excludes_removed_and_selected_analysis_rejects_it(self):
        remove_documents(self.directory, [self.docs[0]])
        run, count = create_segmentation_run(self.directory, "Paragraphs")
        self.assertEqual(count, 6)
        self.assertEqual({row[0] for row in list_segments(self.directory, run)}, {self.docs[1]})
        with self.assertRaises(ValueError):
            prepare_passages(self.directory, "Paragraphs", [self.docs[0]])
        self.assertEqual(len(list_segments(self.directory, self.run)), 12)

    def test_restore_returns_same_document_and_decisions(self):
        before = self.saved_research()
        remove_documents(self.directory, self.docs)
        self.assertEqual(list_documents(self.directory), [])
        self.assertEqual(restore_documents(self.directory, self.docs), 2)
        self.assertEqual([row[0] for row in list_documents(self.directory)], self.docs)
        self.assertEqual(self.saved_research(), before)

    def test_reimport_removed_source_restores_existing_identity(self):
        remove_documents(self.directory, [self.docs[0]])
        outcome = import_document(self.directory, self.paths[0])
        self.assertEqual(outcome.status, "restored")
        self.assertEqual(outcome.document_id, self.docs[0])
        self.assertEqual(import_document(self.directory, self.paths[0]).status, "duplicate")
        self.assertEqual(len(list_documents(self.directory)), 2)
        self.assertEqual(occurrences(self.directory, self.theme)[0][1], self.docs[0])

    def test_invalid_batches_are_atomic_and_logged_only_on_success(self):
        with self.assertRaises(ValueError):
            remove_documents(self.directory, [self.docs[0], "missing"])
        self.assertEqual(len(list_documents(self.directory)), 2)
        with self.assertRaises(ValueError):
            remove_documents(self.directory, [self.docs[0], self.docs[0]])
        with self.assertRaises(ValueError):
            remove_documents(self.directory, [])
        remove_documents(self.directory, [self.docs[0]])
        with self.assertRaises(ValueError):
            restore_documents(self.directory, self.docs)
        self.assertEqual([row[0] for row in list_documents(self.directory)], [self.docs[1]])
        with connect(self.directory) as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM history_events WHERE event_type='documents_removed'").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT count(*) FROM history_events WHERE event_type='documents_restored'").fetchone()[0], 0)

    def test_removal_invalidates_pending_review(self):
        def offline(): raise ValueError("No service for this test")
        proposal, _ = propose_refinement(self.directory, self.run, "names", client_factory=offline)
        remove_documents(self.directory, [self.docs[0]])
        self.assertTrue(review_proposal(self.directory, proposal)["stale"])
        cancel_proposal(self.directory, proposal)

    def test_saved_partition_refresh_remains_possible_after_removal(self):
        remove_documents(self.directory, [self.docs[0]])
        def fixed(passages, assignments, *args):
            return TopicOutput(assignments[:], {0: [("refreshed", .8)], 1: [("words", .7)]})
        proposal, _ = propose_refinement(self.directory, self.run, "words", representation_backend=fixed)
        review = review_proposal(self.directory, proposal)
        self.assertEqual(review["shared_passages"], 12)
        self.assertEqual(review["changed_memberships"], 0)
        cancel_proposal(self.directory, proposal)
