"""Rendert je Slide-Vorlage einen Muster-Clip (6-10 s) zur Abnahme durch den Betreiber (Spec 5.6).

Aufruf: python -m spaces.marketing.scripts.slide_muster [Mandant] <Zielordner>

Der Sprech-Dienst wird nicht gerufen: die "Sprache" ist eine leise Sinusspur mit erfundenen Satzzeiten. Die drei
Layout-Varianten sind auf die sechs Muster verteilt. Neben jeder MP4 entsteht ein PNG-Standbild.
"""
from __future__ import annotations

import math
import struct
import subprocess
import sys
import tempfile
import time
import wave
from pathlib import Path

from spaces.marketing.claw import video_slides
from spaces.marketing.workers import video_rendern
from spaces.marketing.workers.video_stimme import Gesprochen, Satz

SATZ_DAUER = (2.4, 2.2, 2.6)       # erfundene Satzlaengen; dazwischen 0,15 s Pause

AGENDA = ["Übersicht und Lage", "Was sich lohnt", "Ökosystem und nächste Schritte"]
MUSTER = [
    ("titel", 0, {"bildart": "slide", "uebergang": "blende"}, [
        {"vorlage": "titel", "ab_satz": 0, "zeile": "KI im Mittelstand: vom Pilot zum Ergebnis",
         "unterzeile": "Was Entscheider jetzt wissen müssen"},
        {"vorlage": "zahl", "ab_satz": 2, "zahl": "3", "einheit": "Monate bis zum ersten Nutzen",
         "erklaerung": "wenn der Anwendungsfall klar umrissen ist"}]),
    ("stichpunkte", 1, {"bildart": "slide", "uebergang": "schnitt"}, [
        {"vorlage": "titel", "ab_satz": 0, "zeile": "Drei Fragen vor jedem KI-Projekt",
         "unterzeile": "Die Antworten bestimmen den Erfolg"},
        {"vorlage": "stichpunkte", "ab_satz": 1, "ueberschrift": "Worauf es ankommt",
         "punkte": ["Welches Geschäftsproblem lösen wir?", "Welche Daten haben wir wirklich?",
                    "Wer im Team übernimmt die Verantwortung?"]}]),
    ("zahl", 2, {"bildart": "slide", "uebergang": "blende"}, [
        {"vorlage": "titel", "ab_satz": 0, "zeile": "Wo KI heute schon Zeit spart",
         "unterzeile": "Beispiele aus der Beratungspraxis"},
        {"vorlage": "zahl", "ab_satz": 1, "zahl": "40 %", "einheit": "weniger Aufwand im Angebotswesen und in der Büroorganisation",
         "erklaerung": "gemessen über drei Monate im Vertrieb"}]),
    ("zitat", 0, {"bildart": "slide", "uebergang": "schnitt"}, [
        {"vorlage": "zitat", "ab_satz": 0, "zitat": "„Nicht die Technik scheitert, sondern die fehlende Entscheidung.“",
         "von": "Erfahrung aus über 40 Beratungsprojekten"},
        {"vorlage": "stichpunkte", "ab_satz": 2, "ueberschrift": "Was daraus folgt",
         "punkte": ["Maßnahmen zuerst priorisieren", "Dann erst Werkzeuge wählen"]}]),
    ("vergleich", 1, {"bildart": "slide", "uebergang": "blende"}, [
        {"vorlage": "vergleich", "ab_satz": 0,
         "links": {"titel": "Ohne klaren Plan", "punkte": ["Viele Einzelversuche", "Kein messbares Ziel", "Frust und Ärger im Team"]},
         "rechts": {"titel": "Mit klarem Plan", "punkte": ["Ein klarer Anwendungsfall", "Messbarer Nutzen", "Schnelle Akzeptanz"]}},
        {"vorlage": "titel", "ab_satz": 2, "zeile": "Der Unterschied ist die Vorbereitung",
         "unterzeile": "Nicht die Größe des Budgets"}]),
    ("agenda", 2, {"bildart": "slide", "uebergang": "schnitt"}, [
        {"vorlage": "agenda", "ab_satz": 0, "punkte": AGENDA, "aktiv": 1},
        {"vorlage": "agenda", "ab_satz": 2, "punkte": AGENDA, "aktiv": 2}]),
]


def stille_sprache(ordner: Path, dauern: tuple[float, ...] = SATZ_DAUER, pause: float = 0.15) -> Gesprochen:
    """Leise 220-Hz-Spur (etwa -40 dB) mit erfundenen Satzzeiten, ohne Sprech-Dienst."""
    rate, saetze, t = 48000, [], 0.0
    ordner.mkdir(parents=True, exist_ok=True)
    wav = ordner / "stimme.wav"
    with wave.open(str(wav), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        for i, d in enumerate(dauern):
            if i:
                w.writeframes(b"\x00\x00" * round(pause * rate))
                t += pause
            w.writeframes(b"".join(struct.pack("<h", int(300 * math.sin(2 * math.pi * 220 * k / rate)))
                                   for k in range(round(d * rate))))
            saetze.append(Satz(f"Satz {i + 1}.", round(t, 3), d))
            t += d
    return Gesprochen(saetze=saetze, engine="chatterbox", rueckfall=False, wav=wav, dauer_s=round(t, 3))


def standbild(clip: Path, png: Path, zeit_s: float) -> None:
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{zeit_s:.2f}", "-i", str(clip),
                    "-frames:v", "1", str(png)], check=True, stdin=subprocess.DEVNULL, timeout=60)


def main(argv: list[str]) -> int:
    if not argv or len(argv) > 2:
        print("Aufruf: python -m spaces.marketing.scripts.slide_muster [Mandant] <Zielordner>")
        return 2
    mandant, ziel = (argv[0], Path(argv[1])) if len(argv) == 2 else ("VibeMind", Path(argv[0]))
    if not video_rendern.eingerichtet():
        print("HyperFrames ist nicht installiert (npm ci in spaces/marketing/video_werkbank).")
        return 1
    ziel.mkdir(parents=True, exist_ok=True)
    marke = video_slides.marke_fuer(mandant)
    with tempfile.TemporaryDirectory(prefix="slide_muster_") as tmp:
        sprache = stille_sprache(Path(tmp))
        for name, variante, kopf, schritte in MUSTER:
            szene = {**kopf, "variante": variante, "bild": {"schritte": schritte}}
            clip = ziel / f"muster-{name}.mp4"
            start = time.monotonic()
            dauer = video_rendern.szene_rendern(szene, sprache, marke, Path(tmp) / f"szene-{name}", clip)
            sek = time.monotonic() - start
            plan = video_slides.zeitplan(szene["bild"], sprache.saetze, sprache.dauer_s)
            zeigt = next(s for s in reversed(plan) if s.vorlage == name)     # der Schritt, den das Muster vorfuehrt
            standbild(clip, ziel / f"muster-{name}.png", zeigt.start_s + zeigt.dauer_s - 0.5)   # alles eingeblendet
            print(f"{clip.name}: {dauer:.1f} s Clip, Render {sek:.1f} s, Variante {variante}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
