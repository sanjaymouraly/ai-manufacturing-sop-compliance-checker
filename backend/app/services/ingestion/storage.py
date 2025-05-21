"""Safe file storage: filename sanitisation, size limits, checksums."""

from __future__ import annotations

import re
import unicodedata
from datetime import UTC, datetime
from pathlib import Path

from app.config import get_settings
from app.utils.text import sha256_bytes

_SAFE_RE = re.compile(r"[^A-Za-z0-9._-]+")


class UploadValidationError(ValueError):
    pass


def sanitize_filename(filename: str, *, max_length: int = 120) -> str:
    """Return a filesystem-safe base name (no paths, no traversal, no control chars)."""
    name = unicodedata.normalize("NFKD", filename or "")
    name = name.encode("ascii", "ignore").decode("ascii")
    name = name.replace("\\", "/").split("/")[-1]
    name = name.strip().replace(" ", "_")
    name = _SAFE_RE.sub("_", name)
    name = name.lstrip(".") or "upload"
    stem, dot, suffix = name.rpartition(".")
    if dot:
        stem = stem[: max_length - len(suffix) - 1] or "upload"
        name = f"{stem}.{suffix.lower()}"
    else:
        name = name[:max_length]
    return name


def validate_upload(filename: str, data: bytes) -> str:
    """Validate extension + size. Returns the sanitised filename."""
    settings = get_settings()
    safe = sanitize_filename(filename)
    ext = Path(safe).suffix.lower()
    if ext not in settings.upload_extensions:
        raise UploadValidationError(
            f"File type '{ext or 'unknown'}' is not allowed. Allowed: {', '.join(sorted(settings.upload_extensions))}"
        )
    if len(data) == 0:
        raise UploadValidationError("Uploaded file is empty.")
    if len(data) > settings.max_upload_bytes:
        raise UploadValidationError(
            f"File is {len(data) / 1_048_576:.1f} MB which exceeds the {settings.max_upload_mb} MB limit."
        )
    return safe


def store_file(data: bytes, filename: str, *, subdir: str) -> tuple[Path, str]:
    """Persist bytes under ``var/storage/<subdir>/<sha-prefix>_<name>``.

    Content-addressed naming gives free de-duplication and makes checksum
    verification trivial. Returns ``(path, sha256)``.
    """
    digest = sha256_bytes(data)
    safe = sanitize_filename(filename)
    target_dir = get_settings().storage_path / subdir
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / f"{digest[:12]}_{safe}"
    if not path.exists():
        path.write_bytes(data)
    return path, digest


def verify_checksum(path: str | Path, expected_sha256: str) -> bool:
    p = Path(path)
    if not p.exists():
        return False
    return sha256_bytes(p.read_bytes()) == expected_sha256


def file_stat(path: str | Path) -> dict[str, object]:
    p = Path(path)
    if not p.exists():
        return {"exists": False}
    st = p.stat()
    return {
        "exists": True,
        "size": st.st_size,
        "modified_at": datetime.fromtimestamp(st.st_mtime, tz=UTC).isoformat(),
    }
