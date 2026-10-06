"""Map immutable Python source offsets to Qt's UTF-16 document positions.

Qt normalizes paragraph separators and uses UTF-16 cursor positions. This
mapping preserves the original source text, including CRLF and non-BMP chars.
"""
from bisect import bisect_right
from dataclasses import dataclass


@dataclass(frozen=True)
class TextCoordinates:
    display_text: str
    canonical_to_qt: tuple[int, ...]

    @classmethod
    def build(cls, canonical: str) -> "TextCoordinates":
        out: list[str] = []
        positions = [0]
        qt = 0
        i = 0
        while i < len(canonical):
            char = canonical[i]
            if char == "\r":
                out.append("\n")
                qt += 1
                positions.append(qt)
                i += 1
                if i < len(canonical) and canonical[i] == "\n":
                    # Both source boundaries after the CR and LF share the
                    # display boundary after the normalized newline.
                    positions.append(qt)
                    i += 1
                continue
            display = "\n" if char in "\f\u2028\u2029" else char
            out.append(display)
            qt += len(display.encode("utf-16-le")) // 2
            positions.append(qt)
            i += 1
        return cls("".join(out), tuple(positions))

    def qt_range(self, start: int, end: int) -> tuple[int, int]:
        if not 0 <= start < end < len(self.canonical_to_qt):
            raise ValueError("Source range is outside the document")
        return self.canonical_to_qt[start], self.canonical_to_qt[end]

    def canonical_range(self, qt_start: int, qt_end: int) -> tuple[int, int]:
        if not 0 <= qt_start < qt_end <= self.canonical_to_qt[-1]:
            raise ValueError("Qt selection is outside the document")
        # Qt never places the cursor inside an emoji surrogate pair. At a
        # collapsed CRLF boundary, choose the position after both characters.
        start = bisect_right(self.canonical_to_qt, qt_start) - 1
        end = bisect_right(self.canonical_to_qt, qt_end) - 1
        if self.canonical_to_qt[start] != qt_start or self.canonical_to_qt[end] != qt_end:
            raise ValueError("Selection does not fall on valid source boundaries")
        return start, end
