"""Interviewer-turn detection and the passage preview inside Analysis Setup."""
from PySide6.QtCore import QEventLoop, Qt, QTimer

from qualitative_themes.analysis.segment import SegmentRange, model_text
from qualitative_themes.analysis.speakers import detect_speakers, interviewer_ranges, speaker_turns, without_ranges
from qualitative_themes.analysis.topics import prepare_passages
from qualitative_themes.storage.database import create_project
from qualitative_themes.storage.documents import import_document

TRANSCRIPT = """Interviewer: Can you tell me about getting to the clinic?
P1: The bus takes forty minutes and the route changed twice this year.
It made us miss two appointments.

Interviewer: What happened after that?
[00:12:31] P1: We had to redo the intake paperwork and the insurance card again.
"""


def test_turns_run_until_the_next_label_and_roles_are_recognized():
    turns = speaker_turns(TRANSCRIPT)
    assert [t.label for t in turns] == ["Interviewer", "P1", "Interviewer", "P1"]
    assert "two appointments" in TRANSCRIPT[turns[1].start:turns[1].end]


def test_numbered_and_named_labels_are_detected_and_suggested():
    text = ("Interviewer1: How do you get to the clinic?\nP3: By bus, usually.\n"
            "Interviewer1: And when it's late?\nP3: Then we miss it.\nNote: audio cut out here.\n")
    rows = {row["key"]: row for row in detect_speakers([text, text.replace("P3", "Maria")])}
    assert set(rows) == {"interviewer1", "p3", "maria"}
    assert rows["interviewer1"]["suggested"] and rows["interviewer1"]["documents"] == 2
    assert not rows["p3"]["suggested"] and not rows["maria"]["suggested"]
    ranges = interviewer_ranges(text)
    assert [text[a:b].split(":")[0] for a, b in ranges] == ["Interviewer1", "Interviewer1"]
    assert interviewer_ranges(text, ["p3"]) != ranges


def test_otter_style_and_timestamp_labels():
    otter = "Speaker 1  00:00:03\nHow did you get here?\n\nSpeaker 2  00:00:07\nBy bus.\n\n" \
            "Speaker 1  00:00:12\nHow long?\n\nSpeaker 2  00:00:15\nAn hour.\n"
    assert [t.key for t in speaker_turns(otter)] == ["speaker 1", "speaker 2"] * 2
    stamped = "[00:01:02] Interviewer: Tell me.\n[00:01:09] R1: Okay.\n[00:02:00] Interviewer: More?\n[00:02:05] R1: Sure.\n"
    assert [t.key for t in speaker_turns(stamped)] == ["interviewer", "r1"] * 2


def test_prose_with_colons_is_not_mistaken_for_speakers():
    prose = "The thing is: we never knew.\nNote: see appendix.\nIt was hard: really hard.\n"
    assert speaker_turns(prose) == []


def test_model_text_drops_interviewer_turns_but_keeps_offsets():
    ranges = interviewer_ranges(TRANSCRIPT)
    span = SegmentRange(0, len(TRANSCRIPT))
    text = model_text(TRANSCRIPT, span, ranges)
    assert "tell me about" not in text and "What happened" not in text
    assert "bus takes forty minutes" in text and "insurance card" in text
    assert without_ranges("abcdef", 0, 6, [(1, 3)]) == "a def"


def test_prepare_passages_excludes_interviewer_text_only_when_asked(tmp_path):
    project = create_project(tmp_path / "p", "P")
    source = tmp_path / "t.txt"
    source.write_text(TRANSCRIPT)
    doc = import_document(project.directory, source).document_id
    with_prompts = prepare_passages(project.directory, "Paragraphs", [doc])
    without = prepare_passages(project.directory, "Paragraphs", [doc], exclude_interviewer=True)
    assert any("tell me about" in p.text for p in with_prompts)
    assert not any("tell me about" in p.text or "What happened" in p.text for p in without)
    assert [(p.start, p.end) for p in without] == [(p.start, p.end) for p in with_prompts]


def _wait_for_inspection(dialog):
    loop, timer = QEventLoop(), QTimer()
    timer.setSingleShot(True)
    timer.timeout.connect(loop.quit)
    for _ in range(4):
        if dialog._inspection is None:
            break
        dialog._inspection.finished.connect(loop.quit)
        timer.start(5000)
        loop.exec()
    assert dialog._inspection is None


def test_setup_dialog_previews_passages_and_offers_interviewer_option(tmp_path):
    from qualitative_themes.gui.analysis_setup import AnalysisSetupDialog
    project = create_project(tmp_path / "p", "P")
    source = tmp_path / "t.txt"
    source.write_text(TRANSCRIPT)
    doc = import_document(project.directory, source).document_id
    dialog = AnalysisSetupDialog(project.directory, [(doc, "t.txt")], unit="Paragraphs")
    _wait_for_inspection(dialog)
    assert dialog.interviewer_turns == 2 and dialog.exclude_interviewer.isChecked()
    assert dialog.chosen_labels() == ["interviewer"]
    assert dialog.settings().exclude_interviewer is True
    assert dialog.settings().interviewer_labels == ("interviewer",)
    assert dialog.summary["count"] == 2
    assert dialog.preview.toPlainText().startswith("Interviewer:")
    assert dialog.preview_position.text().startswith("Passage 1 of 2")
    dialog.step_preview(1)
    assert dialog.preview_position.text().startswith("Passage 2 of 2")
    dialog.unit.setCurrentText("Whole document")
    _wait_for_inspection(dialog)
    assert dialog.summary["count"] == 1
    checked = [b.text() for b in dialog.unit_group.buttons() if b.isChecked()]
    assert checked == ["Whole document"]
    dialog.speaker_list.item(0).setCheckState(Qt.CheckState.Unchecked)
    assert dialog.settings().exclude_interviewer is False
    dialog.reject()
