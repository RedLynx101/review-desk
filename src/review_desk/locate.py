"""Find a value on the page: which words, on which line, in which box.

The model cites a line, and this code checks that citation against the words. Dates and amounts are
compared by what they mean, so "$1,234.50" matches "1234.50". Text is compared character by character,
with fuzzy matches reported as fuzzy, because OCR gets letters wrong and that needs a lower score.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from difflib import SequenceMatcher

from .normalize import parse_amount, parse_date, squash
from .pagetext import DocumentText, Line, Word
from .schema import SourceBox


@dataclass(frozen=True)
class Match:
    line: Line
    words: tuple[Word, ...]
    similarity: float = 1.0

    @property
    def exact(self) -> bool:
        return self.similarity >= 0.999

    @property
    def min_confidence(self) -> float:
        return min(word.confidence for word in self.words)

    @property
    def text(self) -> str:
        return " ".join(word.text for word in self.words)


def boxes(match: Match) -> list[SourceBox]:
    """One box per visual row, so a value wrapped onto two rows gets two boxes."""
    rows: dict[tuple[int, int], list[Word]] = {}
    for word in match.words:
        rows.setdefault((word.page, round(word.center_y / 3)), []).append(word)
    out = []
    for words in rows.values():
        out.append(SourceBox(
            page=words[0].page,
            x0=round(min(w.x0 for w in words), 2),
            top=round(min(w.top for w in words), 2),
            x1=round(max(w.x1 for w in words), 2),
            bottom=round(max(w.bottom for w in words), 2),
        ))
    return out


def _windows(line: Line, sizes):
    words = line.words
    for start in range(len(words)):
        for size in sizes:
            if start + size <= len(words):
                yield words[start:start + size]


def _trim_edges(text: str) -> str:
    return text.strip(" :;,#()[]")


def find_amount(document: DocumentText, value: Decimal) -> list[Match]:
    """Every place the amount is printed, smallest word window first."""
    matches = []
    target = abs(value)
    for line in document.lines:
        used: set[int] = set()
        for size in (1, 2, 3):
            for start in range(len(line.words) - size + 1):
                window = line.words[start:start + size]
                if used & set(range(start, start + size)):
                    continue
                parsed = parse_amount(_trim_edges(" ".join(word.text for word in window)))
                if parsed is not None and parsed.value == target:
                    matches.append(Match(line, tuple(window)))
                    used.update(range(start, start + size))
    return matches


def find_date(document: DocumentText, value: date) -> list[Match]:
    matches = []
    for line in document.lines:
        for window in _windows(line, (1, 2, 3, 4)):
            parsed = parse_date(_trim_edges(" ".join(word.text for word in window)))
            if parsed is not None and value in (parsed.value, parsed.alternative):
                matches.append(Match(line, tuple(window)))
    return matches


def _longest_prefix_run(line: Line, target: str) -> tuple[str, list[Word]]:
    """The longest run of adjacent words on a row that spells the start of `target`."""
    best_text, best_run = "", []
    for start in range(len(line.words)):
        collected, run = "", []
        for word in line.words[start:]:
            piece = squash(word.text)
            if not piece:
                continue
            if not target.startswith(collected + piece):
                break
            collected += piece
            run.append(word)
        if len(collected) > len(best_text):
            best_text, best_run = collected, run
    return best_text, best_run


def _continue_on_following_lines(document: DocumentText, line: Line, target: str, *, max_rows: int = 3) -> tuple[Word, ...] | None:
    """Wrapped text: find the rest of `target` on the next rows, ignoring other columns beside it."""
    words: list[Word] = []
    number = line.number
    for _ in range(max_rows):
        number += 1
        following = document.line(number)
        if following is None or following.page != line.page:
            return None
        text, run = _longest_prefix_run(following, target)
        if not run:
            return None
        words += run
        target = target[len(text):]
        if not target:
            return tuple(words)
    return None


def find_text(document: DocumentText, text: str | None, *, fuzzy_floor: float = 0.72) -> list[Match]:
    """Every place `text` is printed, allowing it to wrap onto the following row.

    Exact matches come first. Fuzzy matches are only searched for when no exact match exists, and
    they carry their similarity so the score can reflect how far off they were.
    """
    target = squash(text)
    if not target:
        return []
    matches: list[Match] = []
    for line in document.lines:
        for start in range(len(line.words)):
            collected = ""
            run: list[Word] = []
            finished = False
            for word in line.words[start:]:
                piece = squash(word.text)
                if not piece:
                    if run:
                        run.append(word)
                        continue
                    break
                if target.startswith(collected + piece):
                    collected += piece
                    run.append(word)
                    if collected == target:
                        matches.append(Match(line, tuple(run)))
                        finished = True
                        break
                elif not run and target in piece and len(target) >= 4:
                    # Glued text, such as "Invoice#A087865" in one word.
                    matches.append(Match(line, (word,)))
                    finished = True
                    break
                else:
                    break
            if not finished and collected and len(collected) >= 4:
                rest = _continue_on_following_lines(document, line, target[len(collected):])
                if rest:
                    matches.append(Match(line, tuple(run) + rest))
    if matches:
        return matches
    return _fuzzy(document, target, fuzzy_floor)


def _fuzzy(document: DocumentText, target: str, floor: float) -> list[Match]:
    """Best near-match per line. OCR errors change a few characters, not the word count much."""
    matches = []
    for line in document.lines:
        best: tuple[float, tuple[Word, ...]] | None = None
        for start in range(len(line.words)):
            collected = ""
            for end in range(start, len(line.words)):
                collected += squash(line.words[end].text)
                if len(collected) > len(target) * 1.4 + 2:
                    break
                if len(collected) < len(target) * 0.6:
                    continue
                ratio = SequenceMatcher(None, target, collected, autojunk=False).ratio()
                if ratio >= floor and (best is None or ratio > best[0]):
                    best = (ratio, tuple(line.words[start:end + 1]))
        if best:
            matches.append(Match(line, best[1], similarity=min(best[0], 0.99)))
    matches.sort(key=lambda match: -match.similarity)
    return matches


def choose(matches: list[Match], cited_line: int | None) -> tuple[Match | None, bool]:
    """Prefer the match on the cited line. Returns the match and whether the citation held."""
    if not matches:
        return None, False
    for match in matches:
        if match.line.number == cited_line:
            return match, True
    return matches[0], False


def label_context(document: DocumentText, match: Match) -> tuple[str, str]:
    """What a reader would take as the value's label: nearby words to the left, and the row above."""
    first = match.words[0]
    last = match.words[-1]
    left = [word for word in match.line.words if word.x1 <= first.x0 + 0.5 and word not in match.words]
    nearest_left = " ".join(word.text for word in left[-5:])
    above = ""
    previous = document.line(match.line.number - 1)
    if previous is not None and previous.page == match.line.page:
        close = first.top - previous.words[0].bottom < max(first.height * 2.2, 14)
        if close:
            above = " ".join(
                word.text for word in previous.words
                if word.x1 >= first.x0 - 12 and word.x0 <= last.x1 + 12
            )
    return nearest_left.lower(), above.lower()
