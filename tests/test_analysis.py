"""No model weights, Ollama, or Qt needed for these repository checks."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from dataclasses import replace

from qualitative_themes.analysis.topics import (
    AnalysisCancelled, AnalysisSettings, TopicOutput, analyze, list_analysis_runs,
    prepare_passages, save_result,
)
from qualitative_themes.storage.comments import Target, add_comment, list_comments
from qualitative_themes.storage.coding import (
    add_coding, create_theme, effective_assignments, list_themes,
    merge_themes, occurrences, reject_model_suggestion, remove_coding, rename_theme,
)
from qualitative_themes.storage.database import connect, create_project, open_project
from qualitative_themes.storage.documents import import_document


class AnalysisRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.directory = create_project(self.root/"project", "Research").directory
        self.docs = []
        for doc in range(2):
            source = self.root/f"{doc}.txt"
            source.write_text("\n\n".join(f"Document {doc}. Café 😀 passage {i} describes a meaningful research observation." for i in range(6)), encoding="utf-8")
            self.docs.append(import_document(self.directory, source).document_id)

    @staticmethod
    def fake(passages, settings, directory, progress, cancel):
        assignments = [i % 3 - 1 for i in range(len(passages))]
        reps = {topic: [next(p.id for p, t in zip(passages, assignments) if t == topic)]
                for topic in set(assignments)}
        return TopicOutput(assignments, {-1: [], 0: [("access", .8)], 1: [("trust", .7)]},
                           reps, [.5]*len(passages), {"packages": {"fake": "1"}})

    def run_fake(self):
        return analyze(self.directory, "Paragraphs", self.docs, backend=self.fake)[0]

    def mapping(self, run):
        with connect(self.directory) as conn:
            info = json.loads(conn.execute("SELECT model_info_json FROM analysis_runs WHERE id=?", (run,)).fetchone()[0])
        return info["topic_theme_map"]

    def counts(self):
        with connect(self.directory) as conn:
            return [conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                    for table in ["analysis_runs", "segments", "themes", "model_suggestions", "history_events"]]

    def test_manual_only_hides_suggestions_but_keeps_accepted_work(self):
        run = self.run_fake()
        generated = set(self.mapping(run).values())
        empty_manual = create_theme(self.directory, "Empty manual theme")
        self.assertEqual({row[0] for row in list_themes(self.directory)}, {empty_manual})
        theme = self.mapping(run)["0"]
        rename_theme(self.directory, theme, "Human named model theme")
        self.assertNotIn(theme, {row[0] for row in list_themes(self.directory)})
        suggestion = occurrences(self.directory, theme, run)[0]
        coding = add_coding(self.directory, suggestion[1], suggestion[3], suggestion[4], theme)
        self.assertEqual({tid: count for tid, _, count in list_themes(self.directory)},
                         {empty_manual: 0, theme: 1})
        self.assertEqual(len(occurrences(self.directory, theme)), 1)
        remove_coding(self.directory, coding)
        self.assertEqual({row[0] for row in list_themes(self.directory)}, {empty_manual})
        self.assertEqual({row[0] for row in list_themes(self.directory, run)}, generated)

    def test_manual_themes_and_counts_follow_selected_corpus(self):
        theme = create_theme(self.directory, "Manual shared")
        other = create_theme(self.directory, "Only first document")
        add_coding(self.directory, self.docs[0], 0, 10, theme)
        add_coding(self.directory, self.docs[1], 0, 10, theme)
        add_coding(self.directory, self.docs[0], 11, 20, other)
        note = add_comment(self.directory, Target.theme(other), "Preserve this comment")
        run, _ = analyze(self.directory, "Paragraphs", [self.docs[1]], settings=AnalysisSettings(min_topic_size=2), backend=self.fake)
        scoped = {tid: count for tid, _, count in list_themes(self.directory, run)}
        self.assertNotIn(other, scoped)
        self.assertEqual(scoped[theme], 1)
        self.assertEqual({row[1] for row in occurrences(self.directory, theme, run)}, {self.docs[1]})
        self.assertEqual(len(effective_assignments(self.directory, theme)), 2)
        self.assertIn(other, {row[0] for row in list_themes(self.directory)})
        self.assertEqual(list_comments(self.directory, theme_id=other)[0][0].id, note)
        first, _ = analyze(self.directory, "Paragraphs", [self.docs[0]], settings=AnalysisSettings(min_topic_size=2), backend=self.fake)
        self.assertIn(other, {row[0] for row in list_themes(self.directory, first)})

    def test_corpus_mapping_exact_offsets_outliers_and_reopen(self):
        run = self.run_fake()
        mapping = self.mapping(run)
        self.assertEqual(len(set(mapping.values())), 3)
        themes = list_themes(self.directory, run)
        self.assertIn("Not in any theme", [label for _, label, _ in themes])
        self.assertEqual(sum(count for _, _, count in themes), 12)
        hits = occurrences(self.directory, mapping["0"], run)
        self.assertEqual({h[1] for h in hits}, set(self.docs))
        with connect(self.directory) as conn:
            for _, doc, _, start, end, excerpt in hits:
                text = conn.execute("SELECT canonical_text FROM documents WHERE id=?", (doc,)).fetchone()[0]
                self.assertEqual(excerpt, text[start:end])
                self.assertIn("Café 😀", excerpt)
            stored = json.loads(conn.execute("SELECT settings_json FROM analysis_runs WHERE id=?", (run,)).fetchone()[0])
        self.assertEqual(stored["document_ids"], self.docs)
        self.assertEqual(stored["seed"], 42)
        self.assertEqual(list_analysis_runs(open_project(self.directory).directory)[0][0], run)

    def test_backend_failure_and_invalid_output_leave_previous_run_intact(self):
        self.run_fake()
        before = self.counts()
        def failed(*args):
            raise RuntimeError("Simulated clustering failure")
        with self.assertRaises(RuntimeError):
            analyze(self.directory, "Paragraphs", self.docs, backend=failed)
        with self.assertRaises(ValueError):
            analyze(self.directory, "Paragraphs", self.docs,
                    backend=lambda *args: TopicOutput([0], {0: []}))
        self.assertEqual(before, self.counts())

    def test_late_source_validation_rolls_back_all_rows(self):
        self.run_fake()
        before = self.counts()
        passages = prepare_passages(self.directory, "Paragraphs", self.docs)
        output = self.fake(passages, None, None, None, None)
        # Force failure after the run and several segment rows were inserted.
        passages[-1] = replace(passages[-1], source_hash="invalid")
        with self.assertRaises(ValueError):
            save_result(self.directory, "Paragraphs", self.docs, passages, AnalysisSettings(), output)
        self.assertEqual(before, self.counts())

    def test_cancellation_after_inserts_rolls_back(self):
        before = self.counts()
        passages = prepare_passages(self.directory, "Paragraphs", self.docs)
        output = self.fake(passages, None, None, None, None)
        calls = []
        def cancel():
            calls.append(True)
            return len(calls) == 2
        with self.assertRaises(AnalysisCancelled):
            save_result(self.directory, "Paragraphs", self.docs, passages, AnalysisSettings(), output, cancel)
        self.assertEqual(before, self.counts())

    def test_rerun_keeps_human_work_and_does_not_reuse_numeric_topic_ids(self):
        old = self.run_fake()
        theme = self.mapping(old)["0"]
        _, doc, _, start, end, _ = occurrences(self.directory, theme, old)[0]
        add_coding(self.directory, doc, start, end, theme)
        note = add_comment(self.directory, Target.passage(doc, start, end, theme), "Researcher interpretation")
        rename_theme(self.directory, theme, "My interpretation")
        new = self.run_fake()
        self.assertNotEqual(self.mapping(new)["0"], theme)
        self.assertEqual([(c.id, c.body) for c, _ in list_comments(self.directory, theme_id=theme)], [(note, "Researcher interpretation")])
        self.assertIn((theme, "My interpretation", 1), list_themes(self.directory, new))
        self.assertEqual(len(occurrences(self.directory, theme, new)), 1)

    def test_rejection_manual_precedence_removal_and_merge(self):
        run = self.run_fake()
        theme, target = self.mapping(run)["0"], self.mapping(run)["1"]
        _, doc, _, start, end, _ = occurrences(self.directory, theme, run)[0]
        reject_model_suggestion(self.directory, doc, start, end, theme)
        self.assertEqual(len(effective_assignments(self.directory, theme, run)), 3)
        coding = add_coding(self.directory, doc, start, end, theme)
        assignments = effective_assignments(self.directory, theme, run)
        self.assertEqual(len(assignments), 4)
        self.assertIn((coding, doc, start, end, "researcher"), assignments)
        remove_coding(self.directory, coding)
        self.assertEqual(len(effective_assignments(self.directory, theme, run)), 3)
        merge_themes(self.directory, theme, target)
        self.assertEqual(len(effective_assignments(self.directory, target, run)), 7)
        self.assertNotIn(theme, [t[0] for t in list_themes(self.directory, run)])

    def test_small_corpus_help_and_selection_validation(self):
        # One whole document is a single passage: small-collection grouping needs two.
        with self.assertRaisesRegex(ValueError, "need at least 2"):
            analyze(self.directory, "Whole document", self.docs[:1], backend=self.fake)
        with self.assertRaises(ValueError):
            analyze(self.directory, "Paragraphs", [self.docs[0], "missing"], backend=self.fake)
        self.assertEqual(self.counts(), [0]*5)


if __name__ == "__main__":
    unittest.main()
