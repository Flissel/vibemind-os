"""Mundstellung einer Zeichentrickfigur aus der Tonspur (26.11.2026). Fuer die Erklaervideos der Werkbank: die Figur
im Kreis (z.B. Tux) hat drei Bilder - Mund zu, halb offen, offen - und zeigt zu jedem Zeitpunkt das Bild, das zur
Lautstaerke passt. Das ist kein Lippenlesen, sondern das ruckige Sprechen des klassischen Zeichentricks: offen auf
den Silbengipfeln, halb dazwischen, zu in den Pausen.

Gelesen wird die WAV der gesprochenen Szene mit der Standardbibliothek (kein ffmpeg, kein Modell). Ausgegeben wird
CSS: je Mundbild eine Keyframe-Animation der Deckkraft mit harten Wechseln (die Slides der Werkbank bewegen sich nur
ueber CSS-Keyframes). Laesst sich der Ton nicht lesen, bleibt der Mund zu; es wird nie geworfen."""
from __future__ import annotations

import array
import math
import wave
from pathlib import Path

SCHRITT_S = 0.04            # Messfenster
ZU, HALB, OFFEN = 0, 1, 2
STILLE = 0.08               # unter diesem Anteil des lauten Pegels: Mund zu
GIPFEL = 1.05               # lauter als der Durchschnitt der Nachbarschaft: Mund offen
GIPFEL_MIN = 0.25
WECHSEL_MAX = 400           # mehr Wechsel je Szene schreibt die Animation nicht (Schutz vor riesigem CSS)


def lautstaerken(wav: Path, schritt_s: float = SCHRITT_S) -> list[float]:
    """Lautstaerke (RMS) je Messfenster; leer, wenn die Datei kein 16-Bit-PCM ist oder sich nicht lesen laesst."""
    try:
        with wave.open(str(wav), "rb") as w:
            if w.getsampwidth() != 2 or w.getnframes() == 0:
                return []
            kanaele, rate = w.getnchannels(), w.getframerate()
            werte = array.array("h")
            werte.frombytes(w.readframes(w.getnframes()))
    except (OSError, EOFError, wave.Error, ValueError):
        return []
    mono = werte[::kanaele] if kanaele > 1 else werte
    n = max(1, int(rate * schritt_s))
    return [math.sqrt(sum(x * x for x in mono[i:i + n]) / n) for i in range(0, len(mono) - n + 1, n)]


def stellungen(laut: list[float]) -> list[int]:
    """Je Messfenster ZU, HALB oder OFFEN. Einzelne Ausreisser (ein Fenster) gleichen sich dem Vorgaenger an."""
    if not laut:
        return []
    spitze = sorted(laut)[int(len(laut) * 0.95)] or 1.0
    roh = []
    for i, x in enumerate(laut):
        fenster = laut[max(0, i - 4):i + 4]
        mittel = sum(fenster) / len(fenster)
        if x < STILLE * spitze:
            roh.append(ZU)
        elif x >= GIPFEL * mittel and x >= GIPFEL_MIN * spitze:
            roh.append(OFFEN)
        else:
            roh.append(HALB)
    for i in range(1, len(roh) - 1):
        if roh[i] != roh[i - 1] and roh[i] != roh[i + 1]:
            roh[i] = roh[i - 1]
    return roh


def wechsel(wav: Path) -> list[tuple[float, int]]:
    """[(Zeit in s, Stellung)]: der erste Eintrag bei 0, danach nur die Zeitpunkte, an denen sich etwas aendert."""
    st = stellungen(lautstaerken(wav))
    if not st:
        return [(0.0, ZU)]
    aus = [(0.0, st[0])]
    aus += [(round(i * SCHRITT_S, 2), st[i]) for i in range(1, len(st)) if st[i] != st[i - 1]]
    return aus[:WECHSEL_MAX]


def keyframes(plan: list[tuple[float, int]], dauer_s: float) -> str:
    """CSS fuer die drei Mundbilder (.m0, .m1, .m2): je eine Animation `mund0..2` ueber die ganze Szene, die mit
    harten Wechseln (step-end) genau das Bild der aktuellen Stellung zeigt. Am Ende ist der Mund zu."""
    dauer_s = max(0.1, float(dauer_s))
    plan = [(max(0.0, min(float(t), dauer_s)), int(s)) for t, s in plan] or [(0.0, ZU)]
    if plan[0][0] > 0:
        plan.insert(0, (0.0, ZU))
    regeln = []
    for bild in (ZU, HALB, OFFEN):
        stopps: dict[str, int] = {}
        for zeit, stellung in plan:
            stopps[f"{zeit / dauer_s * 100:.3f}%"] = 1 if stellung == bild else 0
        stopps["100%"] = 1 if bild == ZU else 0
        inhalt = " ".join(f"{p} {{ opacity: {v}; }}" for p, v in stopps.items())
        regeln.append(f"@keyframes mund{bild} {{ {inhalt} }}")
        regeln.append(f".figur .m{bild} {{ animation: mund{bild} {dauer_s:.3f}s step-end both; }}")
    return "\n".join(regeln)
