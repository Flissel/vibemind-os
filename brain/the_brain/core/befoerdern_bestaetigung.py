"""Befördern nie direkt (Freigabe des Betreibers 24.11.2026, befoerdern-uebergabe.md).

Der Satz "Befördere diese Bubble" liest zuerst die Bubble und prüft sie mit den gemeinsamen
Kartenregeln (spaces/_contract/kartenregeln.py, dieselbe Datei wie das Werkzeug und die
Software-Design-Ansicht). Fehlt etwas, sagt die Antwort was ("fehlt: 2 leere Karten, …") und
nichts ist offen. Sonst fragt sie: "Daraus wird ein Projekt mit N Karten und dem White Paper
von <Datum>. Befördern?" Erst "Ja" (oder "Ja, befördern") löst den festen Aufruf aus, und nur:
- binnen ``GILT_S`` Sekunden nach genau dieser Rückfrage,
- wenn dazwischen kein anderer Satz kam (jeder andere Satz verwirft sie).

Der bestätigte Plan trägt einen Schlüssel, den der Plan-Ausführer genau einmal einlöst, nur
für die Bubble der Rückfrage. Ein Beförderungs-Schritt ohne gültigen Schlüssel (z. B. vom
Planer) läuft nicht. Das Werkzeug prüft beim Schreiben noch einmal (die Bubble kann sich in
der Zwischenzeit geändert haben). Befördert wird unter der Kennung der Änderung des Plans:
"Zurück" nimmt Projekt und Verknüpfung zusammen zurück.
"""
from __future__ import annotations

import importlib.util
import re
import secrets
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, Mapping, Optional, Sequence

GILT_S = 120
SCHLUESSEL_GILT_S = 120
EREIGNIS = "bubble.promote"

# Pruefung 25.11.2026: kopiert als  Ja, erledigen“.  ging die feste Form nicht und lief 16 s ins
# Modell. Bestaetigungen gelten auch mit umgebenden Anfuehrungszeichen und Satzzeichen.
_RAND_VORN = r"""^[\s"'„“”‚‘’«»]*"""
_RAND_HINTEN = r"""[\s.!?…"'„“”‚‘’«»]*$"""
_JA = re.compile(_RAND_VORN + r"ja\s*[,!.]?\s*(?:bitte\s*)?(?:bef(?:ö|oe)rder(?:e|n)?(?:\s+(?:sie|es|die\s+bubble))?)?"
                 r"\s*(?:bitte)?" + _RAND_HINTEN, re.IGNORECASE)
OHNE_FRAGE = ("Es ist gerade kein Befördern zur Bestätigung offen. "
              "Sag „Befördere diese Bubble“, dann prüfe ich sie und frage zuerst nach.")
OHNE_SCHLUESSEL = ("Befördert wird nur nach Rückfrage. "
                   "Sag „Befördere diese Bubble“, dann prüfe ich sie und frage zuerst nach.")
ABGELEHNT = "Gut, ich befördere nichts."
NICHT_LESBAR = "Ich kann die Bubble gerade nicht lesen; befördert ist nichts."
REGELN_FEHLEN = "Die Prüfregeln fürs Befördern fehlen hier; befördert ist nichts."

_sperre = threading.Lock()
_offen: Dict[str, Any] = {}                  # {"argumente", "antwort", "bubble_id", "bis"}
_schluessel: Dict[str, Dict[str, Any]] = {}  # Schlüssel -> {"bubble_id", "bis"}
_REGELN: Any = None


def _jetzt() -> float:
    return time.time()


def ist_bestaetigung(satz: Any) -> bool:
    return isinstance(satz, str) and bool(_JA.match(satz))


def _ist_nein(satz: Any) -> bool:
    try:
        from . import hoeflichkeit
        return hoeflichkeit.art(satz) == "nein"
    except Exception:  # noqa: BLE001
        return False


# ── die gemeinsamen Kartenregeln ─────────────────────────────────────────────

def _regeln_pfad() -> Optional[Path]:
    """spaces/_contract/kartenregeln.py: im Container unter /app/spaces, im Repo zwei Ebenen höher."""
    hier = Path(__file__).resolve()
    for basis in hier.parents:
        pfad = basis / "spaces" / "_contract" / "kartenregeln.py"
        if pfad.is_file():
            return pfad
    return None


def regeln() -> Any:
    """Das Modul mit ``sinnpruefung`` (einmal geladen), sonst ``None``."""
    global _REGELN
    if _REGELN is None:
        pfad = _regeln_pfad()
        if pfad is None:
            return None
        spec = importlib.util.spec_from_file_location("vibemind_kartenregeln", pfad)
        if spec is None or spec.loader is None:
            return None
        modul = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(modul)
        _REGELN = modul
    return _REGELN


# ── Prüfen und Rückfrage (nur lesen) ─────────────────────────────────────────

def bewertung(bubble: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    """Regel F2b aus den gemeinsamen Kartenregeln (eine 0 ohne Begründung ist keine Bewertung)."""
    modul = regeln()
    return modul.bewertung_der_bubble(bubble) if modul is not None else None


def _mehrzahl(anzahl: int, eins: str, viele: str) -> str:
    return f"eine {eins}" if anzahl == 1 else f"{anzahl} {viele}"


_GRUND_TEXT: Dict[str, Callable[[int], str]] = {
    "keine_karten": lambda n: "keine Karten",
    "leere_karten": lambda n: _mehrzahl(n, "leere Karte", "leere Karten"),
    "lose_karten": lambda n: _mehrzahl(n, "lose Karte", "lose Karten"),
    "keine_bewertung": lambda n: "keine Bewertung",
    "kein_white_paper": lambda n: "kein White Paper",
}


def fehlt_satz(gruende: Sequence[str]) -> str:
    """``["leere_karten:2", "lose_karten:1", "keine_bewertung"]`` → "fehlt: 2 leere Karten, …"."""
    teile = []
    for grund in gruende:
        name, _, zahl = str(grund).partition(":")
        anzahl = int(zahl) if zahl.isdigit() else 0
        teile.append(_GRUND_TEXT.get(name, lambda n, g=name: g)(anzahl))
    return "fehlt: " + ", ".join(teile)


def _datum(wert: Any) -> str:
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", str(wert or ""))
    return f"{int(m.group(3))}.{int(m.group(2))}.{m.group(1)}" if m else "unbekanntem Datum"


SCHWELLE_VORGABE = 95
SCHWELLE_EINSTELLUNG = "VIBEMIND_BEFOERDERN_SCHWELLE"


def schwelle_einstellung() -> int:
    """Die Schwelle fürs Befördern (Einstellung, 0 bis 100), sonst die Vorgabe 95."""
    import os
    wert = str(os.environ.get(SCHWELLE_EINSTELLUNG, "")).strip()
    return int(wert) if wert.isdigit() and 0 <= int(wert) <= 100 else SCHWELLE_VORGABE


# Nur fuer Tests: "1" laesst auch einen Lauf von brain-ideas befoerdern. Vorgabe aus; im Betrieb
# nicht setzen (Entscheidung des Betreibers: befoerdert wird erst mit MiroFish).
JEDE_QUELLE_EINSTELLUNG = "VIBEMIND_TEST_BEFOERDERN_JEDE_QUELLE"
QUELLE_SATZ = "Befördert wird erst, wenn MiroFish bewertet hat; die jetzige Bewertung dient dem Ausbauen."


def jede_quelle_einstellung() -> bool:
    import os
    return str(os.environ.get(JEDE_QUELLE_EINSTELLUNG, "")).strip() == "1"


def schwelle_satz(schwelle: Mapping[str, Any], modul: Any = None) -> str:
    """Deutscher Satz zur Schwelle: Score, Schwelle und die drei To-dos mit dem größten Gewinn."""
    grenze = schwelle.get("schwelle")
    grund = schwelle.get("grund")
    if grund == "kein_lauf":
        return (f"Es gibt noch keine Bewertung (Befördern ab {grenze} Punkten). "
                "Sag „Bewerte diese Bubble“.")
    score = schwelle.get("score")
    if grund == "gekuerzt":
        return (f"Die Bewertung steht bei {score} von 100 Punkten, ist aber gekürzt: der Agent sah nicht die ganze "
                "Bubble. Befördert wird nur mit einer ungekürzten Bewertung; für einen Auftrag ist die Bubble "
                "zu groß. Fasse Karten zusammen und sag dann „Bewerte diese Bubble“.")
    if grund == "quelle":
        satz = f"Die Bewertung steht bei {score} von 100 Punkten. {QUELLE_SATZ}"
    elif grund == "veraltet":
        satz = (f"Die letzte Bewertung ({score} Punkte) ist veraltet, die Karten haben sich seitdem geändert. "
                "Sag „Bewerte diese Bubble“.")
    else:
        satz = f"Die Bewertung steht bei {score} von 100 Punkten, befördert wird ab {grenze}."
    if schwelle.get("gekuerzt"):
        satz += " Sie ist außerdem gekürzt; fürs Befördern zählt nur eine ungekürzte Bewertung."
    if grund != "quelle" and schwelle.get("quelle") not in (None, "mirofish"):
        satz += " " + QUELLE_SATZ
    top = [t for t in schwelle.get("top") or [] if isinstance(t, Mapping)]
    if top:
        satz += " Am meisten bringen: " + "; ".join(
            f"To-do {t.get('nr')} „{t.get('titel')}“ (+{t.get('punkte')})" for t in top) + "."
    return satz


def pruefen(bubble: Mapping[str, Any], karten: Sequence[Mapping[str, Any]],
            kanten: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """``{"bestaetigbar": bool, "satz": str, "pruefung": {...}}``; wirft nicht bei fehlenden Regeln."""
    modul = regeln()
    if modul is None:
        return {"bestaetigbar": False, "satz": REGELN_FEHLEN, "pruefung": None}
    if bubble.get("promoted_to_project_id"):
        return {"bestaetigbar": False, "satz": "Diese Bubble ist schon ein Projekt.", "pruefung": None}
    pruefung = modul.sinnpruefung(list(karten), list(kanten), bewertung(bubble))
    # Stufe 1 (25.11.2026): nur ab der Schwelle des letzten echten Bewertungslaufs, und nur wenn
    # sich die Karten seitdem nicht geändert haben. Ein Handwert ("mit N") zählt nicht.
    schwelle = modul.schwelle_pruefen(bubble, list(karten), list(kanten), schwelle_einstellung(),
                                      jede_quelle=jede_quelle_einstellung())
    pruefung["schwelle"] = schwelle
    if not pruefung.get("bestanden") or not schwelle.get("bestanden"):
        teile = []
        if not pruefung.get("bestanden"):
            teile.append(fehlt_satz(pruefung.get("gruende") or []) + ".")
        if not schwelle.get("bestanden"):
            teile.append(schwelle_satz(schwelle, modul))
        return {"bestaetigbar": False, "satz": " ".join(teile) + " Befördert ist nichts.", "pruefung": pruefung}
    anzahl = sum(1 for k in karten if modul.ist_notiz(k))
    papiere = sorted((k for k in karten if modul.ist_white_paper(k)), key=lambda k: str(k.get("updated_at") or ""))
    papier = (f"dem White Paper vom {_datum(papiere[-1].get('updated_at'))}" if papiere
              else "ohne White Paper")
    karten_text = "einer Karte" if anzahl == 1 else f"{anzahl} Karten"
    return {"bestaetigbar": True, "pruefung": pruefung,
            "satz": f"Daraus wird ein Projekt mit {karten_text} und {papier}. Befördern? Sag „Ja“."}


MAX_KARTEN = 1000  # wie UEBERGABE_MAX_KARTEN im Werkzeug


def lesen_und_pruefen(bubble_id: str, lesen: Optional[Callable[[str, dict], Optional[list]]] = None) -> Dict[str, Any]:
    """Bubble, Karten und Verbindungen lesen (nur GET) und ``pruefen``. Wirft nie."""
    try:
        if lesen is None:
            from .ideen_rueckweg import _lesen as lesen
        zeilen = lesen("ideas", {"select": "id,title,score,metadata,promoted_to_project_id",
                                 "id": f"eq.{bubble_id}", "parent_id": "is.null", "limit": "1"})
        if not zeilen or not isinstance(zeilen[0], dict):
            return {"bestaetigbar": False, "satz": NICHT_LESBAR, "pruefung": None}
        # Den Text der Karte liest sinnpruefung selbst (kartentext: content, summary, content_json),
        # genau wie das Werkzeug beim Befördern.
        karten = lesen("canvas_nodes", {"select": "id,node_type,title,content,summary,content_json,metadata,updated_at",
                                        "linked_idea_id": f"eq.{bubble_id}", "order": "id.asc",
                                        "limit": str(MAX_KARTEN + 1)})
        if not isinstance(karten, list):
            return {"bestaetigbar": False, "satz": NICHT_LESBAR, "pruefung": None}
        if len(karten) > MAX_KARTEN:
            return {"bestaetigbar": False, "pruefung": None,
                    "satz": f"Befördern geht bis {MAX_KARTEN} Karten; diese Bubble hat mehr. Befördert ist nichts."}
        karten = [k for k in karten if isinstance(k, dict)]
        ids = [str(k.get("id")) for k in karten if k.get("id")]
        kanten: list = []
        for start in range(0, len(ids), 100):
            teil = lesen("canvas_edges", {"select": "from_node_id,to_node_id,edge_type",
                                          "from_node_id": f"in.({','.join(ids[start:start + 100])})"})
            if not isinstance(teil, list):
                return {"bestaetigbar": False, "satz": NICHT_LESBAR, "pruefung": None}
            kanten += [k for k in teil if isinstance(k, dict) and k.get("to_node_id") in set(ids)]
        return pruefen(zeilen[0], karten, kanten)
    except Exception:  # noqa: BLE001
        return {"bestaetigbar": False, "satz": NICHT_LESBAR, "pruefung": None}


# ── offen halten, bestätigen, einlösen ───────────────────────────────────────

def fragen(argumente: Dict[str, Any], antwort: str) -> None:
    """Die Rückfrage ist gestellt; nur "Ja" direkt danach löst genau diesen Aufruf aus."""
    with _sperre:
        _offen.clear()
        _offen.update({"argumente": dict(argumente), "antwort": antwort,
                       "bubble_id": str(argumente.get("bubble_id") or ""), "bis": _jetzt() + GILT_S})


def verwerfen() -> None:
    with _sperre:
        _offen.clear()


def satz_pruefen(satz: Any) -> Optional[Dict[str, Any]]:
    """Für jeden Satz vor allem anderen aufrufen.

    ``None``: keine Bestätigung; eine offene Rückfrage verfällt (gebunden an genau sie).
    ``{"ereignis", "argumente", "antwort", "bubble_id"}``: bestätigt, Argumente mit Schlüssel.
    ``{"satz": ...}``: "Ja, befördern" ohne gültige Rückfrage, nichts befördert.
    Ein blosses "Ja" ohne offene Rückfrage gehört nicht hierher (``None``): dafür gibt es die
    feste Antwort auf Höflichkeiten (core/hoeflichkeit).
    """
    jetzt = _jetzt()
    with _sperre:
        offen = dict(_offen)
        _offen.clear()
        if not ist_bestaetigung(satz):
            # "nein" auf die offene Rueckfrage schliesst sie mit einem festen Satz (Pruefung 25.11.).
            if offen and offen.get("bis", 0) >= jetzt and _ist_nein(satz):
                return {"satz": ABGELEHNT, "abgelehnt": True}
            return None
        if not offen or offen.get("bis", 0) < jetzt:
            if re.search(r"bef(?:ö|oe)rder", str(satz), re.IGNORECASE):
                return {"satz": OHNE_FRAGE}
            return None
        schluessel = secrets.token_hex(8)
        for alt in [s for s, eintrag in _schluessel.items() if eintrag["bis"] < jetzt]:
            _schluessel.pop(alt, None)
        _schluessel[schluessel] = {"bubble_id": offen["bubble_id"], "bis": jetzt + SCHLUESSEL_GILT_S}
    return {"ereignis": EREIGNIS, "antwort": offen["antwort"], "bubble_id": offen["bubble_id"],
            "argumente": {**offen["argumente"], "bestaetigung": schluessel}}


def einloesen(schluessel: Any, bubble_id: Any) -> bool:
    """Genau einmal wahr, und nur für die Bubble der Rückfrage."""
    if not isinstance(schluessel, str):
        return False
    with _sperre:
        eintrag = _schluessel.pop(schluessel, None)
    return eintrag is not None and eintrag["bis"] >= _jetzt() and eintrag["bubble_id"] == bubble_id
