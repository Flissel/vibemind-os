"""Bewertung einer Bubble als fester Weg und das Abarbeiten ihrer To-dos (Stufe 1, 25.11.2026).

Vorher (bis ec98a0c6) bewertete MiroFish die Bubble mit fuenf Prompts; die Punkte landeten als
Score aus feasibility/impact/novelty/urgency, die offenen Punkte als Block in der Beschreibung.
Ein Schluesselfehler ("roarboot" statt "rowboat") liess die fuenfte Sicht immer bei 0/25.

Jetzt, nach dem Muster von ``ideen_dokument`` (der Agent bekommt nur Daten, das Gehirn schreibt):

* **Bewerten** ("Bewerte diese Bubble", Rueckweg ``bewerten``): genau ein Lauf an brain-ideas mit
  dem vollen Kartentext (``kartentext`` der gemeinsamen Kartenregeln), den Verbindungen und dem
  White Paper. Fuenf Sichten zu 0-25 Punkten (``kartenregeln.SICHTEN``), je Sicht hoechstens fuenf
  To-dos mit Titel, einem Satz was fehlt, Karten, geschaetztem Gewinn und Art. Das Gehirn prueft
  streng, setzt fuer Kuerzel die Kennungen ein und schreibt ueber ``idea.bewertung_speichern``
  (Ideen-Server ``bubble_bewertung``): ``metadata.bewertung_lauf``, ``eval_history``, ``ideas.score``.
  Der Server schreibt nicht, wenn sich die Karten seit dem Lesen geaendert haben (``kartenstand``).
* **To-do erledigen** ("Erledige To-do 3", nach "Ja, erledigen", Rueckweg ``todo``): ein Lauf an
  brain-ideas mit dem To-do und den Karten; der Agent liefert hoechstens fuenf Kartenaenderungen
  (ergaenzen, fuellen, neue Karte mit Verbindung, verbinden). Das Gehirn schreibt sie ueber die
  festen Aufrufe (``idea.update``, ``idea.create`` mit ``verbinden_mit``) und hakt das To-do ab
  (``idea.todo_abhaken``), alles unter dem Folgeplan: "Zurueck" nimmt es zusammen zurueck.
  To-dos der Art ``white_paper`` und ``entscheidung`` gehen nicht an das Modell.

Kosten: ein Abo-Aufruf an brain-ideas je Bewertung und je erledigtem To-do.
Kennungen und Ziele kommen nur aus dem Auftrag, nie aus dem Text des Agenten.
"""
from __future__ import annotations

import hashlib
import re
from typing import Any, Callable, Mapping, Optional

from .befoerdern_bestaetigung import regeln
from .ideen_dokument import (
    Lesen, _kartenblock, _lage, _ohne_zaun, _REGELN, _schluss, ist_gruppen_kopf, kuerzel_ersetzen, titel_von,
)
from .ideen_fest_contract import feste_argumente, ist_kennung
from .ideen_rueckweg import (
    _HTML, _STEUER, _WERKZEUG, VERWEIGERT, FreigabeAusstehend, RueckwegFehler, _sauber, ein_json_objekt,
    ist_verweigerung,
)

VERSION = 1
# Groesse (25.11.2026): OpenFang nimmt je Nachricht hoechstens 64 KB (Bytes, nicht Zeichen;
# openfang-api routes.rs MAX_MESSAGE_SIZE) und antwortet darueber mit 413. Eine echte Bewertung mit
# 120 Karten scheiterte so. Jetzt wird die ganze Nachricht in UTF-8-Bytes gemessen und vor dem
# Senden gekuerzt: erst das White Paper, dann gleichmaessig der Text langer Karten (je Karte bis
# KARTE_MIN), zuletzt Verbindungen. Passt es dann noch nicht, geht kein Auftrag hinaus.
NACHRICHT_BYTES = 56 * 1024   # Reserve fuer den Rahmen, den der Ausfuehrer um die Nachricht legt
KARTE_MIN = 300
WHITE_PAPER_ZEICHEN = 40_000
WHITE_PAPER_GEKUERZT = 8_000
KANTEN_MAX = 3_000
ZU_GROSS = ("Diese Bubble ist für einen Bewertungsauftrag zu groß, auch gekürzt; es wurde nichts angelegt. "
            "Fasse Karten zusammen oder teile die Bubble.")
TODO_KARTE_ZEICHEN = 6_000      # betroffene Karten eines To-dos, voller Text bis hier
TODO_NACHBAR_ZEICHEN = 160      # andere Karten: Titel und Anfang
TODO_NACHBARN_MAX = 200
TODO_KARTE_MIN = 600
TODO_ZU_GROSS = ("Das To-do ist für einen Auftrag zu groß, auch gekürzt; es wurde nichts angelegt. "
                 "Erledige es von Hand und sag dann „To-do N erledigt“.")
AENDERUNGEN_MAX = 5
AENDERUNG_TEXT = 20_000
NEUE_KARTE_TITEL = 200
VERBINDEN_MAX = 10

ART_TEXT = {"karte_ergaenzen": "Karte ergänzen", "karte_fuellen": "Karte füllen", "verbindung": "Verbindung",
            "white_paper": "White Paper", "entscheidung": "Entscheidung"}
SICHT_PRUEFT = {
    "coding": "Technik: Schnittstellen (z. B. API-Endpunkte), Technikwahl, Datenmodelle, Fehlerbehandlung.",
    "swe_design": "Entwurf: Nutzergeschichten, Abnahmekriterien, Umfang (was gehört dazu, was nicht), "
                  "Abhängigkeiten.",
    "research": "Recherche: Quellen und Belege, Wettbewerb und Vergleich, offene Fragen klar benannt.",
    "rowboat": "Wissenslage: festgehaltenes Vorwissen, Dokumentation, vorhandene Muster und wiederverwendbare "
               "Bausteine, belegte Annahmen, erklärte Begriffe.",
    "ideas": "Ideen: Vollständigkeit der Unterpunkte, Tiefe, Verknüpfung der Karten untereinander, Lücken.",
}

_FORM = "Der Agent hat nicht in der vereinbarten Form geantwortet; es wurde nichts geändert."
AUSBAU_SATZ = "Diese Bewertung dient dem Ausbauen; befördert wird erst, wenn MiroFish bewertet hat."
HERKUNFT_SATZ = ("Die Bewertung ist nicht gespeichert: der Rückweg-Schlüssel fehlt oder passt nicht "
                 "(Einrichtung von Gehirn und Ideen-Server). Geändert ist nichts.")
_KUERZEL = re.compile(r"^\[?(K\d{1,4})\]?$")


def _regeln() -> Any:
    modul = regeln()
    if modul is None:
        raise RueckwegFehler("Die Bewertungsregeln fehlen hier; es wurde nichts angelegt.")
    return modul


def _ganz(wert: Any, von: int, bis: int) -> Optional[int]:
    """Ganze Zahl im Bereich (auch 7.0), sonst ``None``."""
    if isinstance(wert, bool):
        return None
    if isinstance(wert, float) and wert.is_integer():
        wert = int(wert)
    return wert if isinstance(wert, int) and von <= wert <= bis else None


def _text(wert: Any, grenze: int) -> Optional[str]:
    """Bereinigter, nicht leerer Text ohne HTML und Werkzeugaufruf, sonst ``None``."""
    if not isinstance(wert, str) or not wert.strip() or _HTML.search(wert) or _WERKZEUG.search(wert):
        return None
    return _sauber(re.sub(r"[ \t]+", " ", wert).strip(), grenze)


def _inhalt_stand(karte: Mapping[str, Any]) -> str:
    """Kurzer Stand einer Karte (Titel und Text), um Aenderungen seit dem Anlegen zu erkennen."""
    roh = "\x1f".join(str(karte.get(f) or "") for f in ("title", "content", "summary"))
    return hashlib.sha256(roh.encode("utf-8")).hexdigest()[:16]


def groesse(nachricht: str) -> int:
    """Groesse einer Nachricht so, wie OpenFang sie misst: UTF-8-Bytes."""
    return len(nachricht.encode("utf-8"))


def _groesste_grenze(passt: Callable[[int], bool], unten: int, oben: int) -> Optional[int]:
    """Groesste Grenze in [unten, oben], bei der ``passt`` gilt (``passt`` waechst nicht mit der Grenze)."""
    if not passt(unten):
        return None
    while unten < oben:
        mitte = (unten + oben + 1) // 2
        if passt(mitte):
            unten = mitte
        else:
            oben = mitte - 1
    return unten


def kuerzung_satz(kuerzung: Optional[Mapping[str, Any]]) -> str:
    """Ehrlicher Satz, wie viel der Bewertung fehlte; leer, wenn nichts gekuerzt wurde."""
    if not isinstance(kuerzung, Mapping) or not kuerzung.get("gekuerzt"):
        return ""
    teile = []
    if kuerzung.get("karten_gekuerzt"):
        teile.append(f"bei {kuerzung['karten_gekuerzt']} von {kuerzung.get('karten')} Karten nur der Anfang "
                     f"(je bis {kuerzung.get('karte_zeichen')} Zeichen)")
    if kuerzung.get("kanten_weggelassen"):
        teile.append(f"{kuerzung['kanten_weggelassen']} von {kuerzung.get('kanten')} Verbindungen fehlen")
    if kuerzung.get("white_paper_gekuerzt"):
        teile.append("das White Paper nur gekürzt")
    return ("Gekürzt bewertet: " + ", ".join(teile) + ". Fürs Befördern zählt nur eine ungekürzte Bewertung."
            if teile else "")


def _alle_zeilen(bubble_id: str, lesen: Lesen) -> list[dict[str, Any]]:
    zeilen = lesen.karten_der_bubble(bubble_id)
    if zeilen is None:
        raise RueckwegFehler("Die Karten lassen sich gerade nicht lesen. Versuch es gleich noch einmal.")
    return [dict(k) for k in zeilen if isinstance(k, Mapping) and isinstance(k.get("id"), str)
            and k.get("linked_idea_id") == bubble_id]


def _kanten(ids: list[str], lesen: Lesen) -> list[dict[str, Any]]:
    kanten = lesen.kanten(ids) if ids else []
    if kanten is None:
        raise RueckwegFehler("Die Verbindungen lassen sich gerade nicht lesen. Versuch es gleich noch einmal.")
    return [dict(k) for k in kanten if isinstance(k, Mapping)]


# ── Bewerten: Anlegen ────────────────────────────────────────────────────

def bewerten_vorbereiten(event_id: str, bubble: Mapping[str, Any], lesen: Lesen) -> tuple[str, dict[str, Any]]:
    """(Nachricht an den Agenten, Rueckweg) fuer einen Bewertungslauf, oder ``RueckwegFehler``."""
    modul = _regeln()
    bubble_id, bubble_titel = str(bubble["id"]), titel_von(bubble)
    zeilen = _alle_zeilen(bubble_id, lesen)
    kanten = _kanten([z["id"] for z in zeilen], lesen)
    stand = modul.kartenstand(zeilen, kanten)
    notizen = sorted((z for z in zeilen if modul.ist_notiz(z)), key=_lage)
    if not notizen:
        raise RueckwegFehler("In dieser Bubble gibt es keine Karten; es gibt nichts zu bewerten.")
    texte, kuerzel, kuerzel_titel = [], {}, {}
    for n, karte in enumerate(notizen, start=1):
        k = f"K{n}"
        texte.append((k, titel_von(karte), modul.kartentext(karte)))
        kuerzel[k], kuerzel_titel[k] = karte["id"], titel_von(karte)
    nach_id = {i: k for k, i in kuerzel.items()}
    alle_kanten = sorted({f"{nach_id[e['from_node_id']]} -> {nach_id[e['to_node_id']]} ({e.get('edge_type') or 'related'})"
                          for e in kanten if e.get("from_node_id") in nach_id and e.get("to_node_id") in nach_id})
    papiere = sorted((z for z in zeilen if modul.ist_white_paper(z)), key=lambda z: str(z.get("updated_at") or ""))
    papier_voll = modul.kartentext(papiere[-1]) if papiere else ""
    nachricht, kuerzung = bewerten_einpassen(bubble_titel, texte, alle_kanten, papier_voll)
    rueckweg = {
        "version": VERSION, "vertrag": "bewerten", "ereignis": event_id, "bubble_id": bubble_id,
        "titel": _sauber(f"Bewertung: {bubble_titel}", 200), "kuerzel": kuerzel, "kuerzel_titel": kuerzel_titel,
        "kartenstand": stand, "gekuerzt": kuerzung["gekuerzt"], "kuerzung": kuerzung,
    }
    return nachricht, rueckweg


def bewerten_einpassen(bubble_titel: str, texte: list[tuple[str, str, str]], alle_kanten: list[str],
                       papier_voll: str, grenze: int = NACHRICHT_BYTES) -> tuple[str, dict[str, Any]]:
    """(Nachricht unter ``grenze`` Bytes, Kuerzung) oder ``RueckwegFehler(ZU_GROSS)``.

    Reihenfolge: alles voll; sonst White Paper auf ``WHITE_PAPER_GEKUERZT``; sonst die groesste
    gemeinsame Grenze je Karte (mindestens ``KARTE_MIN``); sonst zusaetzlich so wenige Verbindungen
    weglassen wie noetig. Die Kuerzung zaehlt ehrlich mit, was fehlt.
    """
    kanten = alle_kanten[:KANTEN_MAX]
    papier = _sauber(papier_voll, WHITE_PAPER_ZEICHEN) if len(papier_voll) > WHITE_PAPER_ZEICHEN else papier_voll
    laengste = max((len(t) for _, _, t in texte), default=0)

    def block(je_karte: Optional[int]) -> list[tuple[str, str, str]]:
        return [(k, titel, (_sauber(t, je_karte) if je_karte is not None and len(t) > je_karte else t) or "(leer)")
                for k, titel, t in texte]

    def bauen(je_karte: Optional[int], papier_jetzt: str, anzahl_kanten: int) -> str:
        gekuerzt = (je_karte is not None and je_karte < laengste) or papier_jetzt != papier_voll \
            or anzahl_kanten < len(alle_kanten)
        return nachricht_bewerten(bubble_titel, block(je_karte), kanten[:anzahl_kanten], papier_jetzt, gekuerzt)

    je_karte: Optional[int] = None
    anzahl = len(kanten)
    nachricht = bauen(None, papier, anzahl)
    if groesse(nachricht) > grenze and len(papier) > WHITE_PAPER_GEKUERZT:
        papier = _sauber(papier, WHITE_PAPER_GEKUERZT)
        nachricht = bauen(None, papier, anzahl)
    if groesse(nachricht) > grenze:
        je_karte = _groesste_grenze(lambda c: groesse(bauen(c, papier, anzahl)) <= grenze, KARTE_MIN,
                                    max(KARTE_MIN, laengste))
        if je_karte is None:
            je_karte = KARTE_MIN
            weg = _wenigste_weglassen(lambda n: groesse(bauen(KARTE_MIN, papier, len(kanten) - n)) <= grenze,
                                      len(kanten))
            if weg is None:
                raise RueckwegFehler(ZU_GROSS)
            anzahl = len(kanten) - weg
        nachricht = bauen(je_karte, papier, anzahl)
    gekuerzte_karten = sum(1 for _, _, t in texte if je_karte is not None and len(t) > je_karte)
    kuerzung = {
        "gekuerzt": bool(gekuerzte_karten or anzahl < len(alle_kanten) or papier != papier_voll),
        "karten": len(texte), "karten_gekuerzt": gekuerzte_karten,
        "karte_zeichen": je_karte if gekuerzte_karten else None,
        "kanten": len(alle_kanten), "kanten_weggelassen": len(alle_kanten) - anzahl,
        "white_paper_gekuerzt": papier != papier_voll,
        "bytes": groesse(nachricht),
    }
    return nachricht, kuerzung


def _wenigste_weglassen(passt: Callable[[int], bool], hoechstens: int) -> Optional[int]:
    """Kleinste Zahl n in [0, hoechstens], bei der ``passt(n)`` gilt (``passt`` waechst mit n)."""
    if not passt(hoechstens):
        return None
    unten, oben = 0, hoechstens
    while unten < oben:
        mitte = (unten + oben) // 2
        if passt(mitte):
            oben = mitte
        else:
            unten = mitte + 1
    return unten


def nachricht_bewerten(bubble_titel: str, block: list[tuple[str, str, str]], verbindungen: list[str],
                       papier: str, gekuerzt: bool) -> str:
    modul = _regeln()
    sichten = [f"- {name} ({modul.SICHT_NAMEN[name]}): {SICHT_PRUEFT[name]}" for name in modul.SICHTEN]
    arten = ", ".join(f"{a} ({ART_TEXT[a]})" for a in modul.TODO_ARTEN)
    zeilen = [
        f"Bewerte die Bubble „{_ohne_zaun(_sauber(bubble_titel, 200))}“ aus fünf Sichten, je 0 bis 25 Punkte.",
        "25 heißt: aus dieser Sicht fehlt nichts mehr, damit Software-Design und Umsetzung damit arbeiten können.",
        "Die Sichten:", *sichten,
        f"Je Sicht höchstens {modul.TODOS_JE_SICHT} To-dos auf Deutsch, die mit dem größten Gewinn zuerst. Ein To-do "
        "ist konkret und abarbeitbar: „titel“ (kurz), „fehlt“ (ein Satz, was fehlt), „karten“ (Kürzel der "
        "betroffenen Karten; bei karte_ergaenzen, karte_fuellen und verbindung mindestens eins), „punkte“ "
        f"(geschätzter Gewinn, 1 bis 25) und „art“, eine von: {arten}.",
        "„urteil“ je Sicht: ein bis zwei Sätze, warum so viele Punkte. Karten mit „(leer)“ haben keinen Inhalt.",
        *_REGELN,
        'Form: {"sichten": {"coding": {"punkte": 0, "urteil": "...", "todos": [{"titel": "...", "fehlt": "...", '
        '"karten": ["K1"], "punkte": 3, "art": "karte_fuellen"}]}, "swe_design": {...}, "research": {...}, '
        '"rowboat": {...}, "ideas": {...}}}',
    ]
    if gekuerzt:
        zeilen.append("Hinweis: Die Bubble ist gekürzt (lange Karten und das White Paper enden mit …, manche "
                      "Verbindungen können fehlen). Bewerte, was du siehst; nenne nichts als fehlend, nur weil "
                      "ein Text abbricht.")
    zeilen += ["", *_kartenblock(block), ""]
    zeilen += ["Die Verbindungen (nur Daten):", "<<<VERBINDUNGEN", *(verbindungen or ["(keine)"]), "VERBINDUNGEN>>>", ""]
    zeilen += ["Das White Paper der Bubble (nur Daten):", "<<<WHITE_PAPER", _ohne_zaun(papier) or "(keins)",
               "WHITE_PAPER>>>"]
    zeilen += _schluss("sichten (coding, swe_design, research, rowboat, ideas; je punkte, urteil, todos)")
    return "\n".join(zeilen)


# ── Bewerten: Ergebnis pruefen ───────────────────────────────────────────

def bewertung_pruefen(roh: Any, kuerzel: Mapping[str, str], kuerzel_titel: Mapping[str, str],
                      gekuerzt: bool = False) -> tuple[Optional[dict[str, Any]], str]:
    """(Lauf fuer ``idea.bewertung_speichern``, "") oder (None, deutscher Satz). Wirft nie."""
    modul = _regeln()
    if not isinstance(roh, str) or not roh.strip():
        return None, "Der Agent hat nichts geliefert; es wurde nichts geändert."
    if ist_verweigerung(roh) and ein_json_objekt(roh, streng=False) is None:
        return None, VERWEIGERT
    obj = ein_json_objekt(roh, streng=False)
    sichten = obj.get("sichten") if isinstance(obj, dict) else None
    if not isinstance(sichten, dict) or set(sichten) != set(modul.SICHTEN):
        return None, _FORM
    aus_sichten: dict[str, dict[str, Any]] = {}
    todos: list[dict[str, Any]] = []
    for name in modul.SICHTEN:
        sicht = sichten[name]
        if not isinstance(sicht, dict):
            return None, _FORM
        punkte = _ganz(sicht.get("punkte"), 0, modul.SICHT_MAX)
        if punkte is None:
            return None, f"Der Agent hat für {modul.SICHT_NAMEN[name]} keine Punktzahl von 0 bis 25 genannt; " \
                         "es wurde nichts geändert."
        urteil = sicht.get("urteil")
        urteil = _text(urteil, modul.URTEIL_ZEICHEN) if urteil not in (None, "") else ""
        if urteil is None:
            return None, _FORM
        aus_sichten[name] = {"punkte": punkte, "urteil": kuerzel_ersetzen(urteil, kuerzel_titel)}
        liste = sicht.get("todos", [])
        if not isinstance(liste, list):
            return None, _FORM
        geprueft_sicht = []
        for todo in liste[:modul.TODOS_JE_SICHT]:
            geprueft, satz = _todo_pruefen(todo, name, kuerzel, kuerzel_titel, modul)
            if geprueft is None:
                return None, satz
            geprueft_sicht.append(geprueft)
        # Ein To-do bringt hoechstens, was der Sicht fehlt (erster echter Lauf, 26.11.2026).
        todos += modul.todos_begrenzen(geprueft_sicht, punkte)
    # Die Quelle setzt der feste Rueckweg, nicht das Modell (was es selbst dazu schreibt, zaehlt nicht).
    lauf = {"sichten": aus_sichten, "todos": todos, "gekuerzt": bool(gekuerzt), "quelle": modul.QUELLE_AGENT}
    fehler = modul.lauf_fehler(lauf)
    if fehler:
        return None, _FORM
    return lauf, ""


def _todo_pruefen(todo: Any, sicht: str, kuerzel: Mapping[str, str], kuerzel_titel: Mapping[str, str],
                  modul: Any) -> tuple[Optional[dict[str, Any]], str]:
    if not isinstance(todo, dict):
        return None, _FORM
    titel = _text(todo.get("titel"), modul.TODO_TITEL_ZEICHEN)
    fehlt = _text(todo.get("fehlt"), modul.TODO_FEHLT_ZEICHEN)
    punkte = _ganz(todo.get("punkte"), 1, modul.SICHT_MAX)
    art = todo.get("art")
    karten = todo.get("karten", [])
    if titel is None or fehlt is None or punkte is None or art not in modul.TODO_ARTEN or not isinstance(karten, list):
        return None, _FORM
    ids: list[str] = []
    for k in karten[:modul.TODO_KARTEN_MAX]:
        m = _KUERZEL.match(k.strip()) if isinstance(k, str) else None
        if m is None or m.group(1) not in kuerzel:
            return None, "Der Agent hat ein To-do auf eine Karte bezogen, die es nicht gibt; es wurde nichts geändert."
        if kuerzel[m.group(1)] not in ids:
            ids.append(kuerzel[m.group(1)])
    if art in ("karte_ergaenzen", "karte_fuellen", "verbindung") and not ids:
        return None, _FORM
    return {"sicht": sicht, "titel": kuerzel_ersetzen(titel, kuerzel_titel)[:modul.TODO_TITEL_ZEICHEN],
            "fehlt": kuerzel_ersetzen(fehlt, kuerzel_titel)[:modul.TODO_FEHLT_ZEICHEN],
            "karten": ids, "punkte": punkte, "art": art}, ""


def bewertung_meldung(lauf: Mapping[str, Any], score: int, kuerzung: Optional[Mapping[str, Any]] = None) -> str:
    modul = _regeln()
    teile = ", ".join(f"{modul.SICHT_NAMEN[n]} {lauf['sichten'][n]['punkte']}" for n in modul.SICHTEN)
    # Score in Prozent (Variante A); die rohe Summe steht dabei, damit die Sichten zu je 25 aufgehen.
    satz = f"Bewertung: {score} von 100 ({modul.lauf_summe(lauf)} von {modul.SICHT_MAX * len(modul.SICHTEN)} " \
           f"Punkten: {teile})."
    if isinstance(lauf.get("aufrufe"), int) and not isinstance(lauf.get("aufrufe"), bool):
        n = lauf["aufrufe"]
        satz += f" Bewertet von MiroFish mit fünf Rollen, {n} Abo-Aufruf{'' if n == 1 else 'e'}."
    gekuerzt = kuerzung_satz(kuerzung)
    if gekuerzt:
        satz += " " + gekuerzt
    elif lauf.get("gekuerzt"):
        satz += " Gekürzt bewertet. Fürs Befördern zählt nur eine ungekürzte Bewertung."
    if lauf.get("quelle", modul.QUELLE_AGENT) != modul.BEFOERDERN_QUELLE:
        satz += " " + AUSBAU_SATZ
    todos = [{"nr": nr, **t} for nr, t in enumerate(lauf["todos"], start=1)]
    if not todos:
        return satz + " Offene To-dos nennt der Agent keine."
    top = modul.offene_todos({"todos": todos}, 3)
    satz += f" {len(todos)} To-do{'' if len(todos) == 1 else 's'}; am meisten bringen: " + "; ".join(
        f"{t['nr']}. {t['titel']} (+{t['punkte']}, {ART_TEXT[t['art']]})" for t in top) + "."
    return satz + " Sag „Erledige To-do N“ oder, wenn du es selbst gemacht hast, „To-do N erledigt“."


# ── To-do: Rueckfrage und Anlegen ────────────────────────────────────────

def _todo_finden(bubble: Mapping[str, Any], nr: Any) -> dict[str, Any]:
    modul = _regeln()
    lauf = modul.bewertung_lauf(bubble)
    if lauf is None:
        raise RueckwegFehler("Diese Bubble hat noch keine Bewertung mit To-dos. Sag „Bewerte diese Bubble“.")
    treffer = [t for t in lauf["todos"] if isinstance(t, Mapping) and t.get("nr") == nr]
    if not treffer:
        raise RueckwegFehler(f"Ein To-do {nr} gibt es in der letzten Bewertung nicht "
                             f"(sie hat {len(lauf['todos'])}).")
    todo = dict(treffer[0])
    if todo.get("erledigt"):
        raise RueckwegFehler(f"To-do {nr} ist schon erledigt. Für einen neuen Stand sag „Bewerte diese Bubble“.")
    if todo.get("art") == "white_paper":
        raise RueckwegFehler(f"To-do {nr} ist ein neues White Paper: sag „Erstelle ein White Paper aus dieser "
                             f"Bubble“ und danach „To-do {nr} erledigt“.")
    if todo.get("art") == "entscheidung":
        raise RueckwegFehler(f"To-do {nr} ist eine Entscheidung, die triffst du: „{_sauber(todo.get('fehlt'), 300)}“ "
                             f"Halte sie in einer Karte fest und sag dann „To-do {nr} erledigt“.")
    return todo


def todo_frage(bubble: Mapping[str, Any], nr: int) -> str:
    """Die Rueckfrage vor "Erledige To-do N" (liest nur); ``RueckwegFehler`` wenn es nicht geht."""
    todo = _todo_finden(bubble, nr)
    return (f"To-do {nr} „{_sauber(todo.get('titel'), 120)}“ ({ART_TEXT.get(str(todo.get('art')), '')}, "
            f"+{todo.get('punkte')}): {_sauber(todo.get('fehlt'), 400)} Das gebe ich brain-ideas (ein Abo-Aufruf); "
            "geschrieben wird über die normalen Werkzeuge, „Zurück“ nimmt es zurück. Sag „Ja, erledigen“.")


def todo_vorbereiten(event_id: str, bubble: Mapping[str, Any], nr: Any, lesen: Lesen) -> tuple[str, dict[str, Any]]:
    modul = _regeln()
    todo = _todo_finden(bubble, nr)
    bubble_id, bubble_titel = str(bubble["id"]), titel_von(bubble)
    zeilen = _alle_zeilen(bubble_id, lesen)
    notizen = sorted((z for z in zeilen if modul.ist_notiz(z)), key=_lage)
    betroffen = [i for i in todo.get("karten") or [] if isinstance(i, str)]
    nach_id = {z["id"]: z for z in notizen}
    fehlend = [i for i in betroffen if i not in nach_id]
    if fehlend:
        raise RueckwegFehler(f"Eine Karte von To-do {nr} gibt es nicht mehr. Sag „Bewerte diese Bubble“ für "
                             "einen neuen Stand.")
    reihenfolge = betroffen + [z["id"] for z in notizen if z["id"] not in betroffen][:TODO_NACHBARN_MAX]
    kuerzel = {f"K{n}": i for n, i in enumerate(reihenfolge, start=1)}
    kuerzel_titel = {k: titel_von(nach_id[i]) for k, i in kuerzel.items()}
    texte = {k: modul.kartentext(nach_id[i]) for k, i in kuerzel.items()}
    betroffen_k = [k for k, i in kuerzel.items() if i in betroffen]
    nachbarn_k = [k for k in kuerzel if k not in betroffen_k]

    def bauen(je_karte: int, anzahl_nachbarn: int) -> str:
        voll = [(k, kuerzel_titel[k], _sauber(texte[k], je_karte) or "(leer)") for k in betroffen_k]
        rest = [(k, kuerzel_titel[k], _sauber(" ".join(texte[k].split()), TODO_NACHBAR_ZEICHEN) or "(leer)")
                for k in nachbarn_k[:anzahl_nachbarn]]
        return nachricht_todo(bubble_titel, todo, voll, rest)

    # Erst die betroffenen Karten kuerzen (bis TODO_KARTE_MIN), dann Nachbarn weglassen.
    je_karte = _groesste_grenze(lambda c: groesse(bauen(c, len(nachbarn_k))) <= NACHRICHT_BYTES,
                                min(TODO_KARTE_MIN, TODO_KARTE_ZEICHEN), TODO_KARTE_ZEICHEN)
    anzahl = len(nachbarn_k)
    if je_karte is None:
        je_karte = min(TODO_KARTE_MIN, TODO_KARTE_ZEICHEN)
        weg = _wenigste_weglassen(lambda n: groesse(bauen(je_karte, len(nachbarn_k) - n)) <= NACHRICHT_BYTES,
                                  len(nachbarn_k))
        if weg is None:
            raise RueckwegFehler(TODO_ZU_GROSS.replace("To-do N", f"To-do {nr}"))
        anzahl = len(nachbarn_k) - weg
    # Nur Karten, die der Agent auch sieht, koennen Ziel einer Aenderung sein.
    for k in nachbarn_k[anzahl:]:
        kuerzel.pop(k, None)
        kuerzel_titel.pop(k, None)
    nachricht = bauen(je_karte, anzahl)
    rueckweg = {
        "version": VERSION, "vertrag": "todo", "ereignis": event_id, "bubble_id": bubble_id,
        "titel": _sauber(f"To-do {nr}: {todo.get('titel')}", 200), "nr": nr, "art": todo.get("art"),
        "kuerzel": kuerzel, "kuerzel_titel": kuerzel_titel,
        "stand": {i: _inhalt_stand(nach_id[i]) for i in kuerzel.values()},
        "lage": {i: [nach_id[i].get("x") or 0, nach_id[i].get("y") or 0] for i in kuerzel.values()},
    }
    return nachricht, rueckweg


def nachricht_todo(bubble_titel: str, todo: Mapping[str, Any], voll: list[tuple[str, str, str]],
                   rest: list[tuple[str, str, str]]) -> str:
    zeilen = [
        f"Arbeite ein To-do aus der Bewertung der Bubble „{_ohne_zaun(_sauber(bubble_titel, 200))}“ ab. Deine "
        "Änderungen schreibt das Gehirn direkt in die Karten.",
        f"Das To-do: „{_ohne_zaun(str(todo.get('titel')))}“. Was fehlt: {_ohne_zaun(str(todo.get('fehlt')))} "
        f"(Art: {ART_TEXT.get(str(todo.get('art')), '')}).",
        f"Liefere höchstens {AENDERUNGEN_MAX} Änderungen in der Liste „aenderungen“, jede eine von:",
        '- {"art": "ergaenzen", "karte": "K1", "text": "..."}: hängt Text an die Karte an.',
        '- {"art": "fuellen", "karte": "K1", "text": "..."}: ersetzt den ganzen Text einer leeren oder dünnen Karte.',
        '- {"art": "neu", "titel": "...", "text": "...", "verbinden": ["K1"]}: neue Karte, mit mindestens einer '
        "Verbindung zu einer vorhandenen Karte; der Titel kommt in der Bubble noch nicht vor.",
        '- {"art": "verbinden", "von": "K1", "nach": "K2"}: verbindet zwei vorhandene Karten.',
        "Schreibe in der Sprache und Form der Karten, konkret und ohne Wiederholungen. Keine Zahlen, Namen oder "
        "Termine, die nicht in den Karten stehen; was offen ist, benennst du als offen.",
        *_REGELN,
        "", "Die betroffenen Karten mit vollem Text:", *_kartenblock(voll), "",
        "Die anderen Karten (Titel und Anfang, nur zum Verbinden und Einordnen):", *_kartenblock(rest),
    ]
    zeilen += _schluss("aenderungen (Liste; je art und die Felder dieser Art)")
    return "\n".join(zeilen)


# ── To-do: Ergebnis pruefen ──────────────────────────────────────────────

def todo_pruefen(roh: Any, rueckweg: Mapping[str, Any]) -> tuple[Optional[list[dict[str, Any]]], str]:
    """Die Aenderungen mit Kennungen statt Kuerzeln, oder (None, Satz). Wirft nie."""
    if not isinstance(roh, str) or not roh.strip():
        return None, "Der Agent hat nichts geliefert; es wurde nichts geändert."
    obj = ein_json_objekt(roh, streng=False)
    if obj is None:
        return None, VERWEIGERT if ist_verweigerung(roh) else _FORM
    liste = obj.get("aenderungen")
    if not isinstance(liste, list) or not 1 <= len(liste) <= AENDERUNGEN_MAX:
        return None, _FORM
    kuerzel, kuerzel_titel = rueckweg["kuerzel"], rueckweg["kuerzel_titel"]
    vorhandene_titel = {str(t).strip().lower() for t in kuerzel_titel.values()}

    def karte(wert: Any) -> Optional[str]:
        m = _KUERZEL.match(wert.strip()) if isinstance(wert, str) else None
        return kuerzel.get(m.group(1)) if m else None

    aus: list[dict[str, Any]] = []
    for a in liste:
        if not isinstance(a, dict) or a.get("art") not in ("ergaenzen", "fuellen", "neu", "verbinden"):
            return None, _FORM
        art = a["art"]
        if art in ("ergaenzen", "fuellen"):
            ziel, text = karte(a.get("karte")), _mehrzeilig(a.get("text"))
            if ziel is None or text is None:
                return None, _FORM if ziel is not None else "Der Agent hat eine Karte genannt, die es nicht gibt; " \
                                                             "es wurde nichts geändert."
            aus.append({"art": art, "id": ziel, "text": kuerzel_ersetzen(text, kuerzel_titel)})
        elif art == "neu":
            titel = _text(a.get("titel"), NEUE_KARTE_TITEL)
            text = _mehrzeilig(a.get("text"))
            ziele = a.get("verbinden")
            ziele = [karte(z) for z in ziele] if isinstance(ziele, list) else []
            if titel is None or text is None or not ziele or None in ziele or len(ziele) > VERBINDEN_MAX:
                return None, _FORM
            if titel.strip().lower() in vorhandene_titel:
                return None, (f"Der Agent wollte eine Karte „{titel}“ anlegen, die es schon gibt; "
                              "es wurde nichts geändert.")
            vorhandene_titel.add(titel.strip().lower())
            aus.append({"art": "neu", "titel": kuerzel_ersetzen(titel, kuerzel_titel),
                        "text": kuerzel_ersetzen(text, kuerzel_titel), "verbinden": list(dict.fromkeys(ziele))})
        else:
            von, nach = karte(a.get("von")), karte(a.get("nach"))
            if von is None or nach is None or von == nach:
                return None, _FORM
            aus.append({"art": "verbinden", "id": von, "nach": nach})
    return aus, ""


def _mehrzeilig(wert: Any) -> Optional[str]:
    if (not isinstance(wert, str) or not wert.strip() or len(wert) > AENDERUNG_TEXT
            or _STEUER.search(wert) or _HTML.search(wert) or _WERKZEUG.search(wert) or ist_verweigerung(wert)):
        return None
    return wert.strip()


# ── Verarbeiten (Nachfasser) ─────────────────────────────────────────────

Aufruf = Callable[[str, dict[str, Any]], Any]
Pruefe = Callable[[str, dict[str, Any], Any], tuple[Optional[bool], str]]


def _fest(ereignis: str, argumente: dict[str, Any], aufruf: Aufruf, pruefe: Pruefe) -> tuple[Optional[bool], str]:
    """Ein fester Aufruf mit Vertrag und Nachpruefung: (ok, Grund)."""
    geprueft, grund = feste_argumente(ereignis, argumente)
    if geprueft is None:
        return False, f"contract: {grund}"
    try:
        quittung = aufruf(ereignis, geprueft)
    except FreigabeAusstehend:
        return None, "wartet"
    except Exception as e:  # noqa: BLE001
        return None, f"{ereignis}: {type(e).__name__}: {str(e)[:200]}"
    ok, grund = pruefe(ereignis, geprueft, quittung)
    return ok, grund if ok is True else f"{ereignis}: {grund}"


def verarbeiten(auftrag: Mapping[str, Any], rueckweg: Mapping[str, Any], *,
                lies_karten: Callable[[list[str]], Optional[list]], aufruf: Aufruf,
                pruefe: Pruefe) -> tuple[str, Optional[bool], str, str, dict[str, Any]]:
    """(Status, verified, reason, Meldung, mehr) fuer ``ideen_dokument.verarbeiten``."""
    if rueckweg["vertrag"] == "bewerten":
        lauf, satz = bewertung_pruefen(auftrag.get("ergebnis"), rueckweg["kuerzel"], rueckweg["kuerzel_titel"],
                                       rueckweg.get("gekuerzt") is True)
        if lauf is None:
            return "abgelehnt", False, "result rejected", satz, {}
        ok, grund = _fest("idea.bewertung_speichern",
                          {"bubble_id": rueckweg["bubble_id"], "lauf": lauf, "kartenstand": rueckweg["kartenstand"]},
                          aufruf, pruefe)
        if ok is not True:
            if grund == "wartet":
                return "wartet", None, "idea.bewertung_speichern: approval pending", \
                    "Die Bewertung wartet auf Freigabe; gespeichert ist noch nichts.", {}
            if "herkunft_unbelegt" in grund:
                return "fehler", False, grund, HERKUNFT_SATZ, {}
            if "kartenstand_changed" in grund:
                return "fehler", False, grund, ("Während der Bewertung haben sich Karten geändert; gespeichert habe "
                                                "ich nichts. Sag noch einmal „Bewerte diese Bubble“."), {}
            return "fehler", ok, grund, "Die Bewertung ließ sich nicht speichern. Bitte prüfe die Bubble.", {}
        score = _regeln().lauf_score(lauf)
        kuerzung = rueckweg.get("kuerzung") if isinstance(rueckweg.get("kuerzung"), Mapping) else None
        return "geschrieben", True, "written and verified", bewertung_meldung(lauf, score, kuerzung), \
            {"score": score, **({"kuerzung": dict(kuerzung)} if kuerzung else {})}
    return _todo_verarbeiten(auftrag, rueckweg, lies_karten=lies_karten, aufruf=aufruf, pruefe=pruefe)


def _todo_verarbeiten(auftrag: Mapping[str, Any], rueckweg: Mapping[str, Any], *,
                      lies_karten: Callable[[list[str]], Optional[list]], aufruf: Aufruf,
                      pruefe: Pruefe) -> tuple[str, Optional[bool], str, str, dict[str, Any]]:
    aenderungen, satz = todo_pruefen(auftrag.get("ergebnis"), rueckweg)
    if aenderungen is None:
        return "abgelehnt", False, "result rejected", satz, {}
    bubble_id, nr = rueckweg["bubble_id"], rueckweg["nr"]
    # Karten, die der Agent ueberschreibt oder ergaenzt, muessen noch so sein wie beim Anlegen.
    ziele = sorted({a["id"] for a in aenderungen if a["art"] in ("ergaenzen", "fuellen")})
    if ziele:
        gelesen = lies_karten(ziele)
        if gelesen is None:
            return "fehler", None, "cards unreadable", "Die Karten lassen sich gerade nicht lesen; geändert ist nichts.", {}
        jetzt = {k.get("id"): k for k in gelesen if isinstance(k, Mapping) and k.get("linked_idea_id") == bubble_id}
        geaendert = [i for i in ziele if i not in jetzt or _inhalt_stand(jetzt[i]) != rueckweg["stand"].get(i)]
        if geaendert:
            return "abgelehnt", False, "card changed", ("Eine betroffene Karte hat sich inzwischen geändert; geändert "
                                                        f"habe ich nichts. Sag noch einmal „Erledige To-do {nr}“."), {}
    titel = rueckweg["kuerzel_titel"]
    nach_kuerzel = {i: k for k, i in rueckweg["kuerzel"].items()}
    erledigt: list[str] = []
    for a in aenderungen:
        if a["art"] == "ergaenzen":
            ereignis, argumente = "idea.update", {"id": a["id"], "content_append": a["text"]}
            text = f"„{titel[nach_kuerzel[a['id']]]}“ ergänzt"
        elif a["art"] == "fuellen":
            ereignis, argumente = "idea.update", {"id": a["id"], "content": a["text"]}
            text = f"„{titel[nach_kuerzel[a['id']]]}“ gefüllt"
        elif a["art"] == "neu":
            x, y = (rueckweg.get("lage") or {}).get(a["verbinden"][0]) or [0, 0]
            ereignis = "idea.create"
            argumente = {"bubble_id": bubble_id, "title": a["titel"], "content": a["text"],
                         "x": int(x) + 260, "y": int(y) + 40 * (len(erledigt) + 1), "verbinden_mit": a["verbinden"]}
            text = f"neue Karte „{a['titel']}“"
        else:
            ereignis, argumente = "idea.update", {"id": a["id"], "verbinden_mit": [a["nach"]]}
            text = f"„{titel[nach_kuerzel[a['id']]]}“ mit „{titel[nach_kuerzel[a['nach']]]}“ verbunden"
        ok, grund = _fest(ereignis, argumente, aufruf, pruefe)
        if ok is not True:
            schon = f" Schon geschrieben: {', '.join(erledigt)}; „Zurück“ nimmt das zurück." if erledigt else ""
            if grund == "wartet":
                return "wartet", None, f"{ereignis}: approval pending", \
                    f"To-do {nr} wartet auf Freigabe.{schon}", {}
            return "fehler", ok, grund, f"To-do {nr} ist nicht ganz erledigt: {text} ging nicht.{schon}", {}
        erledigt.append(text)
    ok, grund = _fest("idea.todo_abhaken", {"bubble_id": bubble_id, "nr": nr, "durch": "agent"}, aufruf, pruefe)
    haken = "" if ok is True else (
        " Abhaken ging nicht (Rückweg-Schlüssel fehlt oder passt nicht); sag „To-do {nr} erledigt“.".format(nr=nr)
        if "herkunft_unbelegt" in grund else f" Abhaken ging nicht ({grund[:80]}); sag „To-do {nr} erledigt“.")
    return ("geschrieben", True if ok is True else ok, "written and verified" if ok is True else grund,
            f"To-do {nr} erledigt: {', '.join(erledigt)}.{haken} „Zurück“ nimmt das zurück. Die Bewertung ist "
            "damit veraltet; sag „Bewerte diese Bubble“ für einen neuen Stand.", {})


def rueckweg_gueltig(rueckweg: Mapping[str, Any]) -> bool:
    """Form des Rueckwegs ``bewerten`` / ``todo`` (fuer ``ideen_dokument.rueckweg_aus``)."""
    k, kt = rueckweg.get("kuerzel"), rueckweg.get("kuerzel_titel")
    if not (isinstance(k, Mapping) and k and all(ist_kennung(v) for v in k.values())
            and isinstance(kt, Mapping) and set(kt) == set(k)):
        return False
    if rueckweg.get("vertrag") == "bewerten":
        stand = rueckweg.get("kartenstand")
        return isinstance(stand, str) and re.fullmatch(r"sha256:[0-9a-f]{64}", stand) is not None
    nr = rueckweg.get("nr")
    return (isinstance(nr, int) and not isinstance(nr, bool) and 1 <= nr <= 25
            and isinstance(rueckweg.get("stand"), Mapping))
