"""Document text extraction with provenance (pages + character offsets).

Supported: PDF, DOCX, TXT, MD, CSV, JSON. Optional OCR for image-only PDFs.

Guarantees
----------
* The extracted ``text`` is stored verbatim; every section/requirement/citation
  references character offsets *into this exact string*, which makes citations
  re-verifiable forever.
* When content cannot be extracted (image-only PDF without OCR, unreadable
  table) the extractor reports a warning and lowers ``confidence``. It never
  invents text.
"""

from __future__ import annotations

import csv
import io
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.logging_setup import get_logger
from app.utils.text import normalize_whitespace

logger = get_logger(__name__)

SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".txt", ".md", ".csv", ".json"}


class UnsupportedDocumentError(ValueError):
    pass


@dataclass
class PageSpan:
    page: int
    char_start: int
    char_end: int

    def to_dict(self) -> dict[str, int]:
        return {"page": self.page, "char_start": self.char_start, "char_end": self.char_end}


@dataclass
class ExtractedDocument:
    text: str
    source_format: str
    page_count: int = 1
    pages: list[PageSpan] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    confidence: float = 1.0
    ocr_used: bool = False
    structured: dict[str, Any] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def page_for_offset(self, offset: int) -> int:
        for span in self.pages:
            if span.char_start <= offset < span.char_end:
                return span.page
        return self.pages[-1].page if self.pages else 1


def _single_page(text: str) -> list[PageSpan]:
    return [PageSpan(page=1, char_start=0, char_end=len(text))]


# --------------------------------------------------------------------------- #
# Format handlers
# --------------------------------------------------------------------------- #
def _extract_text(data: bytes, fmt: str) -> ExtractedDocument:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        text = data.decode("latin-1", errors="replace")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    warnings: list[str] = []
    if "\ufffd" in text:
        warnings.append("Replacement characters present: source encoding was not valid UTF-8.")
    # Treat a form feed as a page break so paginated plain text keeps page refs.
    pages: list[PageSpan] = []
    if "\f" in text:
        cursor = 0
        for i, chunk in enumerate(text.split("\f"), start=1):
            pages.append(PageSpan(page=i, char_start=cursor, char_end=cursor + len(chunk)))
            cursor += len(chunk) + 1
        text = text.replace("\f", "\n")
    else:
        pages = _single_page(text)
    return ExtractedDocument(
        text=text,
        source_format=fmt,
        page_count=len(pages),
        pages=pages,
        warnings=warnings,
        confidence=1.0 if not warnings else 0.9,
    )


def _extract_pdf(data: bytes, *, enable_ocr: bool = False) -> ExtractedDocument:
    from pypdf import PdfReader

    warnings: list[str] = []
    reader = PdfReader(io.BytesIO(data))
    chunks: list[str] = []
    pages: list[PageSpan] = []
    cursor = 0
    empty_pages = 0
    for index, page in enumerate(reader.pages, start=1):
        try:
            page_text = page.extract_text() or ""
        except Exception as exc:  # pragma: no cover - defensive
            page_text = ""
            warnings.append(f"Page {index}: extraction error ({exc.__class__.__name__}).")
        page_text = page_text.replace("\r\n", "\n").strip("\n")
        if not page_text.strip():
            empty_pages += 1
            if enable_ocr:
                ocr_text, ocr_warn = _try_ocr_page(data, index)
                if ocr_text:
                    page_text = ocr_text
                if ocr_warn:
                    warnings.append(ocr_warn)
            if not page_text.strip():
                warnings.append(
                    f"Page {index}: no extractable text layer (scanned image?). "
                    "Content not available; no text was inferred."
                )
        header = f"[page {index}]\n"
        block = header + page_text + "\n"
        chunks.append(block)
        pages.append(PageSpan(page=index, char_start=cursor, char_end=cursor + len(block)))
        cursor += len(block)

    text = "".join(chunks)
    total = max(1, len(reader.pages))
    confidence = round(max(0.1, 1.0 - (empty_pages / total) * 0.9), 3)
    doc = ExtractedDocument(
        text=text,
        source_format="pdf",
        page_count=total,
        pages=pages,
        warnings=warnings,
        confidence=confidence,
        ocr_used=enable_ocr and empty_pages > 0,
    )
    try:
        info: dict[str, object] = dict(reader.metadata or {})
        doc.metadata = {str(k).lstrip("/"): str(v) for k, v in info.items()}
    except Exception:  # pragma: no cover
        pass
    return doc


def _try_ocr_page(data: bytes, page_number: int) -> tuple[str, str | None]:
    """Best-effort OCR. Returns (text, warning). Never fabricates content."""
    try:
        import pytesseract  # type: ignore
        from pdf2image import convert_from_bytes  # type: ignore
    except Exception:
        return "", (
            f"Page {page_number}: OCR requested but optional OCR dependencies "
            "(pytesseract, pdf2image, tesseract-ocr) are not installed."
        )
    try:
        images = convert_from_bytes(data, first_page=page_number, last_page=page_number, dpi=200)
        if not images:
            return "", f"Page {page_number}: OCR produced no image."
        text = pytesseract.image_to_string(images[0]) or ""
        if not text.strip():
            return "", f"Page {page_number}: OCR returned no text."
        return text, f"Page {page_number}: text recovered via OCR (lower confidence)."
    except Exception as exc:  # pragma: no cover - environment dependent
        return "", f"Page {page_number}: OCR failed ({exc.__class__.__name__})."


def _extract_docx(data: bytes) -> ExtractedDocument:
    import docx  # python-docx

    document = docx.Document(io.BytesIO(data))
    lines: list[str] = []
    warnings: list[str] = []

    for para in document.paragraphs:
        style = (para.style.name or "").lower() if para.style is not None else ""
        text = para.text.rstrip()
        if not text.strip():
            lines.append("")
            continue
        if style.startswith("heading"):
            level = "".join(ch for ch in style if ch.isdigit()) or "1"
            lines.append("#" * min(6, int(level)) + " " + text)
        else:
            lines.append(text)

    for t_index, table in enumerate(document.tables, start=1):
        lines.append("")
        lines.append(f"[table {t_index}]")
        for row in table.rows:
            cells = [normalize_whitespace(cell.text) for cell in row.cells]
            lines.append(" | ".join(cells))
    if not any(line.strip() for line in lines):
        warnings.append("DOCX contained no extractable paragraphs or tables.")

    text = "\n".join(lines)
    return ExtractedDocument(
        text=text,
        source_format="docx",
        page_count=1,
        pages=_single_page(text),
        warnings=warnings,
        confidence=1.0 if text.strip() else 0.2,
        metadata={"paragraphs": len(document.paragraphs), "tables": len(document.tables)},
    )


def _extract_csv(data: bytes) -> ExtractedDocument:
    raw = data.decode("utf-8", errors="replace")
    warnings: list[str] = []
    try:
        dialect = csv.Sniffer().sniff(raw[:4096], delimiters=",;\t|")
    except Exception:
        dialect = csv.excel
        warnings.append("CSV delimiter could not be sniffed; defaulted to comma.")
    reader = csv.DictReader(io.StringIO(raw), dialect=dialect)
    rows = [dict(r) for r in reader]
    header = reader.fieldnames or []
    lines = [" | ".join(header)]
    for _i, row in enumerate(rows, start=1):
        lines.append(" | ".join(normalize_whitespace(str(row.get(h) or "")) for h in header))
    text = "\n".join(lines)
    return ExtractedDocument(
        text=text,
        source_format="csv",
        page_count=1,
        pages=_single_page(text),
        warnings=warnings,
        confidence=1.0 if rows else 0.4,
        structured={"columns": header, "rows": rows},
    )


def _extract_json(data: bytes) -> ExtractedDocument:
    raw = data.decode("utf-8", errors="replace")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise UnsupportedDocumentError(f"Invalid JSON: {exc.msg} (line {exc.lineno})") from exc
    text = json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=False)
    return ExtractedDocument(
        text=text,
        source_format="json",
        page_count=1,
        pages=_single_page(text),
        confidence=1.0,
        structured=payload if isinstance(payload, dict) else {"items": payload},
    )


_MD_PAGE_BREAK = re.compile(r"^\s*(---\s*page\s*break\s*---|\\pagebreak)\s*$", re.I | re.M)


def _extract_markdown(data: bytes) -> ExtractedDocument:
    doc = _extract_text(data, "md")
    if _MD_PAGE_BREAK.search(doc.text):
        pages: list[PageSpan] = []
        cursor = 0
        for i, chunk in enumerate(_MD_PAGE_BREAK.split(doc.text)[::2] or [doc.text], start=1):
            pages.append(PageSpan(page=i, char_start=cursor, char_end=cursor + len(chunk)))
            cursor += len(chunk)
        doc.pages = pages
        doc.page_count = len(pages)
    return doc


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #
def extract_document(data: bytes, filename: str, *, enable_ocr: bool = False) -> ExtractedDocument:
    """Extract text + provenance from an uploaded file."""
    ext = Path(filename).suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        raise UnsupportedDocumentError(
            f"Unsupported file type '{ext or filename}'. Supported: {', '.join(sorted(SUPPORTED_EXTENSIONS))}"
        )
    if ext == ".pdf":
        doc = _extract_pdf(data, enable_ocr=enable_ocr)
    elif ext == ".docx":
        doc = _extract_docx(data)
    elif ext == ".csv":
        doc = _extract_csv(data)
    elif ext == ".json":
        doc = _extract_json(data)
    elif ext == ".md":
        doc = _extract_markdown(data)
    else:
        doc = _extract_text(data, "txt")

    if not doc.text.strip():
        doc.warnings.append("No text content could be extracted from this document.")
        doc.confidence = min(doc.confidence, 0.1)
    logger.info(
        "Extracted %s (%s): %d chars, %d page(s), confidence=%.2f, warnings=%d",
        filename,
        doc.source_format,
        len(doc.text),
        doc.page_count,
        doc.confidence,
        len(doc.warnings),
    )
    return doc
