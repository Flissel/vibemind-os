"""Tresor: liest und schreibt Wissensdokumente, nur nach bestandener Pruefung.

Regel fuer die Deutung beim Ueberschreiben:
  - neue Deutung nicht leer          -> neue gilt
  - neue leer, alte vorhanden        -> alte bleibt, WENN jeder Beleg, auf den
                                        sie sich stuetzt, im neuen Dokument
                                        denselben Wert hat; sonst verworfen
Dateien ohne YAML-Kopf (freie Rowboat-Notizen) werden nie angefasst.
Geschrieben wird atomar ueber eine .tmp-Datei und os.replace.
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

    def pfad(self, dok: Dokument) -> Path:
        return self.wurzel / ORDNER[dok.typ] / f"{dateiname(dok)}.md"

    def lesen_von(self, dok: Dokument) -> Optional[Dokument]:
        p = self.pfad(dok)
        if not p.exists():
            return None
        try:
            return lesen(p.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning("[tresor] %s unlesbar: %s", p, e)
            return None

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

    def schreiben(self, neu: Dokument) -> Tuple[bool, List[str]]:
        alt = self.lesen_von(neu)
        if not neu.deutung.strip() and alt is not None and alt.deutung.strip():
            if self._deutung_traegt(alt, neu):
                # Belegnummern der alten Deutung auf die neuen Nummern abbilden
                alt_b = {b.nr: (b.quelle, b.ziel, b.feld) for b in alt.belege}
                neu_nr = {(b.quelle, b.ziel, b.feld): b.nr for b in neu.belege}
                deutung = _REF.sub(lambda m: f"[B{neu_nr[alt_b[int(m.group(1))]]}]", alt.deutung)
                neu = neu.model_copy(update={"deutung": deutung})
        bekannt = self.bekannte_namen() | {dateiname(neu)}
        links = [l for l in neu.links if l in bekannt]
        neu = neu.model_copy(update={"links": links})
        probleme = pruefen(neu, bekannte_dokumente=bekannt)
        if probleme:
            return False, probleme
        p = self.pfad(neu)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".md.tmp")
        try:
            tmp.write_text(rendern(neu), encoding="utf-8")
            os.replace(tmp, p)
        except OSError as e:
            try:
                tmp.unlink()
            except OSError:
                pass
            return False, [f"Schreiben fehlgeschlagen: {e}"]
        return True, []
