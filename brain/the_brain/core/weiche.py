"""Die Weiche im Schattenbetrieb (Routing-Papier 24.11.2026, Schritt 2, Entwurf A).

Je Satz bewertet die Weiche Kandidaten aus ALLEN Bereichen gegen die Fähigkeitskarten
(data/faehigkeitskarten.yaml): Einbettung über das vorhandene Modell
(core.qdrant_kg.Embedder), Kosinus gegen jeden Beispielsatz, höchster Wert je Fähigkeit,
Bonus für den Bereich des offenen Reiters, dann Schwelle plus Marge zwischen dem besten und
dem zweitbesten Bereich.

Sie entscheidet NICHTS. Der heutige Weg bleibt in Kraft; die Weiche schreibt nur auf, was
sie gewählt hätte, und ob das dem heutigen Weg entspricht:
  - in die Spur (Snapshot des PlanRecorders, Feld ``weiche``) und in
    ``data/weiche_schatten.jsonl`` (dauerhaft, Grundlage des Vergleichsberichts);
  - in Neo4j: (:Entscheidung)-[:SCHATTEN]->(:Weichenvorschlag)-[:KANDIDAT]->(:Bereich).
Nie Satztext: der Satz liegt nur im Speicher, bis er bewertet ist.

Nie aufhalten, nie scheitern: ``melden`` legt den Satz in eine begrenzte Warteschlange; ein
eigener Faden bewertet nach der Antwort. Volle Schlange, Fehler, zu alte Sätze und
Überschreitungen der Zeitgrenze werden gezählt und geschluckt. Die Zeitgrenze bricht die
Rechnung nicht ab (ein Faden lässt sich nicht abbrechen), sie verwirft das Ergebnis.

Kein Modellauftrag: nur das lokale Einbettungsmodell, kein Sprachmodell.

Schalter (Umgebung):
  WEICHE_SCHATTEN=0      alles aus
  WEICHE_SCHWELLE        Mindestpunkte des besten Bereichs (Vorgabe 0.68)
  WEICHE_MARGE           Mindestabstand zum zweitbesten Bereich (Vorgabe 0.04)
  WEICHE_BONUS           Aufschlag für den Bereich des Reiters (Vorgabe 0.05)
  WEICHE_ZEIT_S          Zeitgrenze je Satz (Vorgabe 1.0 s)
"""
from __future__ import annotations

import json
import logging
import os
import queue
import threading
import time
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

logger = logging.getLogger("brain.weiche")

_WURZEL = Path(__file__).resolve().parent.parent
KARTEN_PFAD = _WURZEL / "data" / "faehigkeitskarten.yaml"
SCHATTEN_PFAD = _WURZEL / "data" / "weiche_schatten.jsonl"

WARTESCHLANGE_MAX = 100
VERALTET_S = 30.0
KANDIDATEN = 5
NIRGENDS = "nirgends"


def _zahl(name: str, vorgabe: float, unten: float = 0.0, oben: float = 10.0) -> float:
    try:
        return min(oben, max(unten, float(os.environ.get(name, str(vorgabe)))))
    except ValueError:
        return vorgabe


def an() -> bool:
    return os.environ.get("WEICHE_SCHATTEN", "1") not in ("0", "false", "False")


def schwelle() -> float:
    return _zahl("WEICHE_SCHWELLE", 0.68, 0.0, 1.0)


def marge() -> float:
    return _zahl("WEICHE_MARGE", 0.04, 0.0, 1.0)


def bonus() -> float:
    return _zahl("WEICHE_BONUS", 0.05, 0.0, 0.5)


def zeitgrenze_s() -> float:
    return _zahl("WEICHE_ZEIT_S", 1.0, 0.05, 30.0)


# ── Karten ────────────────────────────────────────────────────────────


class Karten:
    """Die geladenen Fähigkeitskarten: je Beispielsatz (Bereich, Fähigkeit)."""

    def __init__(self, daten: Mapping[str, Any]) -> None:
        self.version = daten.get("version")
        self.beispiele: List[str] = []
        self.zuordnung: List[Tuple[str, str]] = []
        self.namen: Dict[str, str] = {}
        bereiche = daten.get("bereiche")
        if not isinstance(bereiche, Mapping) or not bereiche:
            raise ValueError("Karten ohne Bereiche")
        for kennung, bereich in bereiche.items():
            if not isinstance(bereich, Mapping):
                raise ValueError(f"Bereich {kennung!r} ist keine Abbildung")
            self.namen[str(kennung)] = str(bereich.get("name") or kennung)
            for faehigkeit in bereich.get("faehigkeiten") or []:
                fid = faehigkeit.get("id") if isinstance(faehigkeit, Mapping) else None
                if not isinstance(fid, str) or not fid:
                    raise ValueError(f"Fähigkeit ohne id in {kennung!r}")
                for satz in faehigkeit.get("beispiele") or []:
                    if isinstance(satz, str) and satz.strip():
                        self.beispiele.append(satz.strip())
                        self.zuordnung.append((str(kennung), fid))
        for satz in daten.get(NIRGENDS) or []:
            if isinstance(satz, str) and satz.strip():
                self.beispiele.append(satz.strip())
                self.zuordnung.append((NIRGENDS, NIRGENDS))
        if not any(b != NIRGENDS for b, _ in self.zuordnung):
            raise ValueError("Karten ohne Beispielsätze")

    @classmethod
    def laden(cls, pfad: Optional[Path] = None) -> "Karten":
        import yaml

        with Path(pfad or KARTEN_PFAD).open("r", encoding="utf-8") as f:
            return cls(yaml.safe_load(f) or {})

    def faehigkeiten(self) -> List[Tuple[str, str]]:
        """Alle (Bereich, Fähigkeit) ohne Dubletten, in Kartenreihenfolge."""
        return list(dict.fromkeys(z for z in self.zuordnung if z[0] != NIRGENDS))


# ── Bewerten ──────────────────────────────────────────────────────────


def _normiert(vektoren: Any) -> Any:
    import numpy as np

    m = np.asarray(vektoren, dtype=np.float32)
    if m.ndim == 1:
        m = m.reshape(1, -1)
    laengen = np.linalg.norm(m, axis=1, keepdims=True)
    laengen[laengen == 0] = 1.0
    return m / laengen


class Weiche:
    """Bewertet einen Satz gegen die Karten. Lädt das Modell erst beim ersten Satz."""

    def __init__(self, embedder: Any = None, karten: Optional[Karten] = None) -> None:
        self._embedder = embedder
        self._karten = karten
        self._matrix: Any = None
        self._lock = threading.Lock()

    def _vorbereiten(self) -> Tuple[Any, Karten, Any]:
        with self._lock:
            if self._karten is None:
                self._karten = Karten.laden()
            if self._embedder is None:
                from core.qdrant_kg import Embedder

                self._embedder = Embedder.get()
            if self._matrix is None:
                texte = self._karten.beispiele
                if hasattr(self._embedder, "encode_batch"):
                    vektoren = self._embedder.encode_batch(texte)
                else:
                    vektoren = [self._embedder.encode(t) for t in texte]
                self._matrix = _normiert(vektoren)
            return self._embedder, self._karten, self._matrix

    def aufwaermen(self) -> None:
        """Lädt Karten und Modell und bettet alle Beispiele ein (gehört nicht zur Zeitgrenze)."""
        self._vorbereiten()

    def bewerten(self, satz: str, reiter: Optional[str] = None) -> Dict[str, Any]:
        """Vorschlag ohne Satztext: Ergebnis, Regel, Kandidaten, Bereiche, Punkte."""
        embedder, karten, matrix = self._vorbereiten()
        t0 = time.monotonic()
        frage = _normiert(embedder.encode(satz))[0]
        aehnlich = matrix @ frage

        bester: Dict[Tuple[str, str], float] = {}
        nirgends_max = -1.0
        for (bereich, fid), wert in zip(karten.zuordnung, aehnlich.tolist()):
            if bereich == NIRGENDS:
                nirgends_max = max(nirgends_max, wert)
                continue
            if wert > bester.get((bereich, fid), -1.0):
                bester[(bereich, fid)] = wert

        aufschlag = bonus()
        punkte = {
            schluessel: wert + (aufschlag if reiter and schluessel[0] == reiter else 0.0)
            for schluessel, wert in bester.items()
        }
        rangfolge = sorted(punkte.items(), key=lambda kv: -kv[1])

        je_bereich: Dict[str, Tuple[float, str]] = {}
        for (bereich, fid), wert in rangfolge:
            if bereich not in je_bereich:
                je_bereich[bereich] = (wert, fid)
        bereiche = sorted(je_bereich.items(), key=lambda kv: -kv[1][0])

        oben_bereich, (oben_punkte, oben_faehigkeit) = bereiche[0]
        zweite_punkte = bereiche[1][1][0] if len(bereiche) > 1 else -1.0
        abstand = oben_punkte - zweite_punkte

        s, m = schwelle(), marge()
        if oben_punkte < s:
            ergebnis, regel = "nirgends", "unter_schwelle"
        elif nirgends_max >= oben_punkte:
            ergebnis, regel = "nirgends", "naeher_an_nirgends"
        elif abstand < m:
            ergebnis, regel = "rueckfrage", "marge_zu_klein"
        else:
            ergebnis, regel = "waehlt", "schwelle_und_marge"
        gewaehlt = ergebnis == "waehlt"

        return {
            "ergebnis": ergebnis,
            "regel": regel,
            "bereich": oben_bereich if gewaehlt else None,
            "faehigkeit": oben_faehigkeit if gewaehlt else None,
            "punkte": round(float(oben_punkte), 4),
            "abstand": round(float(abstand), 4),
            "nirgends_punkte": round(float(nirgends_max), 4),
            "schwelle": s,
            "marge": m,
            "bonus": aufschlag if reiter else 0.0,
            "reiter": reiter,
            "kandidaten": [
                {"bereich": b, "faehigkeit": f, "punkte": round(float(w), 4)}
                for (b, f), w in rangfolge[:KANDIDATEN]
            ],
            "bereiche": [
                {"bereich": b, "rang": i + 1, "punkte": round(float(w), 4), "faehigkeit": f,
                 "verworfen": not (gewaehlt and i == 0)}
                for i, (b, (w, f)) in enumerate(bereiche)
            ],
            "karten_version": karten.version,
            "rechen_ms": int((time.monotonic() - t0) * 1000),
        }


def gleich_wie_heute(vorschlag: Mapping[str, Any], heute_bereich: Optional[str]) -> Optional[bool]:
    """Hätte die Weiche dasselbe getan wie der heutige Weg?

    ``waehlt``: gleich, wenn der heutige Bereich derselbe ist. ``rueckfrage``/``nirgends``:
    gleich, wenn heute kein Bereich gegriffen hat. ``zeit``/``fehler``: ``None``.
    """
    ergebnis = vorschlag.get("ergebnis")
    if ergebnis == "waehlt":
        return heute_bereich == vorschlag.get("bereich")
    if ergebnis in ("rueckfrage", "nirgends"):
        return heute_bereich is None
    return None


# ── Schattenbetrieb ───────────────────────────────────────────────────


class Schatten:
    """Ein Faden, eine begrenzte Warteschlange. ``melden`` blockiert nie."""

    def __init__(self, weiche: Optional[Weiche] = None, pfad: Optional[Path] = None,
                 maxsize: int = WARTESCHLANGE_MAX) -> None:
        self.weiche = weiche or Weiche()
        self.pfad = Path(pfad or SCHATTEN_PFAD)
        self._schlange: "queue.Queue[Dict[str, Any]]" = queue.Queue(maxsize=maxsize)
        self.zaehler = {"gemeldet": 0, "bewertet": 0, "verworfen": 0, "veraltet": 0,
                        "zeit": 0, "fehler": 0, "graph_fehler": 0}
        self._faden: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._datei_lock = threading.Lock()

    def _starten(self) -> None:
        with self._lock:
            if self._faden is None or not self._faden.is_alive():
                self._faden = threading.Thread(target=self._schleife, name="WeicheSchatten", daemon=True)
                self._faden.start()

    def melden(self, satz: Any, eintrag: Optional[Mapping[str, Any]], *, graph: Any = None,
               recorder: Any = None) -> bool:
        """Nimmt einen Satz zur Bewertung an. True = angenommen. Wirft nie."""
        try:
            if not an() or not isinstance(satz, str) or not satz.strip():
                return False
            if not isinstance(eintrag, Mapping) or not eintrag.get("trace_id"):
                return False
            self._schlange.put_nowait({"satz": satz, "eintrag": dict(eintrag), "graph": graph,
                                       "recorder": recorder, "eingang": time.monotonic()})
        except queue.Full:
            self.zaehler["verworfen"] += 1
            return False
        except Exception as e:  # noqa: BLE001
            logger.debug(f"[weiche] melden übersprungen: {type(e).__name__}: {e}")
            return False
        self.zaehler["gemeldet"] += 1
        self._starten()
        return True

    def einen_bewerten(self, auftrag: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Bewertet einen Satz und schreibt den Vorschlag. Wirft nie."""
        eintrag = auftrag.get("eintrag") or {}
        trace_id = str(eintrag.get("trace_id") or "")
        vorschlag: Dict[str, Any]
        try:
            if time.monotonic() - float(auftrag.get("eingang") or 0.0) > VERALTET_S:
                self.zaehler["veraltet"] += 1
                return None
            self.weiche.aufwaermen()
            t0 = time.monotonic()
            vorschlag = self.weiche.bewerten(auftrag["satz"], eintrag.get("reiter"))
            dauer_ms = int((time.monotonic() - t0) * 1000)
            if dauer_ms > zeitgrenze_s() * 1000:
                self.zaehler["zeit"] += 1
                vorschlag = {"ergebnis": "zeit", "regel": "zeitgrenze", "bereich": None,
                             "faehigkeit": None, "kandidaten": [], "bereiche": []}
            vorschlag["dauer_ms"] = dauer_ms
        except Exception as e:  # noqa: BLE001
            self.zaehler["fehler"] += 1
            logger.debug(f"[weiche] Bewertung fehlgeschlagen: {type(e).__name__}: {e}")
            vorschlag = {"ergebnis": "fehler", "regel": type(e).__name__, "bereich": None,
                         "faehigkeit": None, "kandidaten": [], "bereiche": []}
        finally:
            auftrag["satz"] = None  # den Text nicht länger halten als nötig

        heute_bereich = eintrag.get("bereich")
        vorschlag.update({
            "trace_id": trace_id,
            "ts": time.time(),
            "heute": {"weg": eintrag.get("weg"), "stufe": eintrag.get("stufe"),
                      "bereich": heute_bereich, "ok": eintrag.get("ok")},
            "gleich_wie_heute": gleich_wie_heute(vorschlag, heute_bereich),
        })
        if vorschlag["ergebnis"] not in ("zeit", "fehler"):
            self.zaehler["bewertet"] += 1
        self._ablegen(vorschlag, auftrag.get("recorder"), auftrag.get("graph"))
        return vorschlag

    def _ablegen(self, vorschlag: Dict[str, Any], recorder: Any, graph: Any) -> None:
        try:
            if recorder is not None and hasattr(recorder, "attach_weiche"):
                recorder.attach_weiche(vorschlag["trace_id"], vorschlag)
        except Exception as e:  # noqa: BLE001
            logger.debug(f"[weiche] Spur übersprungen: {e}")
        try:
            with self._datei_lock:
                self.pfad.parent.mkdir(parents=True, exist_ok=True)
                with self.pfad.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(vorschlag, ensure_ascii=False, default=str) + "\n")
        except Exception as e:  # noqa: BLE001
            logger.debug(f"[weiche] Datei übersprungen: {e}")
        try:
            if graph is not None and hasattr(graph, "upsert_weichenvorschlag"):
                from core.entscheidung_graph import zeitgrenze_s as graph_zeit

                graph.upsert_weichenvorschlag(vorschlag, timeout_s=graph_zeit())
        except Exception as e:  # noqa: BLE001
            self.zaehler["graph_fehler"] += 1
            logger.debug(f"[weiche] Graph übersprungen: {type(e).__name__}: {e}")

    def _schleife(self) -> None:
        while True:
            auftrag = self._schlange.get()
            try:
                self.einen_bewerten(auftrag)
            finally:
                self._schlange.task_done()

    def warten(self, zeit_s: float = 2.0) -> bool:
        """Nur für Tests: wartet, bis die Schlange leer ist."""
        ende = time.time() + zeit_s
        while time.time() < ende:
            if self._schlange.unfinished_tasks == 0:
                return True
            time.sleep(0.01)
        return False


_SCHATTEN: Optional[Schatten] = None
_SCHATTEN_LOCK = threading.Lock()


def schatten() -> Schatten:
    global _SCHATTEN
    with _SCHATTEN_LOCK:
        if _SCHATTEN is None:
            _SCHATTEN = Schatten()
        return _SCHATTEN


def melden(satz: Any, eintrag: Optional[Mapping[str, Any]], *, graph: Any = None,
           recorder: Any = None) -> bool:
    """Meldet einen Satz an die Weiche. Wirft nie, blockiert nie, entscheidet nichts."""
    try:
        if not an():
            return False
        return schatten().melden(satz, eintrag, graph=graph, recorder=recorder)
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[weiche] melden übersprungen: {type(e).__name__}: {e}")
        return False


def karten_pruefen(weiche: Weiche) -> Dict[str, Any]:
    """Prüft die Karten gegen sich selbst, ohne neuen Satz einzubetten (ein-aus-allen).

    Jeder Beispielsatz wird gegen alle ANDEREN Beispiele bewertet: Landet er im eigenen
    Bereich? Zeigt, welche Bereiche sich überlappen, bevor die Weiche echte Sätze sieht.
    """
    import numpy as np

    _, karten, matrix = weiche._vorbereiten()
    aehnlich = matrix @ matrix.T
    np.fill_diagonal(aehnlich, -1.0)
    bereiche = [b for b, _ in karten.zuordnung]
    treffer: Counter = Counter()
    gesamt: Counter = Counter()
    verwechselt: Counter = Counter()
    for i, eigener in enumerate(bereiche):
        j = int(np.argmax(aehnlich[i]))
        geschaetzt = bereiche[j]
        gesamt[eigener] += 1
        if geschaetzt == eigener:
            treffer[eigener] += 1
        else:
            verwechselt[f"{eigener} -> {geschaetzt}"] += 1
    return {
        "beispiele": len(bereiche),
        "je_bereich": {b: {"treffer": treffer[b], "gesamt": n} for b, n in gesamt.items()},
        "verwechselt": dict(verwechselt.most_common()),
    }


# ── Vergleichsbericht ────────────────────────────────────────────────


def _zeilen(pfad: Path, seit_ts: Optional[float]) -> Iterable[Dict[str, Any]]:
    if not pfad.exists():
        return
    with pfad.open("r", encoding="utf-8") as f:
        for zeile in f:
            try:
                d = json.loads(zeile)
            except ValueError:
                continue
            if not isinstance(d, dict):
                continue
            if seit_ts is not None and float(d.get("ts") or 0.0) < seit_ts:
                continue
            yield d


def bericht(pfad: Optional[Path] = None, *, seit_ts: Optional[float] = None,
            beispiele: int = 10) -> Dict[str, Any]:
    """Wo weicht die Weiche vom heutigen Weg ab? Nur Zählungen und Kennungen, kein Satztext."""
    pfad = Path(pfad or SCHATTEN_PFAD)
    gesamt = 0
    ergebnisse: Counter = Counter()
    gleich: Counter = Counter()
    paare: Counter = Counter()
    je_stufe: Dict[str, Counter] = {}
    abweichungen: List[Dict[str, Any]] = []
    for d in _zeilen(pfad, seit_ts):
        gesamt += 1
        ergebnis = str(d.get("ergebnis") or "?")
        ergebnisse[ergebnis] += 1
        g = d.get("gleich_wie_heute")
        schluessel = "gleich" if g is True else "anders" if g is False else "offen"
        gleich[schluessel] += 1
        heute = d.get("heute") if isinstance(d.get("heute"), dict) else {}
        stufe = str(heute.get("stufe") or "?")
        je_stufe.setdefault(stufe, Counter())[schluessel] += 1
        if g is False:
            heute_b = heute.get("bereich") or "-"
            weiche_b = d.get("bereich") or ergebnis
            paare[f"{heute_b} -> {weiche_b}"] += 1
            abweichungen.append({
                "trace_id": d.get("trace_id"), "stufe": stufe, "heute": heute_b,
                "weiche": weiche_b, "faehigkeit": d.get("faehigkeit"),
                "punkte": d.get("punkte"), "abstand": d.get("abstand"), "regel": d.get("regel"),
            })
    return {
        "saetze": gesamt,
        "ergebnisse": dict(ergebnisse),
        "vergleich": dict(gleich),
        "je_stufe": {k: dict(v) for k, v in sorted(je_stufe.items(), key=lambda kv: -sum(kv[1].values()))},
        "abweichungen_paare": dict(paare.most_common()),
        "letzte_abweichungen": abweichungen[-beispiele:][::-1] if beispiele > 0 else [],
        "schalter": {"an": an(), "schwelle": schwelle(), "marge": marge(), "bonus": bonus(),
                     "zeit_s": zeitgrenze_s()},
        "zaehler": dict(_SCHATTEN.zaehler) if _SCHATTEN is not None else {},
    }


def bericht_text(daten: Mapping[str, Any]) -> str:
    """Der Bericht als kurzer Text (scripts/weiche_vergleich.py)."""
    zeilen = [f"Sätze mit Weichenvorschlag: {daten.get('saetze', 0)}"]
    ergebnisse = ", ".join(f"{k} {v}" for k, v in sorted((daten.get("ergebnisse") or {}).items()))
    zeilen.append("Ergebnis der Weiche: " + (ergebnisse or "-"))
    v = daten.get("vergleich") or {}
    zeilen.append(f"Wie heute: gleich {v.get('gleich', 0)}, anders {v.get('anders', 0)}, "
                  f"offen {v.get('offen', 0)}")
    zeilen.append("Je heutiger Stufe (gleich/anders/offen):")
    for stufe, z in (daten.get("je_stufe") or {}).items():
        zeilen.append(f"  {stufe:22} {z.get('gleich', 0):4} {z.get('anders', 0):4} {z.get('offen', 0):4}")
    paare: Sequence[Tuple[str, int]] = list((daten.get("abweichungen_paare") or {}).items())
    if paare:
        zeilen.append("Abweichungen (heute -> Weiche):")
        for paar, n in paare:
            zeilen.append(f"  {paar:34} {n}")
    return "\n".join(zeilen)
