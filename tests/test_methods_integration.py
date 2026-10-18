"""Real BERTopic pipelines with synthetic embeddings; no downloads or Ollama."""
import pytest

@pytest.mark.integration
def test_real_guided_and_zero_shot_pipelines():
    pytest.importorskip("bertopic")
    from pathlib import Path
    from tempfile import TemporaryDirectory
    from types import SimpleNamespace
    from unittest.mock import patch
    import hashlib
    import numpy as np
    from bertopic.backend import BaseEmbedder
    from qualitative_themes.analysis.topics import fit_bertopic
    from qualitative_themes.analysis.settings import AnalysisSettings
    
    class LocalTestEncoder(BaseEmbedder):
        max_seq_length=256
        def __init__(self,*args,**kwargs): super().__init__()
        def get_sentence_embedding_dimension(self): return 8
        def encode(self,texts,**kwargs):
            result=[]
            for text in texts:
                v=np.zeros(8)
                v[0 if ('clinic' in text or 'health' in text) else 1]=1
                random=np.random.default_rng(int(hashlib.sha256(text.encode()).hexdigest()[:8],16))
                v+=random.normal(0,.01,8)
                result.append(v/np.linalg.norm(v))
            return np.asarray(result)
        def embed(self,documents,verbose=False): return self.encode(documents)
    
    passages=[SimpleNamespace(id=str(i),text=text) for i,text in enumerate([
        'clinic healthcare medication treatment patient '+str(i) for i in range(6)
    ]+['satellite rocket orbit launch spacecraft '+str(i) for i in range(6)])]
    with TemporaryDirectory() as temporary, patch('sentence_transformers.SentenceTransformer',LocalTestEncoder):
        for method in ['guided','zero_shot']:
            settings=AnalysisSettings(analysis_method=method,min_topic_size=2,min_samples=1,n_neighbors=3,
                topic_definitions=(('Healthcare access','clinic, healthcare, treatment' if method=='guided' else 'healthcare clinic treatment'),))
            output=fit_bertopic(passages,settings,Path(temporary),print,None)
            print('RESULT',method,output.assignments, output.model_info.get('predefined_topic_labels'),flush=True)
            assert len(output.assignments)==12
            if method=='zero_shot':
                assert output.assignments[:6]==[0]*6
                assert all(t!=0 for t in output.assignments[6:])
        # Actual BERTopic keyword backend on a single passage, matched and unmatched.
        for text in ['clinic healthcare medication treatment', 'satellite rocket orbit launch']:
            one=[SimpleNamespace(id='single',text=text)]
            output=fit_bertopic(one,settings,Path(temporary),print,None)
            assert len(output.assignments)==1
            print('SINGLE',output.assignments,flush=True)
