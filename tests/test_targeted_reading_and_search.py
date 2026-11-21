"""Targeted reading (overview, fit strength, guided reading, coverage) and cross-document search."""
import hashlib
import json

import numpy as np
import pytest

from qualitative_themes.analysis.fit import fit_strength
from qualitative_themes.analysis.overview import UNGROUPED_TITLE, build_overview, reading_queue
from qualitative_themes.analysis.topics import TopicOutput, analyze
from qualitative_themes.storage import comments as comment_store
from qualitative_themes.storage import reading
from qualitative_themes.storage.database import create_project
from qualitative_themes.storage.documents import import_document
from qualitative_themes.storage.search import search

IDENTITY = {"model": "test-encoder", "revision": "1"}
CLINIC = ["The clinic bus route changed and we missed appointments.",
          "Getting to the clinic takes two buses and a long walk.",
          "The clinic moved farther from our neighborhood this year.",
          "We missed the clinic because the bus never came.",
          "Insurance paperwork had to be redone at the clinic."]
GARDEN = ["Tomatoes grew well in the community garden this summer.",
          "The garden plots need more water in July.",
          "Neighbors share seeds from the garden every spring."]


def vector_for(text):
    v = np.array([1.0 if "clinic" in text else 0.0, 1.0 if "garden" in text else 0.0, 0.0])
    v[2] = (int(hashlib.sha256(text.encode()).hexdigest()[:4], 16) % 100) / 400
    return v / np.linalg.norm(v)


@pytest.fixture
def analysed(tmp_path):
    project = create_project(tmp_path / "p", "P")
    docs = []
    for name, lines in [("clinic.txt", CLINIC), ("garden.txt", GARDEN + ["A one-off remark about weather."])]:
        source = tmp_path / name
        source.write_text("\n\n".join(lines))
        docs.append(import_document(project.directory, source).document_id)

    def backend(passages, settings, directory, progress, should_cancel):
        cache = directory / "models" / "embeddings" / hashlib.sha256(json.dumps(IDENTITY, sort_keys=True).encode()).hexdigest()
        cache.mkdir(parents=True, exist_ok=True)
        for p in passages:
            np.save(cache / (hashlib.sha256(p.text.encode()).hexdigest() + ".npy"), vector_for(p.text))
        topics = [0 if "clinic" in p.text else (1 if "garden" in p.text else -1) for p in passages]
        return TopicOutput(topics, {0: [("clinic", .5), ("bus", .3)], 1: [("garden", .5)], -1: []},
                           model_info={"embedding_identity": IDENTITY})

    run, _ = analyze(project.directory, "Paragraphs", docs, backend=backend)
    return project.directory, docs, run


def test_fit_strength_labels_core_and_borderline_and_is_cached(analysed):
    directory, docs, run = analysed
    fits = fit_strength(directory, run)
    clinic = [v for (doc, _s, _e), v in fits.items() if doc == docs[0]]
    assert len(clinic) == 5
    assert {v["label"] for v in clinic} >= {"core", "borderline"}
    assert all(-1.0 <= v["similarity"] <= 1.0 for v in fits.values())
    assert fit_strength(directory, run) == fits  # served from the analysis record the second time


def test_overview_cards_heatmap_and_ungrouped_title(analysed):
    directory, docs, run = analysed
    data = build_overview(directory, run)
    labels = [t["label"] for t in data["themes"]]
    assert labels[-1] == UNGROUPED_TITLE
    clinic = next(t for t in data["themes"] if t["passages"] == 5)
    assert clinic["documents"] == 1 and clinic["keywords"][:2] == ["clinic", "bus"]
    assert len(clinic["quotes"]) == 2
    assert data["matrix"][(clinic["theme_id"], docs[0])] == 5
    assert data["coverage"]["reviewed"] == 0 and data["has_fit"]


def test_guided_queue_is_core_first_and_marks_count_toward_coverage(analysed):
    directory, docs, run = analysed
    clinic = next(t for t in build_overview(directory, run)["themes"] if t["passages"] == 5)
    queue = reading_queue(directory, run, [clinic["theme_id"]])
    sims = [item["fit"]["similarity"] for item in queue]
    assert queue[0]["fit"]["label"] == "core" and sims[0] == max(sims)
    first = queue[0]
    reading.mark(directory, first["document_id"], first["start"], first["end"], "relevant", clinic["theme_id"], run)
    reading.mark(directory, first["document_id"], first["start"], first["end"], "read", clinic["theme_id"], run)
    assert reading.marks(directory)[(first["document_id"], first["start"], first["end"])] == "relevant"
    cover = reading.coverage(directory, run)
    assert cover["reviewed"] == 1 and cover["documents"][docs[1]]["share"] == 0


def test_search_finds_word_beginnings_with_offsets_themes_and_comments(analysed):
    directory, docs, run = analysed
    hits = search(directory, "insur", run_id=run)
    assert len(hits) == 1 and hits[0].match.lower() == "insur" and hits[0].document_id == docs[0]
    assert any("clinic" in t.lower() for t in hits[0].themes)
    assert search(directory, "linic") == []  # mid-word fragments don't match
    assert len(search(directory, "garden")) == 3
    assert len(search(directory, "clinic", document_ids={docs[1]})) == 0
    comment_store.add_comment(directory, comment_store.Target.passage(docs[1], 0, 8), "Compare with the clinic transport theme")
    with_comments = search(directory, "transport", include_comments=True)
    assert [h.kind for h in with_comments] == ["comment"]


def test_reading_guide_exports_a_word_document(analysed, tmp_path):
    from docx import Document
    from qualitative_themes.export.reading_guide import export_reading_guide
    directory, docs, run = analysed
    destination = tmp_path / "guide.docx"
    count = export_reading_guide(directory, run, destination)
    text = "\n".join(p.text for p in Document(destination).paragraphs)
    assert count == 3 and "Reading guide" in text and "Where to read" in text and UNGROUPED_TITLE in text


def test_overview_tab_and_dialogs_render(analysed, qt_test_application):
    from qualitative_themes.gui.main_window import MainWindow
    from qualitative_themes.storage.database import open_project
    directory, docs, run = analysed
    window = MainWindow()
    window.show()
    window._show_project(open_project(directory))
    window.tabs.setCurrentWidget(window.overview)
    qt_test_application.processEvents()
    assert window.overview.data and window.overview.heatmap.rowCount() == 2
    assert window.overview.cards.count() == 3
    clinic = next(t for t in window.overview.data["themes"] if t["passages"] == 5)
    window.open_guided_reading(clinic["theme_id"])
    dialog = window._reading
    assert dialog.where.text().startswith("1 of 5")
    dialog.judge("relevant")
    assert dialog.progress_label.text() == "1 of 5 reviewed"
    dialog.open_document()
    assert window.tabs.currentWidget() is window.workspace
    window.open_search()
    window._search.field.setText("garden")
    window._search.run()
    assert "3 matches in 1 document" in window._search.summary.text()
    window._search.open_current()
    assert window.workspace.doc_id == docs[1]
    assert window.workspace.viewer.textCursor().selectedText().lower() == "garden"


def test_large_maps_are_sampled_evenly():
    from qualitative_themes.analysis.visualizations import map_sample
    assert map_sample(10) == list(range(10))
    picked = map_sample(12000, 6000)
    assert len(picked) == 6000 and picked == sorted(set(picked)) and picked[0] == 0 and picked[-1] >= 11990


def test_search_comments_respect_theme_and_document_filters(analysed):
    directory, docs, run = analysed
    clinic = next(t for t in build_overview(directory, run)["themes"] if t["passages"] == 5)
    comment_store.add_comment(directory, comment_store.Target.passage(docs[0], 0, 8), "Compare transport")
    comment_store.add_comment(directory, comment_store.Target.passage(docs[1], 0, 8), "Compare transport")
    hits = search(directory, "transport", include_comments=True, run_id=run,
                  theme_ids={clinic["theme_id"]})
    assert len(hits) == 1 and hits[0].document_id == docs[0]
    assert hits[0].themes == [clinic["label"]]
    assert search(directory, "transport", include_comments=True, run_id=run,
                  theme_ids={clinic["theme_id"]}, document_ids={docs[1]}) == []
    assert search(directory, "clinic", limit=0) == []
    assert len(search(directory, "transport", include_comments=True, limit=1)) == 1


def test_search_fetches_only_candidate_text_and_falls_back_without_fts(analysed, monkeypatch):
    import importlib
    from contextlib import contextmanager
    store = importlib.import_module("qualitative_themes.storage.search")
    directory, docs, run = analysed
    statements = []
    original = store.connect

    @contextmanager
    def traced_connect(directory):
        with original(directory) as conn:
            conn.set_trace_callback(statements.append)
            yield conn

    monkeypatch.setattr(store, "connect", traced_connect)
    hits = search(directory, "garden", run_id=run)
    reads = [sql for sql in statements if "SELECT canonical_text FROM documents" in sql]
    assert len(reads) == 1 and docs[1] in reads[0]
    assert all(docs[0] not in sql for sql in reads)
    monkeypatch.setattr(store, "_fts_candidates", lambda *_: None)
    assert search(directory, "garden", run_id=run) == hits
    assert search(directory, "GARDEN", case_sensitive=True) == []


def test_search_enter_uses_latest_query_and_cancels_duplicate_search(analysed):
    from qualitative_themes.gui.search_dialog import SearchDialog
    directory, docs, run = analysed
    dialog = SearchDialog(directory, run)
    opened = []
    dialog.open_result.connect(lambda *args: opened.append(args))
    dialog.field.setText("clinic")
    dialog.run()
    assert not dialog._debounce.isActive()
    dialog.field.setText("garden")
    dialog.open_current()
    assert len(opened) == 1 and opened[0][0] == docs[1]
    assert not dialog._debounce.isActive()
    dialog.field.clear()
    dialog.open_current()
    assert len(opened) == 1 and not dialog.hits


def test_search_dialog_reports_truncation_and_groups_comments(analysed, monkeypatch):
    from qualitative_themes.gui import search_dialog
    directory, docs, run = analysed
    comment_store.add_comment(directory, comment_store.Target.passage(docs[0], 0, 8), "The clinic and garden")
    dialog = search_dialog.SearchDialog(directory, run)
    dialog.include_comments.setChecked(True)
    dialog.field.setText("The")
    dialog.run()
    from PySide6.QtCore import Qt
    headers = [dialog.results.item(i).text() for i in range(dialog.results.count())
               if dialog.results.item(i).data(Qt.ItemDataRole.UserRole) is None]
    assert len(headers) == len(set(headers)) == 2
    monkeypatch.setattr(search_dialog, "RESULT_LIMIT", 2)
    dialog.field.setText("clinic")
    dialog.run()
    assert len(dialog.hits) == 2
    assert "Showing first 2 matches" in dialog.summary.text()
