"""CLIP-Messung fuer ueberarbeitete Newsletter-Bilder (sales-claw Spec
2026-09-30-newsletter-bild-ueberarbeiten-design.md §4.6), wie Laura: fastembed
Qdrant/clip-ViT-B-32 (512 dim, CPU). Laeuft NUR am PC im Bild-Arbeiter -
auf der VM laeuft kein Modell (Betreiber-Vorgabe). Eine gemessene Groesse,
keine Selbstauskunft eines Modells; bei jedem Fehler {} statt Absturz."""
from __future__ import annotations

import io
import os

import numpy as np

BILD_MODELL = "Qdrant/clip-ViT-B-32-vision"
TEXT_MODELL = "Qdrant/clip-ViT-B-32-text"
_GELADEN: tuple | None = None


def _modelle():
    global _GELADEN
    if _GELADEN is None:
        from fastembed import ImageEmbedding, TextEmbedding
        cache = os.environ.get("FASTEMBED_CACHE_PATH") or None
        _GELADEN = (ImageEmbedding(BILD_MODELL, cache_dir=cache), TextEmbedding(TEXT_MODELL, cache_dir=cache))
    return _GELADEN


def laeuft() -> bool:
    try:
        import fastembed  # noqa: F401
        return True
    except ImportError:
        return False


def _cos(a, b) -> float:
    a, b = np.asarray(a, dtype=np.float64), np.asarray(b, dtype=np.float64)
    n = float(np.linalg.norm(a) * np.linalg.norm(b))
    return float(a @ b / n) if n else 0.0


def _bild(daten: bytes):
    from PIL import Image
    with Image.open(io.BytesIO(daten)) as b:
        return b.convert("RGB").copy()


def messen(alt: bytes | None, neu: bytes, hinweis: str) -> dict:
    if alt is None:
        return {"aehnlich_original": None, "naeher_am_hinweis": None}
    try:
        bilder, texte = _modelle()
        v_alt, v_neu = list(bilder.embed([_bild(alt), _bild(neu)]))
        aehnlich = round(_cos(v_alt, v_neu), 4)
        richtung = None
        if (hinweis or "").strip():
            [v_text] = list(texte.embed([hinweis.strip()[:300]]))
            richtung = round(_cos(v_text, v_neu) - _cos(v_text, v_alt), 4)
        return {"aehnlich_original": aehnlich, "naeher_am_hinweis": richtung}
    except Exception:  # noqa: BLE001 - Messung darf die Erzeugung nie kippen
        return {}
