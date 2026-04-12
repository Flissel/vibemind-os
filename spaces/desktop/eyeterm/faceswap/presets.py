"""Target-face preset resolver.

Looks up face images in `faceswap/targets/` by name (without extension).
Images are never committed — the folder is gitignored. Users drop their
own face images there (synthetic StyleGAN faces, own face, licensed stock).
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional

TARGETS_DIR = Path(__file__).parent / "targets"
ACCEPTED_EXTS = (".jpg", ".jpeg", ".png", ".webp")

# Display names for the built-in StyleGAN synthetic presets. Neutral first
# names — they are NOT real people. Used by the UI dropdown; the API still
# exposes the underlying slug (filename stem) as `id`.
#
# 100 slots — international mix, gender-balanced. Slugs default→face1..face99
# map to names in insertion order. Overflow past face99 falls back to the
# title-cased slug via display_name().
_NAME_POOL = [
    "Alina",  # default
    "Diego", "Simone", "Mei", "Fabio", "Lukas", "Jana", "Kiyo", "Rosa",
    "Finn", "Nadia", "Martin", "Omar", "Amara", "Yusuf", "Clara", "Noah",
    "Leah", "Marco", "Sora", "Priya", "Jonas", "Elena", "Kofi", "Ines",
    "Milan", "Yara", "Hannes", "Farah", "Ravi", "Anouk", "Tomas", "Aisha",
    "Bruno", "Lena", "Kenji", "Saskia", "Amir", "Greta", "Tariq", "Marta",
    "Sven", "Zoya", "Jens", "Lila", "Pablo", "Emma", "Hiro", "Nora",
    "Lars", "Sofia", "Mika", "Ava", "Ben", "Ida", "Felix", "Romy",
    "Oskar", "Maja", "Henri", "Lia", "Paul", "Mila", "Theo", "Luna",
    "Levi", "Eva", "Jakob", "Anna", "Samuel", "Laura", "David", "Emilia",
    "Leon", "Mira", "Niklas", "Ronja", "Anton", "Frieda", "Erik", "Johanna",
    "Moritz", "Hedi", "Adrian", "Juno", "Rafael", "Selma", "Mateo", "Ellen",
    "Valentin", "Carla", "Sebastian", "Alma", "Caspar", "Vera", "Linus", "Runa",
    "Julius", "Isra", "Aaron", "Thea", "Rohan",
]

DISPLAY_NAMES: Dict[str, str] = {}
DISPLAY_NAMES["default"] = _NAME_POOL[0]
for _i, _nm in enumerate(_NAME_POOL[1:], start=1):
    DISPLAY_NAMES[f"face{_i}"] = _nm


def display_name(slug: str) -> str:
    """Return a nice human name for a preset slug; falls back to title-cased slug."""
    if slug in DISPLAY_NAMES:
        return DISPLAY_NAMES[slug]
    return slug.replace("_", " ").replace("-", " ").title()


def list_presets_detailed() -> List[Dict[str, str]]:
    """List presets with both machine id + display name, sorted by name."""
    if not TARGETS_DIR.is_dir():
        return []
    stems = {p.stem for p in TARGETS_DIR.iterdir() if p.suffix.lower() in ACCEPTED_EXTS}
    out = [{"id": s, "name": display_name(s)} for s in sorted(stems)]
    return out


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
