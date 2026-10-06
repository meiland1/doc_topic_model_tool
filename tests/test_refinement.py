"""Refinement invariants without Qt, model weights, or a running Ollama service."""
from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from qualitative_themes.analysis.refinement import (
    apply_proposal, cancel_proposal, list_proposals, load_source,
    propose_refinement, review_proposal,
)
from qualitative_themes.analysis.topics import AnalysisCancelled, TopicOutput, analyze, list_analysis_runs
from qualitative_themes.analysis.settings import AnalysisSettings
from qualitative_themes.storage.coding import (
    add_coding, add_note, create_theme, effective_assignments, list_notes, list_themes,
    merge_themes, occurrences, reject_model_suggestion, remove_coding, rename_theme,
)
from qualitative_themes.storage.database import connect, create_project, open_project
from qualitative_themes.storage.documents import import_document


class RefinementTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.directory = create_project(self.root / "project", "Refinement").directory
        path = self.root / "interview.txt"
        path.write_text("\n\n".join(f"Café 😀 passage {i} contains observations on work, trust, and access. " + "Research evidence explains contextual observations. " * 35 for i in range(12)), encoding="utf-8")
        self.doc = import_document(self.directory, path).document_id
        self.settings = AnalysisSettings(min_topic_size=2)
        self.run, _ = analyze(self.directory, "Paragraphs", [self.doc], self.settings, backend=self.discovery)

    @staticmethod
    def discovery(passages, *args):
        return TopicOutput([i % 3 - 1 for i in range(len(passages))],
                           {-1: [], 0: [("access", .8)], 1: [("trust", .9)]})

    @staticmethod
    def refreshed(passages, assignments, settings, operation, progress, cancel):
        if operation == "reduce":
            values = [-1 if t == -1 else 0 for t in assignments]
        else:
            values = assignments[:]
        return TopicOutput(values, {t: [("refreshed", .9)] for t in set(values)})

    def proposal(self, operation="words", **kwargs):
        return propose_refinement(self.directory, self.run, operation,
                                  representation_backend=self.refreshed, **kwargs)[0]

    def mapping(self, run):
        with connect(self.directory) as conn:
            return json.loads(conn.execute("SELECT model_info_json FROM analysis_runs WHERE id=?", (run,)).fetchone()[0])["topic_theme_map"]

    def recommended(self, proposal):
        return {item["theme_id"]: item["recommended"] for item in review_proposal(self.directory, proposal)["themes"]}

    def status(self, run):
        with connect(self.directory) as conn:
            return conn.execute("SELECT status FROM analysis_runs WHERE id=?", (run,)).fetchone()[0]

    def manual_snapshot(self):
        with connect(self.directory) as conn:
            return {table: conn.execute(f"SELECT * FROM {table} ORDER BY id").fetchall()
                    for table in ("codings", "notes")}

    def test_preview_and_cancel_leave_original_and_manual_list_unchanged(self):
        before = list_themes(self.directory, self.run)
        proposal = self.proposal()
        self.assertEqual(self.status(proposal), "proposed")
        self.assertEqual([r[0] for r in list_analysis_runs(self.directory)], [self.run])
        self.assertEqual(list_themes(self.directory), [])
        self.assertEqual(list_themes(self.directory, self.run), before)
        cancel_proposal(self.directory, proposal)
        self.assertEqual(self.status(proposal), "cancelled")
        self.assertEqual(list_themes(self.directory, self.run), before)
        self.assertEqual(list_proposals(self.directory), [])

    def test_refresh_keeps_partition_and_reuses_reviewed_stable_ids(self):
        original = self.mapping(self.run)
        original_labels = list_themes(self.directory, self.run)
        proposal = self.proposal()
        review = review_proposal(self.directory, proposal)
        self.assertEqual(review["changed_memberships"], 0)
        self.assertFalse(any(item["ambiguous"] for item in review["themes"]))
        apply_proposal(self.directory, proposal, self.recommended(proposal))
        self.assertEqual(set(self.mapping(proposal).values()), set(original.values()))
        self.assertEqual(list_themes(self.directory, proposal), original_labels)
        self.assertEqual(self.status(proposal), "completed")
        with connect(self.directory) as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM history_events WHERE event_type='refinement_applied'").fetchone()[0], 1)
            info = json.loads(conn.execute("SELECT model_info_json FROM analysis_runs WHERE id=?", (proposal,)).fetchone()[0])
            self.assertTrue(all(words[0][0] == "refreshed" for words in info["keywords"].values()))

    def test_rerun_preserves_labels_notes_additions_and_rejections(self):
        theme = self.mapping(self.run)["0"]
        rename_theme(self.directory, theme, "Researcher access")
        hits = occurrences(self.directory, theme, self.run)
        first, second = hits[:2]
        coding = add_coding(self.directory, first[1], first[3], first[4], theme)
        add_note(self.directory, "Passage memo", coding_id=coding)
        add_note(self.directory, "Theme memo", theme_id=theme)
        add_note(self.directory, "Document memo", document_id=self.doc)
        reject_model_suggestion(self.directory, second[1], second[3], second[4], theme)
        seed = create_theme(self.directory, "Manual seed")
        add_coding(self.directory, self.doc, 0, 5, seed)
        before = self.manual_snapshot()
        proposal = self.proposal("discover", discovery_backend=self.discovery)
        apply_proposal(self.directory, proposal, self.recommended(proposal))
        self.assertEqual(self.manual_snapshot(), before)
        self.assertEqual(len(occurrences(self.directory, theme, proposal)), len(hits) - 1)
        self.assertIn("Researcher access", [label for _, label, _ in list_themes(self.directory, proposal)])
        self.assertIn(seed, {tid for tid, _, _ in list_themes(self.directory, proposal)})
        open_project(self.directory)
        self.assertEqual(list_notes(self.directory, theme_id=theme)[0][1], "Theme memo")

    def test_topic_numbers_never_define_identity(self):
        original = self.mapping(self.run)
        def swapped(passages, *args):
            output = self.discovery(passages)
            output.assignments = [-1 if t == -1 else 1 - t for t in output.assignments]
            return output
        proposal = self.proposal("discover", discovery_backend=swapped)
        apply_proposal(self.directory, proposal, self.recommended(proposal))
        self.assertEqual(self.mapping(proposal)["0"], original["1"])
        self.assertEqual(self.mapping(proposal)["1"], original["0"])

    def test_reduction_requires_review_of_combined_groups(self):
        theme = self.mapping(self.run)["0"]
        rename_theme(self.directory, theme, "Carry access")
        proposal = self.proposal("reduce", settings=replace(self.settings, requested_theme_count=1))
        review = review_proposal(self.directory, proposal)
        self.assertEqual((review["old_theme_count"], review["new_theme_count"]), (2, 1))
        regular = next(item for item in review["themes"] if not item["outlier"])
        self.assertTrue(regular["ambiguous"])
        self.assertIsNone(regular["recommended"])
        self.assertEqual(len(regular["candidates"]), 2)
        choices = self.recommended(proposal)
        choices[regular["theme_id"]] = theme
        apply_proposal(self.directory, proposal, choices)
        self.assertEqual(self.mapping(proposal)["0"], theme)
        self.assertIn("Carry access", [label for _, label, _ in list_themes(self.directory, proposal)])
        self.assertEqual(review["old_uncategorized"], review["new_uncategorized"])

    def test_split_cannot_reuse_identity_twice_and_apply_rolls_back(self):
        def split(passages, *args):
            return TopicOutput([i for i in range(len(passages))], {})
        proposal = self.proposal("discover", discovery_backend=split)
        review = review_proposal(self.directory, proposal)
        candidates = [item for item in review["themes"] if item["candidates"]]
        by_old = {}
        for item in candidates:
            old = item["candidates"][0]["theme_id"]
            by_old.setdefault(old, []).append(item["theme_id"])
        old, themes = next((old, themes) for old, themes in by_old.items() if len(themes) > 1)
        choices = {item["theme_id"]: None for item in review["themes"]}
        choices[themes[0]] = choices[themes[1]] = old
        before = self.mapping(proposal)
        with self.assertRaisesRegex(ValueError, "only once"):
            apply_proposal(self.directory, proposal, choices)
        self.assertEqual(self.mapping(proposal), before)
        self.assertEqual(self.status(proposal), "proposed")

    def test_changed_segmentation_never_guesses_identity(self):
        proposal = self.proposal("discover", unit="Short passages", discovery_backend=self.discovery)
        review = review_proposal(self.directory, proposal)
        self.assertGreater(review["new_passages"], 0)
        self.assertTrue(all(item["recommended"] is None for item in review["themes"]))

    def test_cancel_before_save_leaves_no_proposal(self):
        with self.assertRaises(AnalysisCancelled):
            self.proposal(should_cancel=lambda: True)
        self.assertEqual(list_proposals(self.directory), [])

    def test_cancel_after_saved_proposal_discards_without_touching_source(self):
        with patch("qualitative_themes.analysis.refinement.review_proposal", side_effect=AnalysisCancelled):
            with self.assertRaises(AnalysisCancelled):
                self.proposal()
        self.assertEqual(list_proposals(self.directory), [])
        self.assertEqual(self.status(self.run), "completed")

    def test_stale_decisions_prevent_apply(self):
        proposal = self.proposal()
        choices = self.recommended(proposal)
        rename_theme(self.directory, self.mapping(self.run)["0"], "New decision after preview")
        self.assertTrue(review_proposal(self.directory, proposal)["stale"])
        with self.assertRaisesRegex(ValueError, "changed since preview"):
            apply_proposal(self.directory, proposal, choices)
        self.assertEqual(self.status(proposal), "proposed")

    def test_human_merge_partition_and_history_survive(self):
        mapping = self.mapping(self.run)
        merge_themes(self.directory, mapping["0"], mapping["1"])
        source = load_source(self.directory, self.run)
        self.assertEqual(len(set(source["output"].assignments) - {-1}), 1)
        with connect(self.directory) as conn:
            history = conn.execute("SELECT * FROM history_events WHERE event_type='themes_merged'").fetchall()
        proposal = self.proposal()
        apply_proposal(self.directory, proposal, self.recommended(proposal))
        self.assertEqual(set(self.mapping(proposal).values()), {mapping["1"], mapping["-1"]})
        with connect(self.directory) as conn:
            self.assertEqual(conn.execute("SELECT * FROM history_events WHERE event_type='themes_merged'").fetchall(), history)

    def test_names_only_never_calls_discovery_and_cancel_restores_machine_names(self):
        source_labels = list_themes(self.directory, self.run)
        theme = self.mapping(self.run)["0"]
        rename_theme(self.directory, theme, "Protected name")
        def unavailable():
            raise ValueError("Ollama offline")
        with patch("qualitative_themes.analysis.refinement.fit_bertopic", side_effect=AssertionError("No discovery")):
            proposal = self.proposal("names", client_factory=unavailable)
        self.assertIn("Protected name", [i["label"] for i in review_proposal(self.directory, proposal)["themes"]])
        cancel_proposal(self.directory, proposal)
        self.assertEqual(self.status(self.run), "completed")
        self.assertEqual(len(list_themes(self.directory, self.run)), len(source_labels))
        self.assertIn("Protected name", [label for _, label, _ in list_themes(self.directory, self.run)])

    def test_words_backend_cannot_recluster(self):
        def bad(passages, assignments, *args):
            return TopicOutput([0] * len(passages), {})
        with self.assertRaisesRegex(ValueError, "changed memberships"):
            propose_refinement(self.directory, self.run, "words", representation_backend=bad)
        self.assertEqual(list_proposals(self.directory), [])

    def test_invalid_reduction_and_clustering_changes_rejected(self):
        with self.assertRaisesRegex(ValueError, "fewer"):
            self.proposal("reduce", settings=replace(self.settings, requested_theme_count=2))
        with self.assertRaisesRegex(ValueError, "fresh discovery"):
            self.proposal(settings=replace(self.settings, min_samples=4))
        with self.assertRaisesRegex(ValueError, "fresh discovery"):
            self.proposal(unit="Short passages")

    def test_failed_apply_rolls_back_earlier_theme_updates(self):
        unrelated = create_theme(self.directory, "Unrelated manual theme")
        proposal = self.proposal()
        choices = self.recommended(proposal)
        ids = list(choices)
        choices[ids[-1]] = unrelated
        with connect(self.directory) as conn:
            before = conn.execute("SELECT * FROM themes ORDER BY id").fetchall()
            suggestions = conn.execute("SELECT * FROM model_suggestions WHERE run_id=? ORDER BY id", (proposal,)).fetchall()
        with self.assertRaisesRegex(ValueError, "actual shared"):
            apply_proposal(self.directory, proposal, choices)
        with connect(self.directory) as conn:
            self.assertEqual(conn.execute("SELECT * FROM themes ORDER BY id").fetchall(), before)
            self.assertEqual(conn.execute("SELECT * FROM model_suggestions WHERE run_id=? ORDER BY id", (proposal,)).fetchall(), suggestions)
        self.assertEqual(self.status(proposal), "proposed")

    def test_removed_coding_stays_removed_after_reviewed_rerun(self):
        theme = self.mapping(self.run)["1"]
        hit = occurrences(self.directory, theme, self.run)[0]
        coding = add_coding(self.directory, hit[1], hit[3], hit[4], theme)
        remove_coding(self.directory, coding)
        proposal = self.proposal("discover", discovery_backend=self.discovery)
        apply_proposal(self.directory, proposal, self.recommended(proposal))
        self.assertNotIn((hit[1], hit[3], hit[4]),
                         {(doc, start, end) for _, doc, start, end, _ in effective_assignments(self.directory, theme, proposal)})

    def test_successful_name_preview_does_not_publish_until_apply(self):
        class Reply:
            def __init__(self, payload): self.payload = payload
            def raise_for_status(self): pass
            def json(self): return self.payload
        class Client:
            def get(self, *args, **kwargs): return Reply({"models": [{"name": "local:test"}]})
            def post(self, *args, **kwargs): return Reply({"done": True, "response": json.dumps({"label": "New local name"})})
            def close(self): pass
        before = list_themes(self.directory, self.run)
        proposal = self.proposal("names", settings=replace(self.settings, ollama_model="local:test"), client_factory=Client)
        self.assertEqual(list_themes(self.directory, self.run), before)
        self.assertIn("New local name", [item["label"] for item in review_proposal(self.directory, proposal)["themes"]])
        apply_proposal(self.directory, proposal, self.recommended(proposal))
        self.assertIn("New local name", [label for _, label, _ in list_themes(self.directory, proposal)])

    def test_review_evidence_uses_exact_source_quote(self):
        from qualitative_themes.analysis.refinement import proposal_evidence
        proposal = self.proposal()
        item = review_proposal(self.directory, proposal)["themes"][0]
        with connect(self.directory) as conn:
            text = conn.execute("SELECT canonical_text FROM documents WHERE id=?", (self.doc,)).fetchone()[0]
        for name, start, end, quote, previous in proposal_evidence(self.directory, proposal, item["theme_id"]):
            self.assertEqual(quote, text[start:end])
            self.assertIn("😀", quote)
            self.assertNotEqual(previous, "New passage range")

    def test_refresh_respects_uncategorized_merge_into_regular_theme(self):
        mapping = self.mapping(self.run)
        merge_themes(self.directory, mapping["-1"], mapping["0"])
        proposal = self.proposal()
        review = review_proposal(self.directory, proposal)
        self.assertEqual((review["old_uncategorized"], review["new_uncategorized"]), (0, 0))
        self.assertEqual(review["changed_memberships"], 0)
        apply_proposal(self.directory, proposal, self.recommended(proposal))
        self.assertNotIn(mapping["-1"], set(self.mapping(proposal).values()))
