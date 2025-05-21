"""Document structure parsing: headings, numbered steps, tables, paragraphs.

Produces :class:`ParsedSection` objects carrying a ``section_path`` (e.g.
``4.2.1``), page number and exact character offsets into the extracted text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.services.ingestion.extractors import ExtractedDocument
from app.utils.text import normalize_whitespace

_MD_HEADING = re.compile(r"^(#{1,6})\s+(?P<title>.+?)\s*$")
# A heading is a *single-level* number followed by a short title with no terminal period.
_NUM_HEADING = re.compile(r"^(?P<num>\d+)[.)]?\s+(?P<title>[A-Z][^\n.]{2,70})$")
# Any multi-level number (4.1, 4.2.3) introduces a procedural step.
_NUM_STEP = re.compile(r"^(?P<num>\d+(?:\.\d+)+)[.)]?\s+(?P<body>.+)$")
_STEP_WORD = re.compile(r"^step\s*(?P<num>\d+(?:\.\d+)*)\s*[:.)-]?\s*(?P<body>.+)$", re.I)
_BULLET = re.compile(r"^[-*•]\s+(?P<body>.+)$")
_TABLE_ROW = re.compile(r"^\s*\|?[^|\n]*\|[^|\n]*")
_PAGE_MARK = re.compile(r"^\[page (\d+)\]$")
_TABLE_MARK = re.compile(r"^\[table (\d+)\]$")


@dataclass
class ParsedSection:
    section_path: str
    heading: str | None
    level: int
    kind: str  # heading | paragraph | step | table | list
    page: int
    order_index: int
    char_start: int
    char_end: int
    text: str
    step_number: str | None = None
    parent_path: str = ""
    metadata: dict[str, object] = field(default_factory=dict)


def _heading_path(counters: list[int], level: int) -> str:
    while len(counters) < level:
        counters.append(0)
    counters[level - 1] += 1
    del counters[level:]
    return ".".join(str(c) for c in counters[:level])


def parse_structure(doc: ExtractedDocument) -> list[ParsedSection]:
    """Split an extracted document into addressable sections."""
    text = doc.text
    sections: list[ParsedSection] = []
    counters: list[int] = []
    order = 0
    current_heading: str | None = None
    current_path = ""
    current_level = 0
    current_page = 1
    cursor = 0
    in_table = 0

    for raw_line in text.split("\n"):
        line_start = cursor
        line_end = cursor + len(raw_line)
        cursor = line_end + 1  # account for the newline
        line = raw_line.strip()
        if not line:
            continue

        if m := _PAGE_MARK.match(line):
            current_page = int(m.group(1))
            continue
        page = doc.page_for_offset(line_start) if doc.pages else current_page

        if m := _TABLE_MARK.match(line):
            in_table = int(m.group(1))
            continue

        kind = "paragraph"
        heading: str | None = None
        level = current_level or 1
        path = current_path
        step_number: str | None = None
        body = line

        if m := _MD_HEADING.match(line):
            level = len(m.group(1))
            heading = normalize_whitespace(m.group("title"))
            inline = re.match(r"^(?P<num>\d+(?:\.\d+)*)[.)]?\s+(?P<rest>.+)$", heading)
            if inline:
                path = inline.group("num")
                heading = normalize_whitespace(inline.group("rest"))
                counters = [int(p) for p in path.split(".")]
            else:
                path = _heading_path(counters, level)
            kind = "heading"
            body = heading
            current_heading, current_path, current_level = heading, path, level
            in_table = 0
        elif m := _NUM_STEP.match(line):
            step_number = m.group("num")
            path = step_number
            body = normalize_whitespace(m.group("body"))
            kind = "step"
            level = step_number.count(".") + 1
        elif m := _NUM_HEADING.match(line):
            path = m.group("num")
            heading = normalize_whitespace(m.group("title"))
            level = 1
            counters = [int(path)]
            kind = "heading"
            body = heading
            current_heading, current_path, current_level = heading, path, level
            in_table = 0
        elif m := _STEP_WORD.match(line):
            step_number = m.group("num")
            path = f"{current_path}.{step_number}" if current_path else step_number
            body = normalize_whitespace(m.group("body"))
            kind = "step"
        elif in_table and _TABLE_ROW.match(raw_line) and "|" in raw_line:
            kind = "table"
            path = f"{current_path}.T{in_table}" if current_path else f"T{in_table}"
            body = normalize_whitespace(line)
        elif m := _BULLET.match(line):
            kind = "list"
            body = normalize_whitespace(m.group("body"))
            path = current_path
        else:
            body = normalize_whitespace(line)

        sections.append(
            ParsedSection(
                section_path=path or "0",
                heading=heading or current_heading,
                level=level,
                kind=kind,
                page=page,
                order_index=order,
                char_start=line_start,
                char_end=line_end,
                text=body,
                step_number=step_number,
                parent_path=current_path if kind != "heading" else "",
            )
        )
        order += 1

    return sections


def section_for_offset(sections: list[ParsedSection], offset: int) -> ParsedSection | None:
    for section in sections:
        if section.char_start <= offset < section.char_end:
            return section
    return None
