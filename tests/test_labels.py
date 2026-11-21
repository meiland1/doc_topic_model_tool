"""Mocked local HTTP responses; no httpx installation or Ollama server needed."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from qualitative_themes.analysis.labels import (SCHEMA, TEMPLATE_VERSION, label_run,
    labeling_report, list_local_models, validate_label)
from qualitative_themes.analysis.topics import AnalysisCancelled, TopicOutput, analyze
from qualitative_themes.storage.coding import merge_themes, rename_theme
from qualitative_themes.storage.database import connect, create_project
from qualitative_themes.storage.documents import import_document


class Reply:
    def __init__(self, data, status=200):
        self.data, self.status = data, status
    @property
    def status_code(self):
        return self.status
    def raise_for_status(self):
        if self.status != 200:
            raise RuntimeError(f"HTTP {self.status}")
    def json(self):
        return self.data


class MockClient:
    def __init__(self, outputs=None, models=None, on_post=None):
        self.outputs = outputs or []
        self.models = models if models is not None else [{"name": "local:small", "digest": "sha256:fixture"}]
        self.posts, self.closed, self.on_post, self.unloads = [], False, on_post, []
    def __enter__(self):
        return self
    def __exit__(self, *args):
        self.close()
    def close(self):
        self.closed = True
    def get(self, path, **kwargs):
        assert path == "/api/tags"
        return Reply({"models": self.models})
    def post(self, path, **kwargs):
        assert path == "/api/generate"
        if "prompt" not in kwargs["json"]:  # keep_alive=0: unload the model, not a naming request
            self.unloads.append(kwargs["json"])
            return Reply({"done": True})
        self.posts.append(kwargs["json"])
        if self.on_post:
            self.on_post()
        output = self.outputs.pop(0) if self.outputs else {"label": "Local research theme", "description": "Observed evidence."}
        if isinstance(output, Exception):
            raise output
        if isinstance(output, Reply):
            return output
        return Reply({"done": True, "response": json.dumps(output)})


class LabelingTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.directory = create_project(root/"project", "Labels").directory
        docs = []
        for doc in range(2):
            path = root/f"{doc}.txt"
            path.write_text("\n\n".join(f"Document {doc} observation {i}. " + "Meaningful interview evidence. "*40 for i in range(6)), encoding="utf-8")
            docs.append(import_document(self.directory, path).document_id)
        (root/"unrelated-private.txt").write_text("SECRET_UNRELATED_FILE")
        def fake(passages, *args):
            topics = [-1] + [i % 2 for i in range(1, len(passages))]
            return TopicOutput(topics, {-1: [], 0: [("access", .8)], 1: [("trust", .7)]})
        self.run, _ = analyze(self.directory, "Paragraphs", docs, backend=fake)
        with connect(self.directory) as conn:
            info = json.loads(conn.execute("SELECT model_info_json FROM analysis_runs WHERE id=?", (self.run,)).fetchone()[0])
        self.mapping = info["topic_theme_map"]

    def label(self, client):
        return label_run(self.directory, self.run, "local:small", client_factory=lambda: client)

    def name(self, topic):
        with connect(self.directory) as conn:
            return conn.execute("SELECT coalesce(user_label,generated_label) FROM themes WHERE id=?", (self.mapping[str(topic)],)).fetchone()[0]

    def test_structured_labels_metadata_and_bounded_evidence(self):
        client = MockClient([{"label": "Access barriers"}, {"label": "Trust in institutions", "description": "Accounts of trust."}])
        attempt = self.label(client)
        self.assertTrue(client.closed)
        self.assertEqual(self.name(0), "Access barriers")
        self.assertEqual(self.name(-1), "Not in any theme")
        self.assertEqual(attempt["model_identity"]["digest"], "sha256:fixture")
        self.assertEqual(attempt["template_version"], TEMPLATE_VERSION)
        for payload in client.posts:
            self.assertFalse(payload["stream"])
            self.assertEqual(payload["format"], SCHEMA)
            self.assertLessEqual(payload["options"]["temperature"], .1)
            evidence = json.loads(payload["prompt"].split("\nEvidence: ", 1)[1])
            self.assertLessEqual(len(evidence["representative_passages"]), 3)
            self.assertTrue(all(len(text) <= 800 for text in evidence["representative_passages"]))
            self.assertNotIn("SECRET_UNRELATED_FILE", payload["prompt"])
        self.assertIn("Ollama label", labeling_report(self.directory, self.run))

    def test_invalid_response_and_timeout_use_per_theme_fallback(self):
        attempt = self.label(MockClient([{"label": ""}, TimeoutError("request timed out")]))
        failed = [r for r in attempt["records"] if r["status"] == "fallback"]
        self.assertEqual(len(failed), 2)
        self.assertEqual(self.name(0), "Theme 1: access")
        self.assertIn("timed out", labeling_report(self.directory, self.run))
        with connect(self.directory) as conn:
            self.assertEqual(conn.execute("SELECT status FROM analysis_runs WHERE id=?", (self.run,)).fetchone()[0], "completed")

    def test_status_line_says_when_naming_did_not_work(self):
        from qualitative_themes.analysis.labels import naming_message, naming_outcome
        self.label(MockClient([{"label": "Access barriers"}, TimeoutError("request timed out")]))
        self.assertEqual(naming_outcome(self.directory, self.run), (1, 2, "request timed out"))
        self.assertIn("Named 1 of 2 themes", naming_message(self.directory, self.run))
        self.label(MockClient([{"label": ""}, {"label": "x", "extra": 1}]))
        message = naming_message(self.directory, self.run)
        self.assertTrue(message.startswith("Theme naming didn't work"))
        self.assertIn("1–80 characters", message)
        self.label(MockClient([{"label": "Access barriers"}, {"label": "Trust"}]))
        self.assertEqual(naming_message(self.directory, self.run), "Named 2 themes. Names you gave themes were kept.")

    def test_ollama_reason_is_kept_and_a_failing_engine_is_not_asked_again(self):
        broken = Reply({"error": "unexpected EOF reading model file"}, 500)
        client = MockClient([broken, broken, {"label": "never asked"}])
        attempt = self.label(client)
        self.assertEqual(len(client.posts), 2)
        errors = [r["error"] for r in attempt["records"] if r["status"] == "fallback"]
        self.assertTrue(all("unexpected EOF" in error for error in errors))
        self.assertTrue(errors[0].startswith("Ollama error 500: "))
        client = MockClient([Reply({"error": "bad prompt"}, 400), {"label": "Trust"}])
        self.label(client)
        self.assertEqual(len(client.posts), 2)  # a 4xx is about that request, so carry on

    def test_graphics_card_crash_retries_on_the_processor(self):
        crash = Reply({"error": "llama runner process has terminated: GGML_ASSERT(ptr == (void *) "
                                "((char *)(pool_addr) + pool_used)) failed"}, 500)
        client = MockClient([crash, {"label": "Access barriers"}, {"label": "Trust"}])
        attempt = self.label(client)
        self.assertEqual([p["options"].get("num_gpu") for p in client.posts], [None, 0, 0])
        self.assertTrue(attempt["processor_only"])
        self.assertEqual({r["status"] for r in attempt["records"] if r["topic_id"] != -1}, {"generated"})
        self.assertIn("Ran on the processor", labeling_report(self.directory, self.run))
        # Crashing on the processor too is a real failure, reported as one.
        client = MockClient([crash, crash, crash])
        attempt = self.label(client)
        self.assertEqual(len(client.posts), 3)  # first theme: card, then processor; second: processor, then stop
        self.assertTrue(all("pool_addr" in r["error"] for r in attempt["records"] if r["status"] == "fallback"))

    def test_model_is_unloaded_after_naming(self):
        client = MockClient([{"label": "Access barriers"}, {"label": "Trust"}])
        self.label(client)
        self.assertEqual(client.unloads, [{"model": "local:small", "keep_alive": 0}])
        offline = MockClient(models=[])
        self.label(offline)
        self.assertEqual(offline.unloads, [])  # nothing was loaded

    def test_out_of_memory_stops_at_once_with_a_plain_message(self):
        oom = Reply({"error": "llama-server reported out-of-memory during startup: unable to allocate "
                              "CUDA_Host buffer"}, 500)
        client = MockClient([oom, {"label": "never asked"}])
        attempt = self.label(client)
        self.assertEqual(len(client.posts), 1)  # no processor retry, no second theme
        errors = {r["error"] for r in attempt["records"] if r["status"] == "fallback"}
        self.assertEqual(len(errors), 1)

        self.assertTrue(errors.pop().startswith("Not enough free memory to load local:small."))
        self.assertFalse(attempt["processor_only"])

    def test_unavailable_service_and_uninstalled_model_retain_analysis(self):
        def offline():
            raise ConnectionError("Connection refused")
        attempt = label_run(self.directory, self.run, "missing", client_factory=offline)
        self.assertEqual(sum(r["status"] == "fallback" for r in attempt["records"]), 2)
        client = MockClient(models=[])
        self.label(client)
        self.assertFalse(client.posts)
        self.assertTrue(client.closed)
        self.assertIn("Keyword", labeling_report(self.directory, self.run).title())

    def test_rename_before_and_during_labeling_is_protected(self):
        rename_theme(self.directory, self.mapping["0"], "Researcher's name")
        def rename_during_request():
            rename_theme(self.directory, self.mapping["1"], "Another human name")
        attempt = self.label(MockClient(on_post=rename_during_request))
        self.assertEqual(self.name(0), "Researcher's name")
        self.assertEqual(self.name(1), "Another human name")
        self.assertEqual(sum(r["status"] == "protected" for r in attempt["records"]), 2)

    def test_merged_themes_are_not_relabeled(self):
        merge_themes(self.directory, self.mapping["0"], self.mapping["1"])
        client = MockClient()
        attempt = self.label(client)
        self.assertFalse(client.posts)
        self.assertEqual(sum(r["status"] == "protected" for r in attempt["records"]), 1)
        self.assertEqual(sum(r["status"] == "skipped_merged" for r in attempt["records"]), 1)

    def test_description_changed_during_request_is_preserved(self):
        def edit():
            with connect(self.directory) as conn:
                conn.execute('UPDATE themes SET description=? WHERE id=?',
                             ('Researcher definition', self.mapping['0']))
        attempt = self.label(MockClient(on_post=edit))
        self.assertEqual(next(r['status'] for r in attempt['records'] if r['topic_id'] == 0), 'protected')
        with connect(self.directory) as conn:
            self.assertEqual(conn.execute('SELECT description FROM themes WHERE id=?',
                                         (self.mapping['0'],)).fetchone()[0], 'Researcher definition')

    def test_cancellation_does_not_publish_partial_names(self):
        calls = []
        def cancel():
            return bool(calls)
        client = MockClient(on_post=lambda: calls.append(True))
        with self.assertRaises(AnalysisCancelled):
            label_run(self.directory, self.run, "local:small", client_factory=lambda: client, should_cancel=cancel)
        self.assertTrue(client.closed)
        self.assertEqual(self.name(0), "Theme 1: access")
        self.assertIn("BERTopic keywords", labeling_report(self.directory, self.run))

    def test_local_model_list_filters_remote_models(self):
        client = MockClient(models=[{"name": "local:small"}, {"name": "remote-cloud:latest"},
                                   {"name": "remote", "remote_host": "https://example.com"}])
        self.assertEqual([m["name"] for m in list_local_models(lambda: client)], ["local:small"])
        self.assertTrue(client.closed)

    def test_model_loading_is_announced_once_on_first_real_request(self):
        # Uncategorized (-1) comes first and is skipped without a request, so the
        # loading notice belongs to the first theme that actually calls the engine.
        messages, client = [], MockClient()
        label_run(self.directory, self.run, "local:small", client_factory=lambda: client,
                  progress=messages.append)
        loading = [m for m in messages if "into memory" in m]
        self.assertEqual(len(loading), 1)
        self.assertTrue(loading[0].startswith("Naming theme 2/"))
        self.assertIn("local:small", loading[0])
        self.assertEqual(len(client.posts), 2)

    def test_naming_estimate_wording(self):
        from qualitative_themes.analysis.labels import naming_estimate
        self.assertEqual(naming_estimate(300), "Naming about 300 themes will take roughly 30 minutes. "
                                               "You can cancel at any time.")
        self.assertIn("roughly 1 minute.", naming_estimate(3))

    def test_cancel_during_final_write_releases_lock_and_can_retry(self):
        from contextlib import contextmanager
        from unittest.mock import patch
        cancelled = [False]
        @contextmanager
        def traced(directory):
            with connect(directory) as conn:
                def trace(sql):
                    if sql.startswith('UPDATE themes SET generated_label='):
                        cancelled[0] = True
                conn.set_trace_callback(trace)
                yield conn
        with patch('qualitative_themes.analysis.labels.connect', traced):
            with self.assertRaises(AnalysisCancelled):
                label_run(self.directory, self.run, 'local:small', client_factory=lambda: MockClient(),
                          should_cancel=lambda: cancelled[0])
        self.assertEqual(self.name(0), 'Theme 1: access')
        with connect(self.directory) as conn:
            conn.execute('BEGIN IMMEDIATE')  # No abandoned write lock remains.
        attempt = self.label(MockClient())
        self.assertTrue(any(r['status'] == 'generated' for r in attempt['records']))

    def test_invalid_json_http_error_and_description_limits(self):
        self.label(MockClient([Reply({"done": True, "response": "not JSON"}), Reply({}, 500)]))
        self.assertIn("Ollama error 500: no reason given", labeling_report(self.directory, self.run))
        for value in [{"label": "bad\nline"}, {"label": "x", "description": "y"*401},
                      {"label": "x", "unexpected": True}, {"label": 5}]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_label(value)


if __name__ == "__main__":
    unittest.main()
