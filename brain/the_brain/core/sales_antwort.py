"""Antwortsaetze und Sprungziel fuer die festen Sales-Aufrufe (core/sales_saetze).

Regeln: ``docs/operations/sales-space/ANTWORTSAETZE.md``, Testfaelle in
``antwortsaetze-faelle.json`` (wortgleich). Ein Satz nennt nur die gekuerzte Auftragsnummer, die
Art als festes Wort, den Stand und Zahlen; nie Namen, Titel, Links, Suchtexte oder Gruende.
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, Optional, Tuple

GRENZE = 10 ** 9

# Ergebnisfelder je Art laut Vertrag; alle Zahlen ganze Zahlen 0..GRENZE.
_ZAHLEN = {
    "termine_heute": ("anzahl", "anzahl_kunden", "quellen_stumm"),
    "freigaben_zaehlen": ("wartend", "fehlgeschlagen"),
    "post_offen": ("chats_unbeantwortet", "mails_offen"),
    "kontakt_finden": ("anzahl",),
}
_TREFFER_FELDER = frozenset({"kontakt_id", "titel", "link"})

_LAEUFT = {
    "termine_heute": "Ich schaue in den Kalender (Auftrag {nr}).",
    "freigaben_zaehlen": "Ich schaue in die Freigaben (Auftrag {nr}).",
    "post_offen": "Ich schaue in die Post (Auftrag {nr}).",
    "kontakt_finden": "Ich suche (Auftrag {nr}).",
}
_FEHLER = {
    "termine_heute": "Die Termine konnte ich nicht lesen (Auftrag {nr}).",
    "freigaben_zaehlen": "Die Freigaben konnte ich nicht lesen (Auftrag {nr}).",
    "post_offen": "Die Post konnte ich nicht lesen (Auftrag {nr}).",
    "kontakt_finden": "Die Suche ist fehlgeschlagen (Auftrag {nr}).",
}
GESPERRT = {
    "termine_heute": "Das ist noch nicht freigeschaltet. Die Termine von heute siehst du in der "
                     "Sales-Oberfläche unter Kalender.",
    "freigaben_zaehlen": "Das ist noch nicht freigeschaltet. Die Entwürfe siehst du in der "
                         "Sales-Oberfläche unter Freigaben.",
    "post_offen": "Das ist noch nicht freigeschaltet. Die offene Post siehst du in der "
                  "Sales-Oberfläche unter Posteingang.",
    "kontakt_finden": "Das ist noch nicht freigeschaltet. Kontakte findest du in der "
                      "Sales-Oberfläche unter Kontakte.",
}
FORM = "Die Antwort von Sales passt nicht zur Form (Auftrag {nr})."
TAGESGRENZE = "Für heute sind keine weiteren Aufträge an Sales möglich. Geändert habe ich nichts."
NICHT_ERREICHBAR = "Das Auftragsbuch antwortet gerade nicht. Geändert habe ich nichts."

# Feste Ablehnungstexte des Auftragsbuchs (spaces/auftragsbuch/db.py ABLEHNUNGSTEXTE, werkzeuge).
_GESPERRT_TEXTE = ("Unbekannte Auftragsart", "nicht gefunden")
_GRENZE_TEXT = "Tagesgrenze erreicht"

# Seite der Sales-Oberflaeche je Art (Pfade in SALES_UI_PATHS der App).
_PFAD = {
    "termine_heute": "/kalender",
    "freigaben_zaehlen": "/freigaben",
    "post_offen": "/posteingang",
    "kontakt_finden": "/kontakte",
}
_KENNUNG = re.compile(r"^[0-9a-fA-F-]{8,64}$")


def art_von(capability: Any) -> str:
    return capability[6:] if isinstance(capability, str) and capability.startswith("sales.") else ""


def nummer(auftrag_id: Any) -> str:
    """Die ersten 8 Zeichen einer Kennung, sonst ``""``."""
    return auftrag_id[:8] if isinstance(auftrag_id, str) and _KENNUNG.match(auftrag_id) else ""


def _zahl(wert: Any) -> Optional[int]:
    if isinstance(wert, bool) or not isinstance(wert, int) or wert < 0 or wert > GRENZE:
        return None
    return wert


def form_ok(art: str, ergebnis: Any) -> bool:
    """Gleiche Pruefung wie im Vertrag: genau die Felder der Art, Zahlen ganz und ab 0."""
    if art not in _ZAHLEN or not isinstance(ergebnis, dict):
        return False
    erwartet = set(_ZAHLEN[art]) | ({"treffer"} if art == "kontakt_finden" else set())
    if set(ergebnis) != erwartet:
        return False
    if any(_zahl(ergebnis[f]) is None for f in _ZAHLEN[art]):
        return False
    if art == "kontakt_finden":
        treffer = ergebnis["treffer"]
        if not isinstance(treffer, list):
            return False
        for t in treffer:
            if not isinstance(t, dict) or set(t) != _TREFFER_FELDER:
                return False
            if not all(isinstance(t[f], str) for f in _TREFFER_FELDER):
                return False
    return True


def _mz(n: int, eins: str, mehr: str) -> str:
    return eins if n == 1 else mehr


def _fertig(art: str, e: Dict[str, Any]) -> str:
    if art == "termine_heute":
        n, k, s = e["anzahl"], e["anzahl_kunden"], e["quellen_stumm"]
        teile = ["Heute steht kein Termin an." if n == 0 else
                 f"Heute {_mz(n, 'steht', 'stehen')} {n} {_mz(n, 'Termin', 'Termine')} an."]
        if k > 0:
            teile.append(f"{k} {_mz(k, 'Kundentermin ist', 'Kundentermine sind')} für heute eingetragen.")
        if s > 0:
            teile.append(f"{s} Kalender {_mz(s, 'antwortet', 'antworten')} gerade nicht, es können mehr sein.")
        return " ".join(teile)
    if art == "freigaben_zaehlen":
        w, f = e["wartend"], e["fehlgeschlagen"]
        teile = ["In den Freigaben wartet nichts." if w == 0 else
                 f"In den Freigaben {_mz(w, 'wartet', 'warten')} {w} {_mz(w, 'Entwurf', 'Entwürfe')}."]
        if f > 0:
            teile.append(f"{f} {_mz(f, 'Entwurf ist', 'Entwürfe sind')} fehlgeschlagen.")
        return " ".join(teile)
    if art == "post_offen":
        c, m = e["chats_unbeantwortet"], e["mails_offen"]
        if c == 0 and m == 0:
            return "Keine Post ist offen."
        return (f"{c} {_mz(c, 'Chat ist', 'Chats sind')} unbeantwortet, "
                f"{m} {_mz(m, 'Mail ist', 'Mails sind')} offen.")
    n = e["anzahl"]
    if n == 0:
        return "Kein Kontakt gefunden."
    return f"{n} {_mz(n, 'Kontakt', 'Kontakte')} gefunden."


def satz(art: str, stand: str, auftrag_id: Any, ergebnis: Any = None) -> str:
    """Der Satz zum Stand eines Sales-Auftrags (ANTWORTSAETZE.md). Wirft nie."""
    nr = nummer(auftrag_id) or "?"
    if stand in ("offen", "in_arbeit"):
        return _LAEUFT.get(art, "Auftrag {nr} läuft.").format(nr=nr)
    if stand in ("fehler", "abgelaufen"):
        return _FEHLER.get(art, "Auftrag {nr} ist fehlgeschlagen.").format(nr=nr)
    if stand == "fertig":
        if art not in _ZAHLEN:
            return f"Auftrag {nr} ist fertig."
        if not form_ok(art, ergebnis):
            return FORM.format(nr=nr)
        return _fertig(art, ergebnis)
    return f"Den Stand von Auftrag {nr} kann ich nicht lesen."


def _text_aus(wert: Any) -> Tuple[Any, str]:
    """(JSON-Inhalt, Klartext) aus der Antwort eines MCP-Werkzeugs."""
    if isinstance(wert, str):
        try:
            return json.loads(wert), wert
        except (json.JSONDecodeError, ValueError):
            return None, wert
    if isinstance(wert, dict):
        inhalt = wert.get("content")
        if isinstance(inhalt, list) and len(inhalt) == 1 and isinstance(inhalt[0], dict):
            text = inhalt[0].get("text")
            if isinstance(text, str):
                return _text_aus(text)
        return wert, ""
    return None, ""


# Was das Auftragsbuch bei isError=true als Text liefert (spaces/auftragsbuch/mcp_server._fehlertext):
# nur diese festen Saetze kommen durch, bei "Eingabe passt nicht zum Vertrag" ohne Feldpfad.
_AUFTRAGSBUCH_FEST = (
    "Unbekannte Auftragsart", "Auftrag braucht Laden und Mandant", "Frist ausserhalb 1 bis 120 Minuten",
    "Plan-Kennung ungueltig", "Tagesgrenze erreicht: hoechstens 50 Proben pro Tag",
    "Tagesgrenze erreicht: hoechstens 60 Auftraege des Gehirns pro Tag", "nicht gefunden", "interner Fehler",
)
_AUFTRAGSBUCH_EINGABE = "Eingabe passt nicht zum Vertrag"


def ablehnung_aus_ergebnis(ergebnis: Any) -> str:
    """Fester Ablehnungstext des Auftragsbuchs aus einer MCP-Antwort mit isError, sonst ein Code.

    Fuer ``capability_targets``: dort wird daraus die Fehlermeldung des Schritts. Freier Text
    (er koennte eine Eingabe enthalten) kommt nie durch.
    """
    _, text = _text_aus(ergebnis)
    text = text.strip()
    if text in _AUFTRAGSBUCH_FEST:
        return text
    if text.startswith(_AUFTRAGSBUCH_EINGABE):
        return _AUFTRAGSBUCH_EINGABE
    return "Auftragsbuch: isError=true"


def quittung(ergebnis: Any) -> Optional[Dict[str, Any]]:
    """Die Quittung des Anlegens (``{"auftrag_id", "an": "sales", "art", "stand"}``) oder ``None``."""
    inhalt, _ = _text_aus(ergebnis)
    if (isinstance(inhalt, dict) and inhalt.get("an") == "sales" and nummer(inhalt.get("auftrag_id"))
            and isinstance(inhalt.get("art"), str)):
        return inhalt
    return None


def gelesen(roh: Any) -> Optional[Dict[str, Any]]:
    """Fester Satz und Sprungziel aus der rohen Antwort von ``auftrag_lesen`` eines Sales-Auftrags.

    Laeuft im MCP-Ausfuehrer, bevor die Antwort gekuerzt wird (``capability_targets``); danach sind
    die Treffer nur noch eine Anzahl. ``None`` fuer andere Empfaenger oder Arten. Wirft nie.
    """
    try:
        inhalt, _ = _text_aus(roh)
        if not isinstance(inhalt, dict) or inhalt.get("an") != "sales":
            return None
        art = inhalt.get("art")
        if not isinstance(art, str) or art not in _ZAHLEN:
            return None
        stand = inhalt.get("stand")
        return {"satz": satz(art, stand if isinstance(stand, str) else "", inhalt.get("auftrag_id"),
                             inhalt.get("ergebnis")),
                "ziel": ziel(art, None)}
    except Exception:  # noqa: BLE001
        return None


def antwort_fuer_aufruf(capability: str, ok: bool, ergebnis: Any, fehler: Any) -> Dict[str, Any]:
    """Antwortfelder nach dem festen Aufruf: ``final_text``, ``space``, ``ok`` und ``ziel``/``auftrag``.

    Ein Fehler des Auftragsbuchs nennt nur einen festen Satz, nie den Werkzeugtext.
    """
    art = art_von(capability)
    if art == "auftrag_lesen":
        # Der Ausfuehrer hat die Antwort schon gekuerzt und den festen Satz angehaengt (gelesen).
        inhalt, _ = _text_aus(ergebnis) if ok else (None, "")
        if isinstance(inhalt, dict) and isinstance(inhalt.get("satz"), str):
            z = inhalt.get("ziel")
            return {"ok": True, "space": "sales", "final_text": inhalt["satz"],
                    "ziel": z if isinstance(z, dict) else {"space": "sales", "pfad": "/"}}
        return {"ok": False, "space": "sales", "final_text": NICHT_ERREICHBAR}
    q = quittung(ergebnis) if ok else None
    if q is not None and q.get("art") == art:
        return {"ok": True, "space": "sales", "final_text": satz(art, str(q.get("stand") or "offen"),
                                                                 q["auftrag_id"]),
                "auftrag": {"id": q["auftrag_id"], "art": art, "stand": q.get("stand") or "offen"},
                "ziel": ziel(art, None)}
    _, text = _text_aus(ergebnis)
    roh = " ".join(str(x) for x in (text, fehler or "") if x)
    if _GRENZE_TEXT in roh:
        final = TAGESGRENZE
    elif any(t in roh for t in _GESPERRT_TEXTE):
        final = GESPERRT.get(art, NICHT_ERREICHBAR)
    else:
        final = NICHT_ERREICHBAR
    return {"ok": False, "space": "sales", "final_text": final}


def ziel(art: str, ergebnis: Any) -> Dict[str, Any]:
    """Sprungziel der App: Space ``sales`` und die Seite der Art.

    ``kontakt_finden`` mit genau einem Treffer springt auf dessen Seite (nur der Pfad aus der
    Kennung, nie Titel oder Link aus dem Ergebnis).
    """
    pfad = _PFAD.get(art, "/")
    if art == "kontakt_finden" and form_ok(art, ergebnis) and len(ergebnis["treffer"]) == 1:
        kennung = ergebnis["treffer"][0]["kontakt_id"]
        if _KENNUNG.match(kennung):
            pfad = f"/kontakte/{kennung}"
    return {"space": "sales", "pfad": pfad}
