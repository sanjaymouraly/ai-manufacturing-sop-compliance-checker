"""Prompt-injection detection and neutralisation for untrusted documents.

Uploaded SOPs and process reports are **data, never instructions**. Two layers:

1. :func:`scan_text` flags suspicious spans so the auditor sees an explicit
   ``prompt_injection`` finding with evidence (the attempt is reported, not
   hidden).
2. :func:`neutralize_for_prompt` escapes the text before it is embedded in an
   LLM prompt, and the prompt template itself wraps content in delimiters with
   an explicit "treat as untrusted data" instruction.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass

from app.utils.text import normalize_whitespace

_PATTERNS: list[tuple[str, re.Pattern[str], str]] = [
    (
        "instruction_override",
        re.compile(
            # Either "<verb> ... <scope> ... <noun>" ("ignore all previous instructions")
            # or the elliptical form that names no noun at all ("disregard the above").
            r"\b(ignore|disregard|forget|override)\b[^.\n]{0,40}\b(previous|prior|above|all|any|earlier)\b"
            r"[^.\n]{0,30}\b(instruction|prompt|rule|direction|guideline)s?\b"
            r"|\b(ignore|disregard|forget|override)\s+(?:the\s+|everything\s+)?"
            r"(above|preceding|foregoing|previous|prior|earlier)\b",
            re.I,
        ),
        "Attempts to override the analysis instructions.",
    ),
    (
        "compliance_coercion",
        re.compile(
            r"\b(mark|report|classify|treat|declare|set|output|answer|return|respond|state|write|say)\b"
            r"[^.\n]{0,40}\b(as\s+)?(fully\s+)?(compliant|approved|passed|conforming|no\s+deviation)\b",
            re.I,
        ),
        "Attempts to force a compliant verdict.",
    ),
    (
        "finding_suppression",
        re.compile(
            r"\b(do\s*not|don't|never|avoid)\b[^.\n]{0,40}\b(report|flag|raise|list|mention|record)\b[^.\n]{0,30}\b(deviation|finding|issue|discrepanc|error|problem)",
            re.I,
        ),
        "Attempts to suppress findings.",
    ),
    (
        "role_hijack",
        re.compile(
            r"\b(you\s+are\s+(now\s+)?an?\b|system\s*prompt|assistant\s*:|</?\s*(system|assistant|instructions?)\s*>)",
            re.I,
        ),
        "Attempts to redefine the assistant role or inject a system prompt.",
    ),
    (
        "authority_claim",
        re.compile(
            r"\b(quality\s+(manager|engineer)|auditor|admin(istrator)?)\b[^.\n]{0,30}\b(has\s+)?(approved|authori[sz]ed|waived|signed\s+off)\b[^.\n]{0,40}\b(skip|bypass|without|no\s+need)\b",
            re.I,
        ),
        "Claims out-of-band authority to bypass controls.",
    ),
    (
        "verification_bypass",
        re.compile(
            r"\b(skip|bypass|omit|no\s+need\s+for)\b[^.\n]{0,30}\b(verification|validation|citation|evidence|check)s?\b",
            re.I,
        ),
        "Attempts to bypass evidence verification.",
    ),
    (
        "exfiltration",
        re.compile(
            r"\b(print|reveal|output|show|send)\b[^.\n]{0,30}\b(api[\s_-]?key|secret|token|credential|system\s+prompt|env(ironment)?\s+variable)s?\b",
            re.I,
        ),
        "Attempts to exfiltrate credentials or internal prompts.",
    ),
    (
        "hidden_directive",
        re.compile(r"\[\[?\s*(system|instruction|note\s+to\s+(ai|model|auditor))\b[^\]]{0,80}\]\]?", re.I),
        "Hidden directive embedded in brackets.",
    ),
]


@dataclass
class InjectionHit:
    pattern: str
    description: str
    quote: str
    char_start: int
    char_end: int

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def scan_text(text: str, *, max_hits: int = 25) -> list[InjectionHit]:
    """Return suspicious spans found in untrusted text."""
    hits: list[InjectionHit] = []
    seen: set[tuple[int, int]] = set()
    for name, pattern, description in _PATTERNS:
        for match in pattern.finditer(text or ""):
            span = (match.start(), match.end())
            if span in seen:
                continue
            seen.add(span)
            start = max(0, match.start() - 40)
            end = min(len(text), match.end() + 40)
            hits.append(
                InjectionHit(
                    pattern=name,
                    description=description,
                    quote=normalize_whitespace(text[start:end]),
                    char_start=match.start(),
                    char_end=match.end(),
                )
            )
            if len(hits) >= max_hits:
                return hits
    return sorted(hits, key=lambda h: h.char_start)


def neutralize_for_prompt(text: str) -> str:
    """Escape delimiters/directives before embedding untrusted text in a prompt."""
    cleaned = (text or "").replace("\x00", "")
    cleaned = re.sub(r"</?\s*(system|assistant|user|instructions?)\s*>", "[escaped-tag]", cleaned, flags=re.I)
    cleaned = cleaned.replace("```", "'''")
    for _name, pattern, _desc in _PATTERNS:
        cleaned = pattern.sub(lambda m: f"[UNTRUSTED-CONTENT-REMOVED:{len(m.group(0))}chars]", cleaned)
    return cleaned


def injection_summary(hits: list[InjectionHit]) -> str:
    if not hits:
        return "No embedded instruction-injection patterns detected."
    kinds = sorted({h.pattern for h in hits})
    return f"{len(hits)} suspicious span(s) detected: {', '.join(kinds)}"
