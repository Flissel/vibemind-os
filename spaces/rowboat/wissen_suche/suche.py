"""Suche im Rowboat-Wissen: nach Bedeutung (Index + Ollama am PC) oder per Stichwort als Rueckfall.

Das Werkzeug antwortet immer: Ist der PC aus, das Tailnet weg oder der Index leer, kommen
Stichwort-Treffer mit ``HINWEIS_STICHWORT``. Noch nicht eingebettete Notizen werden auch bei der
Bedeutungssuche per Stichwort ergaenzt (``HINWEIS_OFFEN``).
"""
from __future__ import annotations

import logging
import re
import time
import urllib.request
from pathlib import Path, PurePosixPath
from typing import Any, Callable

from .einbetten import Einbetter, EingabeAbgelehnt, PcNichtErreichbar
from .index import WissensIndex
from .indexer import datei_pfad, bestand_scannen

log = logging.getLogger(__name__)

HINWEIS_STICHWORT = ("Bedeutungssuche gerade nicht möglich (PC bzw. Ollama nicht erreichbar)"
                     " – das sind Treffer der Stichwortsuche.")
HINWEIS_OFFEN = "{n} Notizen sind noch nicht eingebettet und nur per Stichwort gefunden."
HINWEIS_ABGELEHNT = ("Bedeutungssuche für diese Frage nicht möglich (Ollama lehnt den Text ab)"
                     " – das sind Treffer der Stichwortsuche.")
HINWEIS_UNVOLLSTAENDIG = "Stichwortsuche aus Zeitgründen abgebrochen – Treffer unvollständig."

# Das ganze Werkzeug antwortet in weniger als 20 s: Frage einbetten (hoechstens 15 s, siehe
# OllamaEinbetter.gesamt_s) plus Stichwortsuche bis zu dieser Frist ab Beginn der Anfrage.
GESAMT_FRIST_S = 18.0
MIN_STICHWORT_S = 0.5

MAX_AUSZUG = 300
MAX_ANZAHL = 20
STICHWORT_ERGAENZUNG = 3

_STOPPWOERTER = frozenset("""
aber alle allem allen aller alles als also auch auf aus bei beim bin bis bist dann das dass dem den der
des dessen die dies diese diesem diesen dieser dieses doch dort durch ein eine einem einen einer eines
fuer gab gibt hab habe haben hat hatte hatten hier ihr ihre ihren ihrem ich ist jetzt kann kein keine
mit nach nicht noch nur oder ohne sehr sein seine sich sie sind ueber uns und unter vom von vor war waren
was welche welcher wenn wer wie wir wird wurde wurden zum zur zwischen etwas alle gibt
the and for with that this from are was were have has had not but you your our out who what which when
where how all any can will about into than then them they there their
""".split())

_FALTUNG = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"})


def falten(text: str) -> str:
    """Kleinschreibung und Umlaute ausgeschrieben, damit ``Uebergabe`` ``Übergabe`` findet."""
    return text.lower().translate(_FALTUNG)


def begriffe(frage: str) -> list[str]:
    gesehen: list[str] = []
    for wort in re.findall(r"\w+", falten(frage)):
        if len(wort) >= 3 and wort not in _STOPPWOERTER and wort not in gesehen:
            gesehen.append(wort)
    return gesehen


def _auszug(text: str, terme: list[str]) -> tuple[str, str]:
    """(Ueberschrift, Absatz) mit den meisten Begriffen; der Absatz hoechstens MAX_AUSZUG Zeichen."""
    bester, beste_punkte, beste_ueberschrift = "", -1, ""
    ueberschrift = ""
    for absatz in re.split(r"\n\s*\n", text):
        absatz = absatz.strip()
        if not absatz:
            continue
        kopf = re.match(r"^#{1,6}\s+(.+)$", absatz.splitlines()[0])
        if kopf:
            ueberschrift = kopf.group(1).strip()
        gefaltet = falten(absatz)
        punkte = sum(1 for t in terme if t in gefaltet)
        if punkte > beste_punkte:
            bester, beste_punkte, beste_ueberschrift = absatz, punkte, ueberschrift
    return beste_ueberschrift, _kuerzen(bester)


def _kuerzen(text: str) -> str:
    text = " ".join(text.split())
    return text if len(text) <= MAX_AUSZUG else text[: MAX_AUSZUG - 1].rstrip() + "…"


def _text_lesen(pfad: Path) -> str:
    return pfad.read_text(encoding="utf-8", errors="replace").lstrip("\ufeff")


def _stichwort(wurzel: Path, frage: str, anzahl: int, nur: set[str] | None,
               frist: float | None) -> tuple[list[dict], bool]:
    """Wie ``stichwort_suchen``; ``frist`` (time.monotonic) bricht den Lauf ab -> (Treffer, vollstaendig)."""
    terme = begriffe(frage)
    if not terme:
        return [], True
    treffer: list[dict] = []
    vollstaendig = True
    for rel in bestand_scannen(wurzel):
        if nur is not None and rel not in nur:
            continue
        if frist is not None and time.monotonic() > frist:
            vollstaendig = False
            log.warning("Stichwortsuche nach Frist abgebrochen (%d Treffer bis dahin)", len(treffer))
            break
        try:
            text = _text_lesen(datei_pfad(wurzel, rel))
        except OSError:
            continue
        titel = PurePosixPath(rel).stem
        name = falten(titel)
        gefaltet = falten(text)
        punkte = 3 * sum(1 for t in terme if t in name) + sum(min(gefaltet.count(t), 5) for t in terme)
        if punkte <= 0:
            continue
        abschnitt, auszug = _auszug(text, terme)
        treffer.append({"pfad": f"knowledge/{rel}", "titel": titel, "abschnitt": abschnitt or titel,
                        "auszug": auszug, "punkte": punkte, "quelle": "stichwort"})
    treffer.sort(key=lambda t: (-t["punkte"], t["pfad"]))
    return treffer[:anzahl], vollstaendig


def stichwort_suchen(wurzel: Path, frage: str, anzahl: int, nur: set[str] | None = None) -> list[dict]:
    """Punkte = 3 x Begriffe im Dateinamen + Summe aus min(Vorkommen, 5) je Begriff im Text."""
    return _stichwort(wurzel, frage, anzahl, nur, None)[0]


def _bedeutungs_treffer(index: WissensIndex, vektor: list[float], anzahl: int,
                        aktuell: set[str], reserve: int) -> list[dict]:
    """Index-Treffer nur fuer Dateien, deren eingebettete Fassung der Datei auf der Platte entspricht."""
    treffer = []
    for t in index.suchen(vektor, anzahl + reserve):
        if t.pfad not in aktuell:
            continue
        # Der Text beginnt mit "Titel / Ordner / Ueberschrift"; der Auszug zeigt den Inhalt dahinter.
        inhalt = t.text.split("\n\n", 1)[1] if "\n\n" in t.text else t.text
        treffer.append({"pfad": f"knowledge/{t.pfad}", "titel": PurePosixPath(t.pfad).stem,
                        "abschnitt": t.ueberschrift, "auszug": _kuerzen(inhalt),
                        "punkte": round(t.punkte, 4), "quelle": "bedeutung"})
        if len(treffer) >= anzahl:
            break
    return treffer


def _hinweise(*teile: str | None) -> str | None:
    gesetzt = [t for t in teile if t]
    return " ".join(gesetzt) if gesetzt else None


def wissen_suchen(frage: str, anzahl: int, index: WissensIndex, wurzel: Path, einbetter: Einbetter) -> dict:
    start = time.monotonic()
    frage = (frage or "").strip()
    if not frage:
        return {"fehler": "frage fehlt"}
    try:
        anzahl = int(anzahl)
    except (TypeError, ValueError):
        anzahl = 8
    anzahl = max(1, min(MAX_ANZAHL, anzahl))
    st = index.stand()
    stand = {"fertig": st.fertig, "offen": st.offen, "fehler": st.fehler}

    def frist() -> float:
        # Was vom Gesamtbudget nach dem Einbetten bleibt, mindestens MIN_STICHWORT_S.
        return max(start + GESAMT_FRIST_S, time.monotonic() + MIN_STICHWORT_S)

    vektor: list[float] | None = None
    rueckfall = HINWEIS_STICHWORT
    if st.abschnitte > 0:
        try:
            vektor = einbetter.frage(frage)
        except PcNichtErreichbar as exc:
            log.info("Bedeutungssuche nicht moeglich (%s), Stichwortsuche", exc)
        except EingabeAbgelehnt as exc:
            log.info("Frage abgelehnt (%s), Stichwortsuche", exc)
            rueckfall = HINWEIS_ABGELEHNT
    if vektor is None:
        treffer, vollstaendig = _stichwort(wurzel, frage, anzahl, None, frist())
        return {"modus": "stichwort",
                "hinweis": _hinweise(rueckfall, None if vollstaendig else HINWEIS_UNVOLLSTAENDIG),
                "treffer": treffer, "stand": stand}

    # Aktuell ist eine Datei nur, wenn sie 'fertig' ist UND Groesse/mtime noch zur Platte passen.
    # Alles andere - offen, fehler, seit dem letzten Abgleich geaendert oder dem Indexer noch
    # unbekannt - wird per Stichwort ergaenzt, damit kein veralteter Auszug erscheint.
    bestand = bestand_scannen(wurzel)
    dateien = index.dateien()
    fertig = {p for p, d in dateien.items() if d.status == "fertig"}
    aktuell = {p for p in fertig if bestand.get(p) == (dateien[p].groesse, dateien[p].mtime)}
    treffer = _bedeutungs_treffer(index, vektor, anzahl, aktuell, reserve=len(fertig - aktuell))
    nicht_eingebettet = set(bestand) - aktuell
    hinweis = None
    if nicht_eingebettet:
        ergaenzung, vollstaendig = _stichwort(wurzel, frage, STICHWORT_ERGAENZUNG, nicht_eingebettet, frist())
        gesehen = {t["pfad"] for t in treffer}
        treffer += [t for t in ergaenzung if t["pfad"] not in gesehen]
        hinweis = _hinweise(HINWEIS_OFFEN.format(n=len(nicht_eingebettet)),
                            None if vollstaendig else HINWEIS_UNVOLLSTAENDIG)
    return {"modus": "bedeutung", "hinweis": hinweis, "treffer": treffer, "stand": stand}


def wissen_stand(index: WissensIndex, einbetter_url: str,
                 oeffner: Callable[..., Any] = urllib.request.urlopen) -> dict:
    st = index.stand()
    try:
        with oeffner(f"{einbetter_url.rstrip('/')}/api/version", timeout=2) as antwort:
            erreichbar = getattr(antwort, "status", 200) == 200
    except Exception:  # noqa: BLE001 - jede Stoerung heisst: nicht erreichbar
        erreichbar = False
    return {"fertig": st.fertig, "offen": st.offen, "fehler": st.fehler, "abschnitte": st.abschnitte,
            "modell": st.modell, "pc_erreichbar": erreichbar, "letzte_runde": index.lesen("letzte_runde")}
