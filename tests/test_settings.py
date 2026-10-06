from dataclasses import asdict, replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from qualitative_themes.analysis.settings import (PRESETS, AnalysisSettings, effective_parameters,
    parse_ignored_words, preset_settings, settings_from_dict, validate_settings, vectorizer_options)
from qualitative_themes.analysis.topics import TopicOutput, analyze, apply_topic_goal
from qualitative_themes.storage.database import create_project, connect
from qualitative_themes.storage.documents import import_document
from qualitative_themes.storage.preferences import load_setup, save_setup, result_summary


class SettingsTests(unittest.TestCase):
    def test_presets_are_distinct_and_resolve_explicit_parameters(self):
        self.assertEqual(AnalysisSettings(), preset_settings("Balanced"))
        expected = {"Balanced": (5, 2, 15, "eom"), "Broad": (10, 3, 25, "eom"),
                    "Detailed": (3, 1, 10, "eom"), "Exploratory": (2, 1, 5, "leaf")}
        for style, values in expected.items():
            with self.subTest(style=style):
                settings = preset_settings(style)
                self.assertEqual((settings.min_topic_size, settings.min_samples, settings.n_neighbors,
                                  settings.cluster_selection_method), values)
                validate_settings(settings, 45)
                params = effective_parameters(settings, 45)
                self.assertEqual(params["hdbscan"]["min_cluster_size"], values[0])
                self.assertEqual(params["umap"]["n_neighbors"], values[2])
        self.assertEqual(set(expected), set(PRESETS))

    def test_small_corpus_validation_and_neighbor_clamping(self):
        with self.assertRaisesRegex(ValueError, "minimum evidence"):
            validate_settings(preset_settings("Broad"), 5)
        validate_settings(preset_settings("Broad"), 12)
        validate_settings(preset_settings("Detailed"), 6)
        params = effective_parameters(preset_settings("Detailed"), 6)
        self.assertEqual(params["umap"]["n_neighbors"], 5)
        self.assertEqual(params["umap"]["n_components"], 4)
        for change in [{"requested_theme_count": 50}, {"requested_theme_count": 0},
                       {"min_topic_size": 1}, {"batch_size": 0}, {"min_samples": 1.5}]:
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_settings(replace(AnalysisSettings(), **change), 12)

    def test_custom_ignored_words_and_keyword_phrases(self):
        words = parse_ignored_words("Interview, INTERVIEW\nresearch; café")
        self.assertEqual(words, ("café", "interview", "research"))
        settings = preset_settings("Detailed", ignored_words=words, ngram_range=(1, 1))
        args = vectorizer_options(settings, {"the", "and"})
        self.assertEqual(args["stop_words"], ["and", "café", "interview", "research", "the"])
        self.assertEqual(args["ngram_range"], (1, 1))
        self.assertEqual(vectorizer_options(AnalysisSettings(), set())["stop_words"], "english")

    def test_roundtrip_preferences_and_effective_run_serialization(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            directory = create_project(root/"project", "Settings").directory
            source = root/"source.txt"
            source.write_text("\n\n".join(f"Observation {i}: evidence of access and trust." for i in range(12)), encoding="utf-8")
            doc = import_document(directory, source).document_id
            settings = preset_settings("Detailed", ignored_words=("observation",), requested_theme_count=2,
                                       name_themes=True, ollama_model="local:small")
            save_setup(directory, settings, "Paragraphs")
            restored, unit = load_setup(directory)
            self.assertEqual(restored, settings)
            self.assertEqual(unit, "Paragraphs")
            self.assertEqual(settings_from_dict(json.loads(json.dumps(asdict(settings)))), settings)
            captured = []
            def fake(passages, actual, *args):
                captured.append(actual)
                return TopicOutput([-1] + [i % 2 for i in range(11)], {0: [("access", .5)], 1: [("trust", .5)], -1: []})
            run, _ = analyze(directory, unit, [doc], settings, backend=fake)
            self.assertEqual(captured, [settings])
            self.assertEqual(result_summary(directory, run), (2, 1))
            with connect(directory) as conn:
                record = json.loads(conn.execute("SELECT settings_json FROM analysis_runs WHERE id=?", (run,)).fetchone()[0])
                info = json.loads(conn.execute("SELECT model_info_json FROM analysis_runs WHERE id=?", (run,)).fetchone()[0])
            self.assertEqual(record["parameters"], effective_parameters(settings, 12))
            self.assertEqual(record["parameters"]["vectorizer"]["additional_stop_words"], ["observation"])
            self.assertEqual(record["ollama_model"], "local:small")
            self.assertEqual(info["actual_theme_count"], 2)

    def test_reduction_goal_excludes_outliers_and_never_increases_topics(self):
        class Model:
            def __init__(self, topics):
                self.topics_, self.targets = topics, []
            def reduce_topics(self, texts, nr_topics):
                self.targets.append(nr_topics)
        model = Model([-1, 0, 1, 2, 3])
        self.assertEqual(apply_topic_goal(model, ["text"]*5, 2, lambda _: None), 4)
        self.assertEqual(model.targets, [3])
        no_outlier = Model([0, 1, 2])
        apply_topic_goal(no_outlier, ["text"]*3, 1, lambda _: None)
        self.assertEqual(no_outlier.targets, [1])
        for target in [None, 4, 20]:
            model = Model([0, 1])
            apply_topic_goal(model, ["text"]*2, target, lambda _: None)
            self.assertEqual(model.targets, [])

    def test_old_run_settings_restore_with_safe_defaults(self):
        settings = settings_from_dict({"analysis_unit": "Paragraphs", "min_topic_size": 5,
                                       "seed": 42, "parameters": {"ignored": "old"}})
        self.assertEqual(settings, AnalysisSettings())


if __name__ == "__main__":
    unittest.main()
