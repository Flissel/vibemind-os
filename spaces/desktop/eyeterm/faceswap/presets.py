"""Target-face preset resolver.

Looks up face images in `faceswap/targets/` by name (without extension).
Images are never committed — the folder is gitignored. Users drop their
own face images there (synthetic StyleGAN faces, own face, licensed stock).
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional

TARGETS_DIR = Path(__file__).parent / "targets"
ACCEPTED_EXTS = (".jpg", ".jpeg", ".png", ".webp")


def resolve_preset(name: str) -> Path:
    """Resolve a preset name to a face image path. Raises if not found."""
    stem = name.strip().lower()
    for ext in ACCEPTED_EXTS:
        candidate = TARGETS_DIR / f"{stem}{ext}"
        if candidate.exists():
            return candidate
    available = list_presets()
    if available:
        raise FileNotFoundError(
            f"Preset '{name}' not found in {TARGETS_DIR}. "
            f"Available: {', '.join(available)}"
        )
    raise FileNotFoundError(
        f"No target preset '{name}' and no presets installed. "
        f"Drop a face image into {TARGETS_DIR} (jpg/png/webp) to use --target-preset."
    )


def list_presets() -> List[str]:
    """Return sorted list of available preset names (without extension)."""
    if not TARGETS_DIR.is_dir():
        return []
    stems = {p.stem for p in TARGETS_DIR.iterdir() if p.suffix.lower() in ACCEPTED_EXTS}
    return sorted(stems)
