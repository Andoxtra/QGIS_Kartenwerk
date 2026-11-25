"""Disk cache of rendered basemap images, so re-exporting a sheet downloads nothing.

Only the bare basemap of a sheet is cached (transparent, before grid lines,
overlays and border), and only for sources whose terms allow keeping data
(``Source.cacheable``; Esri World Imagery is excluded). Entries expire after
MAX_AGE_DAYS and the cache is trimmed to MAX_BYTES, oldest first.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path

CACHE_VERSION = 1
MAX_AGE_DAYS = 30
MAX_BYTES = 1024 * 1024 * 1024  # 1 GB

_override: Path | None = None


def set_cache_root(path: Path | None) -> None:
    """Override the cache folder (used by the tests)."""
    global _override
    _override = Path(path) if path else None


def root() -> Path:
    if _override is not None:
        path = _override
    else:
        from qgis.core import QgsApplication

        path = Path(QgsApplication.qgisSettingsDirPath()) / "kartenwerk" / "render_cache"
    path.mkdir(parents=True, exist_ok=True)
    return path


def key(source_key: str, layer_uri: str, crs_authid: str, extent, width: int, height: int,
        dpi: int) -> str:
    """Cache key for one basemap render. Anything that changes the image is part of it."""
    payload = json.dumps([
        CACHE_VERSION, source_key, layer_uri, crs_authid,
        [round(v, 2) for v in (extent.xMinimum(), extent.yMinimum(),
                               extent.xMaximum(), extent.yMaximum())],
        int(width), int(height), int(dpi),
    ])
    return f"{source_key}_{hashlib.sha256(payload.encode()).hexdigest()[:32]}"


def _path(cache_key: str) -> Path:
    return root() / f"{cache_key}.png"


def load(cache_key: str):
    """Cached QImage, or None. Expired entries are removed."""
    from qgis.PyQt.QtGui import QImage

    path = _path(cache_key)
    try:
        age = time.time() - path.stat().st_mtime
    except OSError:
        return None
    if age > MAX_AGE_DAYS * 86400:
        path.unlink(missing_ok=True)
        return None
    image = QImage(str(path))
    if image.isNull():
        path.unlink(missing_ok=True)
        return None
    return image


def store(cache_key: str, image) -> None:
    """Write atomically (safe from worker threads). The file age counts from the download."""
    try:
        path = _path(cache_key)
        tmp = path.with_suffix(f".{os.getpid()}.{time.monotonic_ns()}.tmp")
        if image.save(str(tmp), "PNG"):
            os.replace(tmp, path)
        else:
            tmp.unlink(missing_ok=True)
    except OSError:
        pass  # the cache is an optimisation; a failed write must not fail the export


def entries() -> list[Path]:
    return sorted(root().glob("*.png"))


def size_bytes() -> int:
    return sum(p.stat().st_size for p in entries() if p.exists())


def prune(max_bytes: int = MAX_BYTES) -> None:
    """Remove expired entries, then the oldest until the cache fits into max_bytes."""
    now = time.time()
    files = []
    for path in entries():
        try:
            stat = path.stat()
        except OSError:
            continue
        if now - stat.st_mtime > MAX_AGE_DAYS * 86400:
            path.unlink(missing_ok=True)
        else:
            files.append((stat.st_mtime, stat.st_size, path))
    total = sum(size for _, size, _ in files)
    for _, size, path in sorted(files):
        if total <= max_bytes:
            break
        path.unlink(missing_ok=True)
        total -= size
    for tmp in root().glob("*.tmp"):  # leftovers of interrupted writes
        if now - tmp.stat().st_mtime > 3600:
            tmp.unlink(missing_ok=True)


def clear() -> None:
    for path in list(root().glob("*.png")) + list(root().glob("*.tmp")):
        path.unlink(missing_ok=True)
