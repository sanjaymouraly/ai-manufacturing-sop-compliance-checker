"""Text normalisation, hashing and lexical similarity helpers.

Everything here is deterministic and dependency-light so the offline engine
produces byte-identical results across platforms and runs.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections import Counter
from math import sqrt

_WS_RE = re.compile(r"\s+")
_NON_ALNUM = re.compile(r"[^a-z0-9%./+\- ]+")

STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "for",
    "from",
    "has",
    "in",
    "is",
    "it",
    "its",
    "of",
    "on",
    "or",
    "that",
    "the",
    "to",
    "was",
    "were",
    "will",
    "with",
    "shall",
    "must",
    "should",
    "per",
    "any",
    "all",
    "this",
    "each",
    "into",
    "using",
    "use",
}

#: Domain synonyms let the deterministic matcher relate operator wording to SOP wording
#: without an LLM. Kept small, explicit and auditable on purpose.
SYNONYMS: dict[str, str] = {
    "torqued": "torque",
    "torquing": "torque",
    "tightened": "torque",
    "tighten": "torque",
    "verified": "verify",
    "verification": "verify",
    "checked": "verify",
    "check": "verify",
    "inspected": "inspect",
    "inspection": "inspect",
    "recorded": "record",
    "logged": "record",
    "log": "record",
    "documented": "record",
    "cleaned": "clean",
    "cleaning": "clean",
    "calibrated": "calibrate",
    "calibration": "calibrate",
    "measured": "measure",
    "measurement": "measure",
    "labelled": "label",
    "labeled": "label",
    "labelling": "label",
    "labeling": "label",
    "sealed": "seal",
    "sealing": "seal",
    "scanned": "scan",
    "scanning": "scan",
    "photographed": "photograph",
    "photo": "photograph",
    "picture": "photograph",
    "signed": "sign",
    "signature": "sign",
    "signoff": "sign",
    "sign-off": "sign",
    "completed": "complete",
    "performed": "perform",
    "executed": "perform",
    "carried": "perform",
    "temp": "temperature",
    "temperatures": "temperature",
    "press": "pressure",
    "pressures": "pressure",
    "qty": "quantity",
    "nbr": "number",
    "num": "number",
    # "no" is deliberately NOT folded to "number": it is the most common negation
    # token in process records ("no damage found") and collapsing it would make the
    # engine read a negative observation as a positive one.
    "ppe": "ppe",
    "esd": "esd",
    "wiped": "wipe",
    "applied": "apply",
    "install": "install",
    "installed": "install",
    "fitted": "install",
    "mounted": "install",
    "operator": "operator",
    "technician": "operator",
    "batch": "batch",
    "lot": "batch",
    "serial": "serial",
    "sn": "serial",
}


def normalize_whitespace(text: str) -> str:
    return _WS_RE.sub(" ", text).strip()


def normalize_text(text: str) -> str:
    """Lowercase, strip accents/punctuation and collapse whitespace."""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower().replace("\u00b0", " deg ").replace("×", "x")
    text = _NON_ALNUM.sub(" ", text)
    return normalize_whitespace(text)


def stem(token: str) -> str:
    """Conservative suffix stripper so morphological variants collapse together.

    The explicit SYNONYMS table can only cover verbs we thought of in advance; operators
    write "attached", "preheated" or "deburred" and the SOP says "attach", "preheat",
    "deburr". Both sides are stemmed identically, so the stem itself does not need to be a
    real word - only to be stable. Short tokens are left alone to avoid collisions.
    """
    t = token
    if len(t) > 4 and t.endswith("ies"):
        t = t[:-3] + "y"
    elif len(t) > 5 and t.endswith("ing"):
        t = t[:-3]
    elif len(t) > 4 and t.endswith("ed"):
        t = t[:-2]
    elif len(t) > 3 and t.endswith("s") and not t.endswith(("ss", "us", "is")):
        t = t[:-1]
    if len(t) > 4 and t.endswith("e"):
        t = t[:-1]
    if len(t) > 4 and t[-1] == t[-2]:  # "stopp" -> "stop"
        t = t[:-1]
    return t


def tokenize(text: str, *, drop_stopwords: bool = True, apply_stemming: bool = True) -> list[str]:
    tokens = [t for t in normalize_text(text).split(" ") if t]
    tokens = [SYNONYMS.get(t, t) for t in tokens]
    if drop_stopwords:
        tokens = [t for t in tokens if t not in STOPWORDS]
    if apply_stemming:
        tokens = [stem(t) for t in tokens]
    return tokens


def token_set(text: str) -> set[str]:
    return set(tokenize(text))


def cosine_similarity(a: str, b: str) -> float:
    """TF cosine similarity over normalised, synonym-folded tokens."""
    ca, cb = Counter(tokenize(a)), Counter(tokenize(b))
    if not ca or not cb:
        return 0.0
    common = set(ca) & set(cb)
    num = sum(ca[t] * cb[t] for t in common)
    den = sqrt(sum(v * v for v in ca.values())) * sqrt(sum(v * v for v in cb.values()))
    return float(num / den) if den else 0.0


def jaccard(a: str, b: str) -> float:
    sa, sb = token_set(a), token_set(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def similarity(a: str, b: str) -> float:
    """Blended lexical similarity in [0, 1] (cosine + jaccard + containment)."""
    if not a or not b:
        return 0.0
    cos = cosine_similarity(a, b)
    jac = jaccard(a, b)
    sa, sb = token_set(a), token_set(b)
    containment = len(sa & sb) / max(1, min(len(sa), len(sb)))
    return round(0.5 * cos + 0.2 * jac + 0.3 * containment, 4)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def short_hash(text: str, length: int = 12) -> str:
    return sha256_text(text)[:length]


def find_span(haystack: str, needle: str, hint: int = 0) -> tuple[int, int] | None:
    """Locate ``needle`` in ``haystack`` tolerating whitespace differences.

    Returns a (start, end) char span in the *original* haystack or ``None``.
    """
    if not needle:
        return None
    idx = haystack.find(needle, max(0, hint - 400))
    if idx == -1:
        idx = haystack.find(needle)
    if idx != -1:
        return idx, idx + len(needle)

    # whitespace-insensitive search
    pattern = re.compile(r"\s+".join(re.escape(part) for part in needle.split()), re.IGNORECASE)
    match = pattern.search(haystack)
    if match:
        return match.start(), match.end()
    return None


def snippet(text: str, start: int, end: int, context: int = 0) -> str:
    s = max(0, start - context)
    e = min(len(text), end + context)
    return normalize_whitespace(text[s:e])


def truncate(text: str, limit: int = 240) -> str:
    text = normalize_whitespace(text)
    return text if len(text) <= limit else text[: limit - 1] + "…"
