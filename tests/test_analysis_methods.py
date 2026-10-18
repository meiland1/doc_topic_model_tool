import json
import unittest
from dataclasses import asdict, replace
from types import SimpleNamespace
import numpy as np
from qualitative_themes.analysis.settings import AnalysisSettings, settings_from_dict, parse_topic_definitions, validate_settings
from qualitative_themes.analysis.methods import guided_seed_topics, match_predefined, zero_shot_partition, restore_partition_keywords
from qualitative_themes.analysis.topics import TopicOutput

class AnalysisMethodTests(unittest.TestCase):
    def setUp(self):
        self.settings = AnalysisSettings(analysis_method='zero_shot', min_topic_size=2,
            topic_definitions=(('Care', 'Healthcare access'), ('Stress', 'Emotional distress')))

    def test_old_settings_and_roundtrip(self):
        self.assertEqual(settings_from_dict({}).analysis_method, 'general')
        self.assertEqual(settings_from_dict(json.loads(json.dumps(asdict(self.settings)))), self.settings)
        validate_settings(self.settings, 1)

    def test_invalid_configuration(self):
        for text in ['Missing separator', 'Care | ', 'Care | clinic\ncare | medicine']:
            with self.subTest(text=text), self.assertRaises(ValueError):
                validate_settings(AnalysisSettings(analysis_method='guided', topic_definitions=parse_topic_definitions(text)), 10)
        for score in [-1, 1.1, float('nan'), True]:
            with self.subTest(score=score), self.assertRaises(ValueError):
                validate_settings(replace(self.settings, zeroshot_min_similarity=score), 10)
        with self.assertRaises(ValueError):
            validate_settings(replace(self.settings, requested_theme_count=2), 10)

    def test_guided_phrases(self):
        settings = replace(self.settings, analysis_method='guided', topic_definitions=(('Care', 'clinic, health insurance'),))
        self.assertEqual(guided_seed_topics(settings), [['clinic', 'health insurance']])

    def test_cosine_normalization_threshold_tie_and_zero(self):
        topics, scores = match_predefined([[2,0], [0,3], [-1,-1], [0,0]], [[4,0], [0,2]], .7)
        self.assertEqual(topics, [0,1,-1,-1])
        self.assertAlmostEqual(scores[0], 1)
        self.assertEqual(match_predefined([[1,1]], [[1,0],[0,1]], .7)[0], [0])
        with self.assertRaises(ValueError): match_predefined([[float('nan'),0]], [[1,0]], .7)

    def partition(self, vectors, discover):
        return zero_shot_partition([SimpleNamespace(id=str(i)) for i in range(len(vectors))], vectors,
                                  [[1,0],[0,1]], self.settings, discover)

    def test_all_matched(self):
        topics, info = self.partition([[1,0],[0,1]], lambda *args: self.fail('Must not cluster'))
        self.assertEqual(topics, [0,1])
        self.assertEqual(info['predefined_topic_labels'], {'0':'Care','1':'Stress'})

    def test_small_residual(self):
        topics, info = self.partition([[1,0],[-1,0],[-1,-1]], lambda *args: self.fail('Must not cluster'))
        self.assertEqual(topics, [0,-1,-1])
        self.assertTrue(info['zero_shot']['residual_discovery_skipped'])
        self.assertEqual(info['zero_shot']['unmatched_category_names'], ['Stress'])

    def test_mixed_residual_preserves_order_and_ids(self):
        def discover(passages, embeddings, settings):
            self.assertEqual([p.id for p in passages], ['0','2','3'])
            self.assertEqual(settings.analysis_method, 'general')
            return TopicOutput([4,-1,4], {})
        topics, info = self.partition([[-1,0],[0,1],[-1,-1],[0,-1]], discover)
        self.assertEqual(topics, [2,1,-1,2])
        self.assertEqual(info['topic_origins']['2'], 'discovered')

    def test_no_matches_and_cancellation(self):
        topics, info = self.partition([[-1,0]]*3, lambda *args: TopicOutput([-1]*3, {}))
        self.assertEqual(topics, [-1]*3)
        self.assertEqual(info['predefined_topic_labels'], {})
        with self.assertRaisesRegex(RuntimeError, 'cancel'):
            zero_shot_partition([SimpleNamespace(id='x')], [[1,0]], [[1,0]], self.settings, None,
                                lambda: (_ for _ in ()).throw(RuntimeError('cancel')))

    def test_keyword_ids_restored(self):
        output = TopicOutput([1,0,1], {0:[('stress',.5)],1:[('clinic',.4)]}, {0:['b'],1:['a']})
        words, reps = restore_partition_keywords([], [5,2,5], output)
        self.assertEqual(words[5], [('clinic',.4)])
        self.assertEqual(reps[2], ['b'])
        with self.assertRaises(ValueError):
            restore_partition_keywords([], [5,2,5], TopicOutput([0,0,0], {}))

class SavedCategoryTests(unittest.TestCase):
    def test_names_persist_and_ollama_skips_categories(self):
        from tempfile import TemporaryDirectory
        from pathlib import Path
        from qualitative_themes.storage.database import create_project, connect
        from qualitative_themes.storage.documents import import_document
        from qualitative_themes.storage.coding import list_themes
        from qualitative_themes.analysis.topics import analyze
        from qualitative_themes.analysis.labels import label_run, labeling_report
        from qualitative_themes.analysis.refinement import propose_refinement
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            directory = create_project(root/'project','Test').directory
            source = root/'one.txt'
            source.write_text('Patients cannot afford medication.', encoding='utf-8')
            document = import_document(directory, source).document_id
            settings = AnalysisSettings(analysis_method='zero_shot', topic_definitions=(('Care access','Access to healthcare'),))
            def fake(passages,*args):
                return TopicOutput([0], {0:[('medication',.4)]}, model_info={'predefined_topic_labels':{'0':'Care access'}})
            run, count = analyze(directory,'Whole document',[document],settings,backend=fake)
            self.assertEqual(count,1)
            self.assertEqual(list_themes(directory,run)[0][1], 'Care access')
            def unavailable(): self.fail('Ollama must not be contacted for supplied categories')
            label_run(directory,run,None,client_factory=unavailable)
            self.assertIn('Care access', labeling_report(directory,run))
            self.assertEqual(list_themes(directory,run)[0][1], 'Care access')
            with connect(directory) as conn:
                recorded = json.loads(conn.execute('SELECT settings_json FROM analysis_runs WHERE id=?',(run,)).fetchone()[0])
            self.assertEqual(recorded['analysis_method'], 'zero_shot')
            with self.assertRaisesRegex(ValueError,'disabled'):
                propose_refinement(directory,run,'reduce',replace(settings,requested_theme_count=1))
            def keyword_refresh(passages, assignments, *args):
                return TopicOutput([0], {0:[('treatment',.6)]})
            proposal, _ = propose_refinement(directory,run,'words',representation_backend=keyword_refresh)
            with connect(directory) as conn:
                info=json.loads(conn.execute('SELECT model_info_json FROM analysis_runs WHERE id=?',(proposal,)).fetchone()[0])
            self.assertEqual(info['predefined_topic_labels'], {'0':'Care access'})

class BackendWiringTests(unittest.TestCase):
    def test_guided_and_zero_shot_use_local_encoder_and_cached_vectors(self):
        from unittest.mock import patch
        from tempfile import TemporaryDirectory
        from pathlib import Path
        import sys
        from types import ModuleType
        from qualitative_themes.analysis import topics as engine
        from qualitative_themes.analysis import refinement
        from contextlib import contextmanager
        @contextmanager
        def replace_imports(replacements):
            # Restore only mocked entries. patch.dict(sys.modules) restores the
            # entire registry, unloading native dependencies imported inside it.
            missing = object()
            previous = {name: sys.modules.get(name, missing) for name in replacements}
            sys.modules.update(replacements)
            try:
                yield
            finally:
                for name, module in previous.items():
                    if module is missing:
                        sys.modules.pop(name, None)
                    else:
                        sys.modules[name] = module
        captured = []
        class Encoder:
            max_seq_length = 256
            def __init__(self,*args,**kwargs):
                assert kwargs['local_files_only'] is True
            def get_sentence_embedding_dimension(self): return 2
            def encode(self,texts,**kwargs):
                return np.array([[1.,0.] if ('care' in text.lower() or 'clinic' in text.lower()) else [-1.,0.] for text in texts])
        class Algorithm:
            def __init__(self,**kwargs): pass
        class Model:
            def __init__(self,**kwargs): captured.append(kwargs)
            def fit_transform(self,texts,embeddings):
                embeddings[:] = 99  # Simulate guided BERTopic mutating its input.
                self.topics_ = [0]*len(texts)
                self.probabilities_ = None
            def get_topic(self,topic): return [('clinic',.4)]
            def get_representative_docs(self,topic): return []
        modules = {}
        for name, attribute, value in [('bertopic','BERTopic',Model), ('sentence_transformers','SentenceTransformer',Encoder), ('umap','UMAP',Algorithm), ('hdbscan','HDBSCAN',Algorithm)]:
            module=ModuleType(name);setattr(module,attribute,value);modules[name]=module
        passages=[SimpleNamespace(id=str(i),text=text) for i,text in enumerate(['clinic care','clinic medicine','unrelated orbit'])]
        def represent(passages, assignments,*args):
            return TopicOutput(assignments, {t:[('evidence',.2)] for t in set(assignments)})
        with TemporaryDirectory() as temporary, replace_imports(modules), patch.object(engine,'version',return_value='test'), patch.object(refinement,'fixed_partition_backend',side_effect=represent):
            directory=Path(temporary)
            guided=AnalysisSettings(analysis_method='guided',min_topic_size=2,topic_definitions=(('Care','clinic, health insurance'),))
            result=engine.fit_bertopic(passages,guided,directory,lambda *args:None,None)
            self.assertEqual(captured[-1]['seed_topic_list'],[['clinic','health insurance']])
            self.assertEqual(result.model_info['analysis_method'],'guided')
            zero=replace(guided,analysis_method='zero_shot',topic_definitions=(('Care access','Healthcare access'),))
            result=engine.fit_bertopic(passages,zero,directory,lambda *args:None,None)
            self.assertEqual(result.assignments,[0,0,-1])
            self.assertEqual(result.model_info['predefined_topic_labels'],{'0':'Care access'})
            self.assertEqual(result.scores,[])  # Similarity is never stored as confidence.
            for path in directory.glob('models/embeddings/*/*.npy'):
                self.assertLessEqual(np.abs(np.load(path)).max(),1)
            import numpy.fft as fft_inside
        # A later model/visualization test must reuse native modules, not reload them.
        import numpy.fft
        from sklearn.decomposition import PCA
        self.assertIs(sys.modules["numpy.fft"], fft_inside)
        self.assertEqual(np.fft.fft([1., 0.]).shape, (2,))
        self.assertEqual(PCA(n_components=1).fit_transform([[0.,1.],[1.,0.]]).shape, (2,1))
