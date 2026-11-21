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
        validate_settings(preset_settings("Broad"), 5)  # small-collection grouping
        with self.assertRaisesRegex(ValueError, "need at least 40"):
            validate_settings(preset_settings("Broad", min_topic_size=40), 35)
        params = effective_parameters(preset_settings("Detailed", n_neighbors=50), 36)
        self.assertEqual(params["umap"]["n_neighbors"], 35)
        self.assertEqual(params["umap"]["n_components"], 5)
        for change in [{"requested_theme_count": 50}, {"requested_theme_count": 0}, {"min_topic_size": 0},
                       {"batch_size": 0}, {"min_samples": 1.5}, {"top_n_words": 4}, {"top_n_words": 21},
                       {"keyword_style": "fancy"}, {"max_themes": 0}, {"fill_threshold": 1.5},
                       {"auto_merge": "yes"}]:
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_settings(replace(AnalysisSettings(), **change), 12)

    def test_more_options_have_our_defaults_and_old_runs_load(self):
        settings = AnalysisSettings()
        self.assertEqual((settings.fill_leftovers, settings.fill_threshold, settings.keyword_style,
                          settings.play_down_common_words, settings.top_n_words, settings.auto_merge,
                          settings.max_themes, settings.seed), (False, 0.5, "varied", True, 10, False, 100, 42))
        from qualitative_themes.analysis.settings import settings_from_dict, settings_report, unsorted_level
        old = settings_from_dict({"analysis_style": "Balanced", "min_samples": 2, "min_topic_size": 5})
        self.assertEqual(old.max_themes, 100)
        self.assertEqual(unsorted_level(old), "Balanced")
        self.assertEqual(unsorted_level(replace(old, min_samples=1)), "Fewer")
        report = settings_report({"analysis_style": "Balanced", "min_samples": 2, "min_topic_size": 5}, {})
        self.assertIn("Keyword style:", report)
        self.assertIn("not recorded (plain counts)", report)
        self.assertIn("Random seed:", report)
        new = settings_report(vars(AnalysisSettings()) | {"analysis_unit": "Paragraphs"},
                              {"small_collection": True, "small_collection_distance": 0.42, "filled_leftovers": 3})
        self.assertIn("More varied (recommended)", new)
        self.assertIn("cut at distance 0.42", new)
        self.assertIn("How documents were split", new)

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
        def goal(target, cap=100):
            return replace(AnalysisSettings(), requested_theme_count=target, max_themes=cap)
        model = Model([-1, 0, 1, 2, 3])
        self.assertEqual(apply_topic_goal(model, ["text"]*5, goal(2), lambda _: None), 4)
        self.assertEqual(model.targets, [3])
        no_outlier = Model([0, 1, 2])
        apply_topic_goal(no_outlier, ["text"]*3, goal(1), lambda _: None)
        self.assertEqual(no_outlier.targets, [1])
        for target in [None, 4, 20]:
            model = Model([0, 1])
            apply_topic_goal(model, ["text"]*2, goal(target), lambda _: None)
            self.assertEqual(model.targets, [])

    def test_theme_cap_applies_after_discovery_and_with_about_n(self):
        class Model:
            def __init__(self, count):
                self.topics_, self.targets = [-1, *range(count)], []
            def reduce_topics(self, texts, nr_topics):
                self.targets.append(nr_topics)
        messages = []
        model = Model(300)
        self.assertEqual(apply_topic_goal(model, [], replace(AnalysisSettings(), max_themes=100), messages.append), 300)
        self.assertEqual(model.targets, [101])  # 100 themes plus "Not in any theme"
        self.assertIn("stay within 100", messages[0])
        model = Model(300)
        apply_topic_goal(model, [], replace(AnalysisSettings(), requested_theme_count=150, max_themes=100), print)
        self.assertEqual(model.targets, [101])  # the cap wins over a larger About N

    def test_old_run_settings_restore_with_safe_defaults(self):
        settings = settings_from_dict({"analysis_unit": "Paragraphs", "min_topic_size": 5,
                                       "seed": 42, "parameters": {"ignored": "old"}})
        self.assertEqual(settings, AnalysisSettings())


if __name__ == "__main__":
    unittest.main()
