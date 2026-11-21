"""A five-passage corpus is supported without manufacturing extra passages."""
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from qualitative_themes.analysis.settings import (
    SMALL_COLLECTION, AnalysisSettings, effective_parameters, small_corpus_warning, validate_settings,
)
from qualitative_themes.analysis.topics import TopicOutput, analyze
from qualitative_themes.storage.database import create_project
from qualitative_themes.storage.documents import import_document
from qualitative_themes.storage.coding import list_themes


class SmallCorpusTests(unittest.TestCase):
    def test_small_collections_use_small_collection_grouping(self):
        settings = AnalysisSettings(min_topic_size=2, name_themes=True, ollama_model="llama3.1")
        validate_settings(settings, 5)
        params = effective_parameters(settings, 5)
        self.assertNotIn("umap", params)
        self.assertEqual(params["small_collection"]["linkage"], "average")
        self.assertIn("median distance", params["small_collection"]["distance_threshold"])
        self.assertIn("small-collection grouping", small_corpus_warning(settings, 5))
        aimed = effective_parameters(replace(settings, requested_theme_count=9), 5)["small_collection"]
        self.assertEqual(aimed["n_clusters"], 5)  # can't ask for more groups than passages
        self.assertIn("umap", effective_parameters(settings, SMALL_COLLECTION + 1))

    def test_a_minimum_of_one_always_uses_small_collection_grouping(self):
        settings = AnalysisSettings(min_topic_size=1)
        validate_settings(settings, 500)
        self.assertIn("small_collection", effective_parameters(settings, 500))
        self.assertIn("single passage", small_corpus_warning(settings, 500))

    def test_two_passages_is_the_floor(self):
        validate_settings(AnalysisSettings(), 2)
        with self.assertRaisesRegex(ValueError, "Only 1 passage is available; these settings need at least 2"):
            validate_settings(AnalysisSettings(), 1)
        with self.assertRaisesRegex(ValueError, "need at least 50"):
            validate_settings(AnalysisSettings(min_topic_size=50), 40)

    def test_density_neighbors_are_bounded_above_the_small_collection_size(self):
        settings = AnalysisSettings(min_topic_size=2, min_samples=60, n_neighbors=80)
        params = effective_parameters(settings, 40)
        self.assertEqual(params["hdbscan"]["min_samples"], 39)
        self.assertEqual(params["umap"]["n_neighbors"], 39)

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
                    self.assertTrue(any("small-collection grouping" in message for message in progress))
