"""More options in Find themes / Adjust themes, small-collection grouping, leftovers and the export menu."""
from dataclasses import replace

import numpy as np
import pytest
from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QDialogButtonBox

from qualitative_themes.analysis.settings import AnalysisSettings, effective_parameters
from qualitative_themes.analysis.topics import TopicOutput, analyze
from qualitative_themes.storage.database import create_project
from qualitative_themes.storage.documents import import_document


@pytest.fixture
def three_documents(tmp_path, qt_test_application):
    project = create_project(tmp_path / "p", "More options")
    docs = []
    for name, text in [("one", "The bus was late again."), ("two", "Our garden needs watering."),
                       ("three", "The clinic letter was confusing.")]:
        source = tmp_path / f"{name}.txt"
        source.write_text(text, encoding="utf-8")
        docs.append(import_document(project.directory, source).document_id)
    return project.directory, docs


def open_setup(directory, docs, **kwargs):
    from qualitative_themes.gui.analysis_setup import AnalysisSetupDialog
    dialog = AnalysisSetupDialog(directory, [(doc, f"Document {i}") for i, doc in enumerate(docs)], **kwargs)
    loop = QEventLoop()
    poll = QTimer()
    poll.timeout.connect(lambda: loop.quit() if dialog._inspection is None else None)
    poll.start(10)
    QTimer.singleShot(5000, loop.quit)
    loop.exec()
    poll.stop()
    return dialog


def test_three_whole_documents_can_be_analysed(three_documents):
    directory, docs = three_documents
    dialog = open_setup(directory, docs, unit="Whole document")
    try:
        assert dialog.summary["count"] == 3
        assert dialog.buttons.button(QDialogButtonBox.StandardButton.Ok).isEnabled()
        assert "small-collection grouping" in dialog.validation.text()
        dialog.minimum.setValue(1)
        assert dialog.settings().min_topic_size == 1
    finally:
        dialog.reject()


def test_more_options_reach_the_settings_and_reset(three_documents):
    directory, docs = three_documents
    dialog = open_setup(directory, docs, unit="Whole document")
    try:
        dialog.discovery.unsorted.setCurrentText("More")
        dialog.discovery.fill.setChecked(True)
        dialog.discovery.merge.setChecked(True)
        dialog.discovery.seed.setValue(7)
        dialog.keywords.style.setCurrentIndex(dialog.keywords.style.findData("meaning"))
        dialog.keywords.play_down.setChecked(False)
        dialog.keywords.count.setValue(15)
        dialog.cap.setValue(40)
        settings = dialog.settings()
        assert (settings.fill_leftovers, settings.auto_merge, settings.seed, settings.keyword_style,
                settings.play_down_common_words, settings.top_n_words, settings.max_themes) == \
            (True, True, 7, "meaning", False, 15, 40)
        assert settings.min_samples == 2 + 5  # "More" for Balanced
        dialog.restore_defaults()
        defaults = dialog.settings()
        for name in ("fill_leftovers", "auto_merge", "seed", "keyword_style", "play_down_common_words",
                     "top_n_words", "max_themes", "min_samples", "min_topic_size"):
            assert getattr(defaults, name) == getattr(AnalysisSettings(), name), name
    finally:
        dialog.reject()


def test_saved_settings_come_back_in_the_dialog(three_documents):
    directory, docs = three_documents
    saved = replace(AnalysisSettings(), keyword_style="counts", top_n_words=6, fill_leftovers=True, seed=99,
                    max_themes=30, min_samples=1)
    dialog = open_setup(directory, docs, settings=saved, unit="Whole document")
    try:
        assert dialog.discovery.unsorted.currentText() == "Fewer"
        again = dialog.settings()
        assert (again.keyword_style, again.top_n_words, again.fill_leftovers, again.seed, again.max_themes) == \
            ("counts", 6, True, 99, 30)
    finally:
        dialog.reject()


def test_adjust_themes_update_keywords_uses_the_keyword_settings(tmp_path, qt_test_application):
    from qualitative_themes.analysis.refinement import load_source, propose_refinement
    from qualitative_themes.gui.refinement import RefinementSetupDialog
    directory = create_project(tmp_path / "p", "Words").directory
    source = tmp_path / "text.txt"
    source.write_text("\n\n".join(f"Paragraph {i} about buses and clinics." for i in range(6)), encoding="utf-8")
    doc = import_document(directory, source).document_id
    run, _ = analyze(directory, "Paragraphs", [doc], AnalysisSettings(min_topic_size=2),
                     backend=lambda passages, *a: TopicOutput([0, 0, 0, 1, 1, 1], {0: [("bus", .5)], 1: [("clinic", .5)]}))
    dialog = RefinementSetupDialog(load_source(directory, run), operation="words")
    dialog.keywords.style.setCurrentIndex(dialog.keywords.style.findData("counts"))
    dialog.keywords.count.setValue(12)
    settings = dialog.settings()
    assert (settings.keyword_style, settings.top_n_words) == ("counts", 12)
    seen = []

    def words(passages, assignments, chosen, *args):
        seen.append(chosen)
        return TopicOutput(assignments, {t: [("word", .1)] for t in set(assignments)})
    propose_refinement(directory, run, "words", settings, representation_backend=words)
    assert (seen[0].keyword_style, seen[0].top_n_words) == ("counts", 12)
    with pytest.raises(ValueError, match="fresh discovery"):
        propose_refinement(directory, run, "words", replace(settings, fill_leftovers=True), representation_backend=words)


def test_small_collection_grouping_clusters_by_meaning():
    pytest.importorskip("bertopic")
    from qualitative_themes.analysis.topics import _models
    vectors = np.array([[1, 0, 0], [0.95, 0.05, 0], [0, 1, 0], [0.05, 0.95, 0], [0, 0, 1]], dtype=float)
    params = effective_parameters(AnalysisSettings(), len(vectors))
    reducer, clusterer, cut = _models(params, vectors)
    assert 0.05 <= cut <= 0.55
    labels = clusterer.fit(reducer.fit(vectors).transform(vectors)).labels_
    assert labels[0] == labels[1] and labels[2] == labels[3] and len({labels[0], labels[2], labels[4]}) == 3
    aimed = effective_parameters(replace(AnalysisSettings(), requested_theme_count=2), len(vectors))
    _reducer, clusterer, cut = _models(aimed, vectors)
    assert cut is None and len(set(clusterer.fit(vectors).labels_)) == 2


def test_leftovers_move_only_when_asked():
    from qualitative_themes.analysis.topics import fill_leftovers

    class Model:
        def reduce_outliers(self, texts, topics, strategy, embeddings, threshold):
            assert (strategy, threshold) == ("embeddings", 0.5)
            return [0 if t == -1 else t for t in topics]
    topics = [-1, 0, 1, -1]
    off = AnalysisSettings()
    assert fill_leftovers(Model(), [], topics, None, off, print) == (topics, 0)
    on = replace(off, fill_leftovers=True)
    assert fill_leftovers(Model(), [], topics, None, on, print) == ([0, 0, 1, 0], 2)
    assert fill_leftovers(Model(), [], [-1, -1], None, on, print) == ([-1, -1], 0)  # nothing to fill into


def test_export_menu_has_excel_and_technical_columns(qt_test_application):
    from qualitative_themes.gui import strings
    from qualitative_themes.gui.main_window import MainWindow
    window = MainWindow()
    labels = [action.text() for action in window.export_menu.actions()]
    assert strings.EXPORT_EVERYTHING in labels
    assert window.technical_columns_action.isCheckable() and not window.technical_columns_action.isChecked()
    window.close()
