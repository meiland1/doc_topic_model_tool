"""Paragraph-aware segmentation with exact offsets in immutable source text."""
import re
from dataclasses import dataclass

UNITS = {
    "Short passages": (100, 125),
    "Standard passages": (200, 250),
    "Long passages": (375, 450),
    "Paragraphs": None,
    "Whole document": None,
}

_WORDS = re.compile(r"\S+")
_SENTENCE_BREAK = re.compile(r"(?<=[.!?])\s+")
_PARAGRAPH_BREAK = re.compile(r"(?:\r?\n)[ \t]*(?:\r?\n)+|\f+")


@dataclass(frozen=True)
class SegmentRange:
    start_char: int
    end_char: int


def _trim(text: str, start: int, end: int) -> SegmentRange | None:
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    return SegmentRange(start, end) if start < end else None


def _paragraphs(text: str) -> list[SegmentRange]:
    boundaries = [0]
    for match in _PARAGRAPH_BREAK.finditer(text):
        boundaries.extend((match.start(), match.end()))
    boundaries.append(len(text))
    result: list[SegmentRange] = []
    for a, b in zip(boundaries, boundaries[1:]):
        span = _trim(text, a, b)
        if span:
            result.append(span)
    return result


def _sentences(text: str, paragraph: SegmentRange) -> list[SegmentRange]:
    starts = [paragraph.start_char]
    ends = []
    for match in _SENTENCE_BREAK.finditer(text, paragraph.start_char, paragraph.end_char):
        ends.append(match.start())
        starts.append(match.end())
    ends.append(paragraph.end_char)
    return [span for a, b in zip(starts, ends) if (span := _trim(text, a, b))]


def _split_oversized(text: str, span: SegmentRange, target: int, maximum: int) -> list[SegmentRange]:
    words = list(_WORDS.finditer(text, span.start_char, span.end_char))
    if len(words) <= maximum:
        return [span]
    return [SegmentRange(words[i].start(), words[min(i + target, len(words)) - 1].end())
            for i in range(0, len(words), target)]


def segment_text(text: str, unit: str = "Standard passages") -> list[SegmentRange]:
    """Return ordered nonoverlapping [start,end) spans; gaps contain whitespace only."""
    if unit not in UNITS:
        raise ValueError(f"Unknown analysis unit: {unit}")
    if not text.strip():
        return []
    if unit == "Whole document":
        return [_trim(text, 0, len(text))]
    paragraphs = _paragraphs(text)
    if unit == "Paragraphs":
        return paragraphs
    target, maximum = UNITS[unit]
    atoms = [atom for paragraph in paragraphs
             for sentence in _sentences(text, paragraph)
             for atom in _split_oversized(text, sentence, target, maximum)]
    result: list[SegmentRange] = []
    current: SegmentRange | None = None
    count = 0
    for atom in atoms:
        words = len(_WORDS.findall(text, atom.start_char, atom.end_char))
        if current and count + words > maximum:
            result.append(current)
            current, count = None, 0
        current = SegmentRange(current.start_char if current else atom.start_char, atom.end_char)
        count += words
        # A coherent passage is preferred over filling to the maximum.
        if count >= target:
            result.append(current)
            current, count = None, 0
    if current:
        result.append(current)
    _validate(text, result)
    return result


def _validate(text: str, spans: list[SegmentRange]) -> None:
    previous = 0
    for span in spans:
        if not (0 <= previous <= span.start_char < span.end_char <= len(text)):
            raise ValueError("Invalid or overlapping passage offsets")
        if text[previous:span.start_char].strip():
            raise ValueError("Segmentation omitted non-whitespace text")
        previous = span.end_char
    if text[previous:].strip():
        raise ValueError("Segmentation omitted trailing text")


def model_text(text: str, span: SegmentRange) -> str:
    """A derived model input; never use its offsets for document highlighting."""
    from qualitative_themes.analysis.transcript_text import clean_model_text
    return clean_model_text(text[span.start_char:span.end_char])
