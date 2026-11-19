"""Find speaker turns in interview transcripts so interviewer prompts can be left out.

Interviewer questions often form their own "theme" (a cluster of prompts like
"Can you tell me more about…") that sends readers to the wrong places. When the
researcher chooses interviewer labels, model input drops those turns; the source
text, offsets and the viewer are unchanged.

Any short label at the start of a line counts as a speaker label when it is
used at least twice in a document, in these common transcript layouts:

    Interviewer: How did you get there?        (label + colon; any name or code)
    00:01:22 Maria: We took the bus.           (timestamp first)
    [00:01:22] P1: …   /   (00:01:22) R: …
    Interviewer (00:01:22): …                  (timestamp after the label)
    [Interviewer] How did you…                 (label in brackets)
    Speaker 1  00:01:22                        (Otter/Zoom: label + time on its own line,
    We took the bus.                            the turn's text on the next lines)

A turn runs from its label to the next label. Known interviewer labels
(Interviewer, Int, I, Q, Moderator, Facilitator, Researcher, …) are suggested;
the researcher confirms the choice in Analysis Setup.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

KNOWN_INTERVIEWER = ("interviewer", "int", "i", "q", "question", "moderator", "facilitator", "researcher",
                     "interviewer 1", "interviewer 2", "int1", "int2", "iv", "ivr")
NOT_SPEAKERS = {"note", "notes", "date", "time", "location", "place", "title", "subject", "re", "http", "https",
                "file", "duration", "transcript", "transcribed by", "audio", "participants", "present", "page",
                "tags", "summary", "topic", "source", "attendees", "recording", "ps", "nb", "example", "e.g", "i.e"}
_TIME = r"(?:\d{1,2}:\d{2}(?::\d{2})?(?:[.,]\d{1,3})?)"
_LABEL = r"[A-Za-z][\w'’.\- ]{0,30}?"
_PATTERNS = [
    # [time] / (time) / time, then Label:
    re.compile(r"(?m)^[ \t]*(?:[\[(]?" + _TIME + r"[\])]?[ \t]*[-–]?[ \t]*)?(?P<label>" + _LABEL + r")[ \t]*"
               r"(?:[\[(]" + _TIME + r"[\])][ \t]*)?:(?=[ \t]|$)"),
    # [Label] text
    re.compile(r"(?m)^[ \t]*\[(?P<label>" + _LABEL + r")\][ \t]+(?=\S)"),
    # Label  00:01:22  (alone on its line; Otter/Zoom/Teams exports)
    re.compile(r"(?m)^[ \t]*(?P<label>" + _LABEL + r")[ \t]+[\[(]?" + _TIME + r"[\])]?[ \t]*$"),
]


def normalize(label: str) -> str:
    return " ".join(label.replace("’", "'").strip(" .-").split()).casefold()


@dataclass(frozen=True)
class Turn:
    start: int
    end: int
    label: str  # as written
    key: str    # normalized


def _label_matches(text: str):
    found = []
    for pattern in _PATTERNS:
        for match in pattern.finditer(text):
            label = match.group("label")
            key = normalize(label)
            words = key.split()
            if not key or key in NOT_SPEAKERS or len(words) > 4 or key.startswith(("http", "www")):
                continue
            # Sentences that happen to contain a colon ("The thing is: …") are not labels.
            if len(words) > 1 and not any(w[:1].isupper() or w[:1].isdigit() for w in label.split()):
                continue
            found.append((match.start(), label, key))
    found.sort()
    unique, last = [], -1
    for item in found:
        if item[0] != last:
            unique.append(item)
            last = item[0]
    return unique


def speaker_turns(text: str) -> list[Turn]:
    """Turns for labels used at least twice in this text (one-off "Note:" lines are ignored)."""
    matches = _label_matches(text)
    counts = Counter(key for _, _, key in matches)
    matches = [m for m in matches if counts[m[2]] >= 2]
    turns = []
    for index, (start, label, key) in enumerate(matches):
        end = matches[index + 1][0] if index + 1 < len(matches) else len(text)
        turns.append(Turn(start, end, label.strip(), key))
    return turns


def reliable_transcript_turns(text: str) -> list[Turn]:
    """Conservative boundaries: at least two recurring labels, no one-off candidates.

    Unconfirmed labels must not be swallowed into a different speaker's turn.
    Other modes retain the original detector and exclusion behavior.
    """
    matches = _label_matches(text)
    counts = Counter(key for _, _, key in matches)
    if len(counts) < 2 or any(count < 2 for count in counts.values()):
        return []
    return [Turn(start, matches[i + 1][0] if i + 1 < len(matches) else len(text), label.strip(), key)
            for i, (start, label, key) in enumerate(matches)]


def detect_speakers(texts) -> list[dict]:
    """Speaker labels across documents: ``[{"key", "label", "documents", "turns", "suggested"}]``."""
    turns_by_key: Counter = Counter()
    docs_by_key: Counter = Counter()
    shown: dict[str, str] = {}
    for text in texts:
        keys = set()
        for turn in speaker_turns(text):
            turns_by_key[turn.key] += 1
            keys.add(turn.key)
            shown.setdefault(turn.key, turn.label)
        docs_by_key.update(keys)
    rows = [{"key": key, "label": shown[key], "documents": docs_by_key[key], "turns": turns_by_key[key],
             "suggested": is_known_interviewer(key)} for key in turns_by_key]
    rows.sort(key=lambda r: (not r["suggested"], -r["documents"], -r["turns"], r["key"]))
    return rows


def is_known_interviewer(key: str) -> bool:
    key = normalize(key)
    return key in KNOWN_INTERVIEWER or bool(re.fullmatch(r"(interviewer|int|moderator|facilitator|researcher)[ _-]?\d*", key))


def interviewer_ranges(text: str, labels=None) -> list[tuple[int, int]]:
    """Source ranges of turns whose label is one of ``labels`` (normalized).

    ``labels=None`` uses the known interviewer labels.
    """
    chosen = {normalize(label) for label in labels} if labels is not None else None
    return [(t.start, t.end) for t in speaker_turns(text)
            if (t.key in chosen if chosen is not None else is_known_interviewer(t.key))]


def without_ranges(text: str, start: int, end: int, ranges) -> str:
    """The passage's text with the given source ranges cut out."""
    pieces, cursor = [], start
    for a, b in ranges:
        if b <= cursor or a >= end:
            continue
        if a > cursor:
            pieces.append(text[cursor:a])
        cursor = max(cursor, b)
    if cursor < end:
        pieces.append(text[cursor:end])
    return " ".join(pieces)


def count_interviewer_turns(texts, labels=None) -> int:
    return sum(len(interviewer_ranges(text, labels)) for text in texts)


def labels_for(settings) -> list[str] | None:
    """Interviewer labels for an analysis: the researcher's choice, or the known defaults."""
    if not getattr(settings, "exclude_interviewer", False):
        return None
    chosen = tuple(getattr(settings, "interviewer_labels", ()) or ())
    return list(chosen) if chosen else list(KNOWN_INTERVIEWER)
