"""Was ist zu sehen? Das Sehmodell beschreibt ein vorhandenes Newsletter-Bild
(Spec 2026-09-30 §4.3). Nur am PC (Ollama); num_ctx 4096 - mit Ollamas
Standardkontext verlangte qwen2.5vl:7b 37,6 GB RAM (gemessen 30.09.). Sichtbare
Schrift wird aus der Beschreibung entfernt, damit FLUX sie nicht neu malt."""
from __future__ import annotations

import base64
import re

from spaces.marketing.claw import bild_prompt

FRAGE = ("Describe what is visible in this image in 2-3 short English sentences: "
         "subject, setting, colors, light, and any visible text or logos.")
_ZITAT = re.compile(r"[\"“”„'][^\"“”„']{1,60}[\"“”„']")
_TEXTSATZ = re.compile(r"[^.]*\b(visible text|text reads|lettering|logo|sign reads|written)\b[^.]*\.?", re.IGNORECASE)


def ohne_schrift(text: str) -> str:
    ohne = _TEXTSATZ.sub("", _ZITAT.sub("", text or ""))
    return re.sub(r"\s{2,}", " ", ohne).strip()


def beschreiben(bild: bytes, zeitlimit: int = 180) -> str:
    try:
        antwort = bild_prompt._ollama("/api/generate", {
            "model": bild_prompt.SEH_MODELL, "prompt": FRAGE,
            "images": [base64.b64encode(bild).decode("ascii")], "stream": False, "keep_alive": 0,
            "options": {"num_ctx": bild_prompt.NUM_CTX, "temperature": 0}}, zeitlimit=zeitlimit)
    except Exception:  # noqa: BLE001 - ohne Beschreibung geht es weiter (Spec §7)
        return ""
    return ohne_schrift(" ".join(str(antwort.get("response") or "").split()))[:600]
