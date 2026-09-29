"""Tresor: liest und schreibt Wissensdokumente, nur nach bestandener Pruefung.

Regel fuer die Deutung beim Ueberschreiben (Fix-Runde 1, Finding 1):
  - alte Deutung vorhanden, nicht leer, UND von den neuen Belegen weiterhin
    getragen (jeder Beleg, auf den sie sich stuetzt, hat im neuen Dokument
    denselben Wert)                  -> alte bleibt fuehrend, auch wenn die
                                        neue Deutung nicht leer ist. Ein
                                        LLM-Durchlauf ueberschreibt eine noch
                                        gueltige Deutung (von Hand oder vom
                                        LLM) also NICHT einfach bei jedem Lauf.
  - sonst (keine alte Deutung, alte leer, oder nicht mehr getragen)
                                     -> neue Deutung gilt (auch wenn leer)
`Tresor.deutung_noch_gueltig(neu)` prueft denselben Fall vorab, damit der
Kurator (Task 7) den LLM-Aufruf sparen kann, wenn die bestehende Deutung
ohnehin fuehrend bliebe.
Dateien ohne YAML-Kopf (freie Rowboat-Notizen) werden nie angefasst.
Geschrieben wird atomar ueber eine versteckte .tmp-Datei (fuehrender Punkt,
damit kein Watcher sie fuer ein Dokument haelt) und os.replace.
"""
from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import List, Optional, Set, Tuple

from core.knowledge.schema import ORDNER, Dokument, dateiname, lesen, pruefen, rendern

logger = logging.getLogger(__name__)
KNOWLEDGE_DIR = os.environ.get("KNOWLEDGE_DIR") or str(Path.home() / ".rowboat" / "knowledge")
_REF = re.compile(r"\[B(\d+)\]")


class Tresor:
    def __init__(self, wurzel: Optional[Path] = None):
        self.wurzel = Path(wurzel or KNOWLEDGE_DIR)
        self.letzter_status: str = ""
        self.zuletzt_geschrieben: Optional[Dokument] = None

    def pfad(self, dok: Dokument) -> Path:
        return self.wurzel / ORDNER[dok.typ] / f"{dateiname(dok)}.md"

    def laden(self, dok: Dokument) -> Tuple[bool, Optional[Dokument]]:
        """(existiert, dokument). existiert=True mit dokument=None heisst:
        Datei da, aber nicht parsebar (Schlusspruefung I3)."""
        p = self.pfad(dok)
        if not p.exists():
            return False, None
        try:
            return True, lesen(p.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning("[tresor] %s unlesbar: %s", p, e)
            return True, None

    def lesen_von(self, dok: Dokument) -> Optional[Dokument]:
        return self.laden(dok)[1]

    def alle(self) -> List[Dokument]:
        out = []
        for ordner in sorted(set(ORDNER.values())):
            for p in sorted((self.wurzel / ordner).glob("*.md")):
                try:
                    out.append(lesen(p.read_text(encoding="utf-8")))
                except Exception:
                    continue  # freie Notiz ohne Kopf
        return out

    def bekannte_namen(self) -> Set[str]:
        return {dateiname(d) for d in self.alle()}

    @staticmethod
    def _deutung_traegt(alt: Dokument, neu: Dokument) -> bool:
        alt_b = {b.nr: b for b in alt.belege}
        neu_b = {(b.quelle, b.ziel, b.feld): b.wert for b in neu.belege}
        for n in {int(x) for x in _REF.findall(alt.deutung)}:
            b = alt_b.get(n)
            if b is None or neu_b.get((b.quelle, b.ziel, b.feld)) != b.wert:
                return False
        return True

    def deutung_noch_gueltig(self, neu: Dokument,
                             geladen: Optional[Tuple[bool, Optional[Dokument]]] = None) -> bool:
        """True, wenn eine vorhandene, nicht leere Deutung des gespeicherten
        Dokuments von den Belegen aus `neu` weiterhin getragen wird und damit
        bei `schreiben(neu)` fuehrend bliebe. Der Kurator (Task 7) nutzt das,
        um in diesem Fall den LLM-Aufruf zu sparen. `geladen` wie bei
        `schreiben` (Datei nur einmal lesen)."""
        alt = (geladen if geladen is not None else self.laden(neu))[1]
        return bool(alt is not None and alt.deutung.strip() and self._deutung_traegt(alt, neu))

    @staticmethod
    def _inhalt(d: Dokument) -> tuple:
        """Was ein Dokument inhaltlich ausmacht - ohne `stand`/`gemessen`
        (Schlusspruefung I1): Titel, Fakten-Werte, Beleg-(quelle, ziel, feld,
        wert), Deutung und Links."""
        return (d.titel,
                tuple((f.schluessel, f.wert, f.beleg) for f in d.fakten),
                tuple((b.nr, b.quelle, b.ziel, b.feld, b.wert) for b in d.belege),
                d.deutung.strip(), tuple(d.links))

    def schreiben(self, neu: Dokument, bekannte: Optional[Set[str]] = None,
                  geladen: Optional[Tuple[bool, Optional[Dokument]]] = None) -> Tuple[bool, List[str]]:
        """Prueft und schreibt `neu`. Schlusspruefung I1/I3:
          - `bekannte`: Menge bekannter Dokumentnamen; wird sie uebergeben,
            liest `schreiben` NICHT alle Dokumente neu ein (der Kurator
            ermittelt sie einmal pro Lauf).
          - `geladen`: Ergebnis von `laden(neu)`, damit der Kurator die Datei
            nur einmal liest (fuer `deutung_noch_gueltig` und hier).
          - Datei vorhanden, aber unlesbar -> NICHT schreiben (Handbearbeitung
            oder von Rowboat umformatierter Kopf bleibt byte-gleich).
          - Inhalt gleich dem gespeicherten (nur stand/gemessen anders) ->
            NICHT schreiben, Rueckgabe (True, []).
        `letzter_status` sagt danach, was passiert ist: geschrieben,
        unveraendert, abgelehnt oder unlesbar; `zuletzt_geschrieben` ist das
        endgueltige Dokument (fuer den Index, ohne erneutes Lesen)."""
        existiert, alt = geladen if geladen is not None else self.laden(neu)
        self.zuletzt_geschrieben = None
        if existiert and alt is None:
            self.letzter_status = "unlesbar"
            return False, [f"Datei vorhanden, aber unlesbar - Handbearbeitung? "
                           f"nicht ueberschrieben: {self.pfad(neu)}"]
        if alt is not None and alt.deutung.strip() and self._deutung_traegt(alt, neu):
            # Alte Deutung bleibt fuehrend -- auch wenn die neue nicht leer
            # ist: nur ein veraenderter Belegstand darf eine noch gueltige
            # Deutung ersetzen, nicht ein neuer LLM-Durchlauf allein.
            # Belegnummern der alten Deutung auf die neuen Nummern abbilden.
            alt_b = {b.nr: (b.quelle, b.ziel, b.feld) for b in alt.belege}
            neu_nr = {(b.quelle, b.ziel, b.feld): b.nr for b in neu.belege}
            deutung = _REF.sub(lambda m: f"[B{neu_nr[alt_b[int(m.group(1))]]}]", alt.deutung)
            neu = neu.model_copy(update={"deutung": deutung})
        bekannt = (self.bekannte_namen() if bekannte is None else set(bekannte)) | {dateiname(neu)}
        links = [l for l in neu.links if l in bekannt]
        neu = neu.model_copy(update={"links": links})
        probleme = pruefen(neu, bekannte_dokumente=bekannt)
        if probleme:
            self.letzter_status = "abgelehnt"
            return False, probleme
        if alt is not None and self._inhalt(alt) == self._inhalt(neu):
            self.letzter_status = "unveraendert"
            self.zuletzt_geschrieben = alt
            return True, []
        p = self.pfad(neu)
        tmp = p.parent / f".{p.name}.tmp"
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(rendern(neu), encoding="utf-8")
            os.replace(tmp, p)
        except OSError as e:
            try:
                tmp.unlink()
            except OSError:
                pass
            self.letzter_status = "abgelehnt"
            return False, [f"Schreiben fehlgeschlagen: {e}"]
        self.letzter_status = "geschrieben"
        self.zuletzt_geschrieben = neu
        return True, []
