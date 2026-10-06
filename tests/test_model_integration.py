"""Opt-in real-model smoke check; never downloads weights during a test."""
import os
from pathlib import Path
import shutil
from dataclasses import replace

import pytest

from qualitative_themes.analysis.topics import analyze
from qualitative_themes.storage.coding import list_themes, occurrences
from qualitative_themes.storage.database import create_project
from qualitative_themes.storage.documents import import_document


@pytest.mark.integration
@pytest.mark.skipif(not os.environ.get("DOC_TOPIC_MODEL_WEIGHTS"),
                    reason="Opt-in: set DOC_TOPIC_MODEL_WEIGHTS to a previously downloaded models/weights directory.")
def test_real_bertopic_local_corpus(tmp_path):
    project = create_project(tmp_path / "project", "Model smoke")
    cached = Path(os.environ["DOC_TOPIC_MODEL_WEIGHTS"])
    assert cached.is_dir(), "DOC_TOPIC_MODEL_WEIGHTS must point to a local models/weights folder."
    shutil.copytree(cached, project.directory / "models" / "weights")
    corpus = Path(__file__).resolve().parents[1] / "examples" / "smoke_corpus"
    docs = [import_document(project.directory, path).document_id for path in sorted(corpus.glob("*.txt"))]
    run, count = analyze(project.directory, "Paragraphs", docs, progress=print)
    themes = list_themes(project.directory, run)
    assert count == 45
    assert sum(number for _, _, number in themes) == count
    assert all(occurrences(project.directory, theme, run) for theme, _, _ in themes)
    # Restore saved groups through BERTopic's manual path, without reclustering.
    from qualitative_themes.analysis.refinement import (
        apply_proposal, load_source, propose_refinement, review_proposal,
    )
    proposal, _ = propose_refinement(project.directory, run, "words", progress=print)
    review = review_proposal(project.directory, proposal)
    assert review["changed_memberships"] == 0
    assert review["old_uncategorized"] == review["new_uncategorized"]
    apply_proposal(project.directory, proposal, {item["theme_id"]: item["recommended"] for item in review["themes"]})
    if review["new_theme_count"] > 1:
        settings = replace(load_source(project.directory, proposal)["settings"],
                           requested_theme_count=review["new_theme_count"] - 1)
        reduced, _ = propose_refinement(project.directory, proposal, "reduce", settings, progress=print)
        reduction = review_proposal(project.directory, reduced)
        assert reduction["new_theme_count"] < reduction["old_theme_count"]
        assert reduction["old_uncategorized"] == reduction["new_uncategorized"]
        apply_proposal(project.directory, reduced, {item["theme_id"]: item["recommended"] for item in reduction["themes"]})
    # Exercise the genuine embedding/UMAP/HDBSCAN path on five passages.
    from qualitative_themes.analysis.settings import AnalysisSettings
    small_source = tmp_path / "small-five.txt"
    small_source.write_text("\n\n".join([
        "Students describe timetable changes and useful class reminders on their phones.",
        "Calendar users discuss scheduling classes, reminders, and upcoming assignments.",
        "Customers describe failed uploads, repeated crashes, and lost photographs.",
        "Photo uploads stall and the application closes unexpectedly during editing.",
        "One reviewer discusses subscription prices and cancellation support.",
    ]), encoding="utf-8")
    small_doc = import_document(project.directory, small_source).document_id
    small_run, small_count = analyze(project.directory, "Paragraphs", [small_doc],
                                    AnalysisSettings(min_topic_size=2), progress=print)
    assert small_count == 5
    assert sum(count for _, _, count in list_themes(project.directory, small_run)) == 5


@pytest.mark.integration
def test_real_bertopic_visualization_without_embedding_weights(tmp_path):
    """Real built-in plot APIs with supplied vectors; no model download."""
    import numpy as np
    pytest.importorskip('bertopic')
    from qualitative_themes.analysis.visualizations import build_visualizations
    from qualitative_themes.analysis.topics import TopicOutput, AnalysisSettings
    project = create_project(tmp_path/'visuals', 'Built-in plots')
    path = tmp_path/'topics.txt'
    path.write_text('\n\n'.join([
        'Space rockets launch satellites into orbit.', 'Astronauts explore planets and lunar missions.',
        'Computers need memory storage and processor upgrades.', 'Hardware repairs replace broken graphics cards.',
        'Gardens grow flowers in soil with watering.', 'Plants need sunlight and careful garden maintenance.',
    ]), encoding='utf-8')
    doc = import_document(project.directory,path).document_id
    def fake(passages,*args): return TopicOutput([0,0,1,1,2,2], {0:[('space',.5)],1:[('hardware',.5)],2:[('garden',.5)]})
    run,_ = analyze(project.directory,'Paragraphs',[doc],AnalysisSettings(min_topic_size=2),backend=fake)
    def embeddings(directory,texts,*args): return np.array([[i,i*i,1] for i in range(len(texts))],dtype=float)
    result = build_visualizations(project.directory,run,embedding_provider=embeddings)
    assert set(result['html']) == {'map','heatmap','hierarchy'}, result['messages']
    assert 'Topic:' in result['tree']
    assert all('Plotly.newPlot' in content for content in result['html'].values())
