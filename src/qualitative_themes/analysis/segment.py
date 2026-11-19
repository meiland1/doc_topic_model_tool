"""Paragraph-aware segmentation with exact offsets in immutable source text."""
import re
from dataclasses import dataclass

UNITS = {
    "Automatic passages": None,
    "Interview passages": None,
    "Semantic passages": None,
    "Short passages": (100, 125),
    "Standard passages": (200, 250),
    "Long passages": (375, 450),
    "Paragraphs": None,
    "Whole document": None,
}

TOKEN_SAFE_UNITS = {'Automatic passages', 'Interview passages', 'Semantic passages'}

_WORDS = re.compile(r"\S+")
_SENTENCE_BREAK = re.compile(r"(?<=[.!?])\s+")
_PARAGRAPH_BREAK = re.compile(r"(?:\r?\n)[ \t]*(?:\r?\n)+|\f+")


@dataclass(frozen=True)
class SegmentRange:
    """[start_char, end_char) in the stored document text."""
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
    """Split a span that's still too long at word boundaries into ~`target`-word pieces."""
    words = list(_WORDS.finditer(text, span.start_char, span.end_char))
    if len(words) <= maximum:
        return [span]
    return [SegmentRange(words[i].start(), words[min(i + target, len(words)) - 1].end())
            for i in range(0, len(words), target)]


def segment_text(text: str, unit: str = "Standard passages", *, token_budget=None,
                 excluded=(), stats=None, should_cancel=None, semantic_provider=None,
                 semantic_threshold=0.65, semantic_min_tokens=128) -> list[SegmentRange]:
    """Return ordered nonoverlapping [start,end) spans; gaps contain whitespace only.

    Pre: `unit` is a key of UNITS. Token-safe units need a token budget (loaded
    from the bundled tokenizer if not passed) and Semantic passages also need
    `semantic_provider`.
    Post: every non-whitespace character of `text` is inside exactly one span
    (checked by _validate). `excluded` only affects what counts toward token
    limits, not the spans themselves.
    """
    if unit not in UNITS:
        raise ValueError(f"Unknown analysis unit: {unit}")
    if not text.strip():
        return []
    if unit in TOKEN_SAFE_UNITS:
        if token_budget is None:
            from qualitative_themes.analysis.token_budget import load_token_budget
            token_budget = load_token_budget()
        if unit == 'Semantic passages':
            from qualitative_themes.analysis.semantic import sentence_vectors, semantic_spans
            provider = semantic_provider or (lambda texts: sentence_vectors(texts, should_cancel=should_cancel, stats=stats))
            return semantic_spans(text, token_budget, provider, semantic_threshold, excluded, stats, should_cancel,
                                  min_tokens=semantic_min_tokens)
        if unit == 'Interview passages':
            from qualitative_themes.analysis.speakers import reliable_transcript_turns
            turns = reliable_transcript_turns(text)
            if not turns:
                if stats is not None:
                    stats['speaker_fallbacks'] = stats.get('speaker_fallbacks', 0) + 1
                return _automatic(text, token_budget, (), stats, should_cancel)
            blocks = []
            prefix = _trim(text, 0, turns[0].start)
            if prefix:
                blocks.append(prefix)
            previous_key = None
            for turn in turns:
                span = _trim(text, turn.start, turn.end)
                if previous_key == turn.key:
                    blocks[-1] = SegmentRange(blocks[-1].start_char, span.end_char)
                else:
                    blocks.append(span)
                previous_key = turn.key
            if stats is not None:
                stats['speaker_turns'] = stats.get('speaker_turns', 0) + len(turns)
            return _automatic(text, token_budget, excluded, stats, should_cancel, blocks)
        return _automatic(text, token_budget, excluded, stats, should_cancel)
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


def _automatic(text, budget, excluded, stats, should_cancel, blocks=None):
    """Pack paragraphs/sentences into passages that fit the encoder's token limit."""
    def fits(span):
        if should_cancel and should_cancel():
            from qualitative_themes.analysis.topics import AnalysisCancelled
            raise AnalysisCancelled()
        return budget.count(model_text(text, span, excluded)) <= budget.maximum

    def divide(span):
        if fits(span):
            return [span]
        words = list(_WORDS.finditer(text, span.start_char, span.end_char))
        if len(words) > 1:
            middle = words[len(words) // 2].start()
        else:
            middle = (span.start_char + span.end_char) // 2
        if middle <= span.start_char:
            raise ValueError('A source character cannot fit within the model token limit.')
        left = _trim(text, span.start_char, middle)
        right = _trim(text, middle, span.end_char)
        return [part for half in (left, right) if half for part in divide(half)]

    result = []
    sentence_ends = set()
    for paragraph in (blocks if blocks is not None else _paragraphs(text)):
        current = None
        for sentence in _sentences(text, paragraph):
            sentence_ends.add(sentence.end_char)
            parts = divide(sentence)
            for atom in parts:
                candidate = SegmentRange(current.start_char if current else atom.start_char, atom.end_char)
                if current and not fits(candidate):
                    result.append(current)
                    current = atom
                else:
                    current = candidate
        if current:
            result.append(current)
    _validate(text, result)
    if not all(fits(span) for span in result):
        raise ValueError('Automatic passage validation exceeded the model limit.')
    if stats is not None:
        stats['forced_splits'] = stats.get('forced_splits', 0) + sum(span.end_char not in sentence_ends for span in result)
    return result


def _validate(text: str, spans: list[SegmentRange]) -> None:
    """Guard: raise if spans overlap, go out of range, or skip real text."""
    previous = 0
    for span in spans:
        if not (0 <= previous <= span.start_char < span.end_char <= len(text)):
            raise ValueError("Invalid or overlapping passage offsets")
        if text[previous:span.start_char].strip():
            raise ValueError("Segmentation omitted non-whitespace text")
        previous = span.end_char
    if text[previous:].strip():
        raise ValueError("Segmentation omitted trailing text")


def model_text(text: str, span: SegmentRange, excluded=()) -> str:
    """A derived model input; never use its offsets for document highlighting.

    ``excluded`` holds source ranges (e.g. interviewer turns) left out of model input.
    """
    from qualitative_themes.analysis.transcript_text import clean_model_text
    if excluded:
        from qualitative_themes.analysis.speakers import without_ranges
        return clean_model_text(without_ranges(text, span.start_char, span.end_char, excluded))
    return clean_model_text(text[span.start_char:span.end_char])
