"""Read-only visualization preparation, mappings, projections, and cancellation."""
import json
from unittest.mock import patch
import numpy as np
import test_exports as export_fixtures
from qualitative_themes.analysis.visualizations import (visualization_snapshot, restored_theme_mapping,
    project_2d, build_visualizations, offline_html)
from qualitative_themes.analysis.topics import AnalysisCancelled
from qualitative_themes.storage.coding import add_coding, reject_model_suggestion, merge_themes, rename_theme

# Reuse setup only; do not inherit and rerun the export test cases.
import unittest
class VisualizationTests(unittest.TestCase):
    setUp = export_fixtures.ExportTests.setUp
    snapshot = export_fixtures.ExportTests.snapshot

    def test_current_coding_labels_multithemes_and_rejections(self):
        before = self.snapshot()
        data = visualization_snapshot(self.directory, self.run)
        self.assertEqual(len(data['texts']), 6)
        self.assertEqual(self.snapshot(), before)
        first = data['passages'][0]
        tid = first['assignments'][0]['theme_id']
        reject_model_suggestion(self.directory, self.docs[0], first['start_char'], first['end_char'], tid)
        add_coding(self.directory, self.docs[0], 0, 10, self.manual)
        add_coding(self.directory, self.docs[0], 0, 10, tid)
        rename_theme(self.directory, tid, 'Current name')
        updated = visualization_snapshot(self.directory, self.run)
        self.assertEqual(updated['themes'][tid]['theme'], 'Current name')
        counts = [updated['range_indices'].count(i) for i in range(len(updated['passages']))]
        self.assertIn(2, counts)
        self.assertEqual(len(updated['texts']), 7)
        merge_themes(self.directory, tid, self.manual)
        merged = visualization_snapshot(self.directory, self.run)
        self.assertNotIn(tid, merged['theme_topics'])
        self.assertIn(self.manual, merged['theme_topics'])

    def test_mapping_checks_ignore_topic_number_identity(self):
        self.assertEqual(restored_theme_mapping([0,1,0], [1,0,1], {'a':0,'b':1}), {1:'a',0:'b'})
        for after in [[0,0,0], [0,1,1], [0,1], [-1,1,-1]]:
            with self.assertRaises(ValueError): restored_theme_mapping([0,1,0], after, {'a':0,'b':1})

    def test_projection_is_finite_deterministic_and_handles_tiny_data(self):
        values = np.array([[1,0,0],[0,1,0],[0,0,1]])
        projected = project_2d(values)
        self.assertEqual(projected.shape, (3,2))
        np.testing.assert_allclose(projected, project_2d(values))
        np.testing.assert_allclose(project_2d([[1,2,3]]), [[0,0]])
        np.testing.assert_allclose(project_2d([[1],[1]]), [[0,0],[0,0]])
        with self.assertRaises(ValueError): project_2d([[float('nan')]])

    def test_no_analysis_and_cancel_leave_project_unchanged(self):
        before = self.snapshot()
        with self.assertRaises(ValueError): visualization_snapshot(self.directory, None)
        with self.assertRaises(AnalysisCancelled): visualization_snapshot(self.directory, self.run, lambda: True)
        self.assertEqual(self.snapshot(), before)

    def test_offline_html_uses_embedded_plotly_and_blocks_connections(self):
        class Figure:
            def update_layout(self, **kwargs): pass
            def to_html(self, **kwargs):
                self.options = kwargs
                return '<html><head></head><body>Plot</body></html>'
        figure = Figure()
        content = offline_html(figure)
        self.assertIs(figure.options['include_plotlyjs'], True)
        self.assertIn("connect-src 'none'", content)

    def test_builtin_bertopic_contract_with_fake_model_and_embedding_provider(self):
        class Figure:
            def update_layout(self, **kwargs): pass
            def to_html(self, **kwargs): return '<html><head></head><body>Plot</body></html>'
        class Model:
            def fit_transform(self, texts, embeddings, y):
                self.topics_ = list(y)
                self.texts = texts
            def set_topic_labels(self, labels): self.labels = labels
            def visualize_documents(self, docs, **kwargs):
                assert len(docs) == len(self.texts)
                assert kwargs['reduced_embeddings'].shape == (len(docs),2)
                assert kwargs['custom_labels']
                return Figure()
            def visualize_heatmap(self, **kwargs):
                assert kwargs['use_ctfidf'] and kwargs['custom_labels']
                return Figure()
            def hierarchical_topics(self, texts, **kwargs):
                import pandas as pd
                return pd.DataFrame([{'Child_Left_ID':0, 'Child_Left_Name':'old', 'Child_Right_ID':1, 'Child_Right_Name':'old'}])
            def visualize_hierarchy(self, **kwargs): return Figure()
            def get_topic_tree(self, hierarchy):
                return ' / '.join(hierarchy['Child_Left_Name']) + ' / ' + ' / '.join(hierarchy['Child_Right_Name'])
        before = self.snapshot()
        def embeddings(directory, texts, *args):
            return np.array([[i, i*i, 1] for i in range(len(texts))])
        result = build_visualizations(self.directory, self.run, embedding_provider=embeddings, model_factory=lambda vectorizer: Model())
        self.assertEqual(set(result['html']), {'map','heatmap','hierarchy'})
        self.assertIn('Theme ', result['tree'])
        self.assertEqual(self.snapshot(), before)

    def test_all_outliers_and_missing_map_weights_have_readable_fallbacks(self):
        from qualitative_themes.analysis.topics import analyze, AnalysisSettings, TopicOutput
        run, _ = analyze(self.directory, 'Paragraphs', [self.docs[0]], AnalysisSettings(min_topic_size=2),
            backend=lambda passages,*args: TopicOutput([-1]*len(passages), {-1: []}))
        class Model:
            def fit_transform(self, texts, embeddings, y): self.topics_ = list(y)
            def set_topic_labels(self, labels): pass
        def unavailable(*args): raise ValueError('No local weights')
        before = self.snapshot()
        result = build_visualizations(self.directory, run, embedding_provider=unavailable, model_factory=lambda vectorizer: Model())
        self.assertIn('No local weights', result['messages']['map'])
        self.assertIn('Only Uncategorized', result['messages']['heatmap'])
        self.assertIn('at least two', result['messages']['hierarchy'])
        self.assertIn('Outside hierarchy: Uncategorized', result['tree'])
        self.assertEqual(self.snapshot(), before)

    def test_cached_embeddings_do_not_load_or_download_a_model(self):
        from qualitative_themes.analysis.visualizations import local_embeddings
        import hashlib
        data = visualization_snapshot(self.directory, self.run)
        identity = {'model':'offline', 'revision':'saved'}
        metadata = data['metadata']
        metadata['model_info']['embedding_identity'] = identity
        cache = self.directory/'models'/'embeddings'/hashlib.sha256(json.dumps(identity,sort_keys=True).encode()).hexdigest()
        cache.mkdir(parents=True)
        texts = ['first', 'second']
        for i,text in enumerate(texts):
            np.save(cache/(hashlib.sha256(text.encode()).hexdigest()+'.npy'), np.array([i,1,2],dtype=float))
        vectors = local_embeddings(self.directory, texts, metadata, lambda msg: None, None)
        np.testing.assert_allclose(vectors, [[0,1,2],[1,1,2]])

    def test_map_cancellation_is_not_swallowed_as_a_plot_error(self):
        class Model:
            def fit_transform(self, texts, embeddings, y): self.topics_ = list(y)
            def set_topic_labels(self, labels): pass
        def cancelled(*args): raise AnalysisCancelled()
        with self.assertRaises(AnalysisCancelled):
            build_visualizations(self.directory, self.run, embedding_provider=cancelled, model_factory=lambda vectorizer: Model())
