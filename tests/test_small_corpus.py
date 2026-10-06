"""A five-passage corpus is supported without manufacturing extra passages."""
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from qualitative_themes.analysis.settings import (
    AnalysisSettings, effective_parameters, small_corpus_warning, validate_settings,
)
from qualitative_themes.analysis.topics import TopicOutput, analyze
from qualitative_themes.storage.database import create_project
from qualitative_themes.storage.documents import import_document
from qualitative_themes.storage.coding import list_themes


class SmallCorpusTests(unittest.TestCase):
    def test_five_passages_with_balanced_minimum_two_are_valid(self):
        settings = AnalysisSettings(min_topic_size=2, name_themes=True, ollama_model="llama3.1")
        validate_settings(settings, 5)
        params = effective_parameters(settings, 5)
        self.assertEqual(params["umap"]["n_neighbors"], 4)
        self.assertEqual(params["umap"]["n_components"], 3)
        self.assertEqual(params["umap"]["init"], "random")
        self.assertEqual(params["hdbscan"]["min_cluster_size"], 2)
        self.assertIn("can run", small_corpus_warning(settings, 5))

    def test_technical_floor_and_evidence_limit_are_clear(self):
        settings = AnalysisSettings(min_topic_size=2)
        validate_settings(settings, 3)
        with self.assertRaisesRegex(ValueError, "at least 3"):
            validate_settings(settings, 2)
        with self.assertRaisesRegex(ValueError, "minimum evidence"):
            validate_settings(replace(settings, min_topic_size=6), 5)
        validate_settings(AnalysisSettings(), 5)

    def test_density_neighbors_are_bounded_for_small_corpus(self):
        settings = AnalysisSettings(min_topic_size=2, min_samples=20)
        validate_settings(settings, 5)
        self.assertEqual(effective_parameters(settings, 5)["hdbscan"]["min_samples"], 4)

    def test_five_passage_analysis_saves_the_actual_backend_result(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            directory = create_project(root / "project", "Small corpus").directory
            source = root / "five.txt"
            source.write_text("\n\n".join(f"Passage {i} discusses meaningful research evidence." for i in range(5)))
            doc = import_document(directory, source).document_id
            for topics in [[0, 0, 1, 1, -1], [-1] * 5]:
                with self.subTest(topics=topics):
                    def backend(passages, *args):
                        self.assertEqual(len(passages), 5)
                        return TopicOutput(topics, {0: [("research", .8)], 1: [("evidence", .9)], -1: []})
                    progress = []
                    run, count = analyze(directory, "Paragraphs", [doc], AnalysisSettings(min_topic_size=2),
                                         backend=backend, progress=progress.append)
                    self.assertEqual(count, 5)
                    self.assertEqual(sum(n for _, _, n in list_themes(directory, run)), 5)
                    self.assertTrue(any("Small corpus" in message for message in progress))
