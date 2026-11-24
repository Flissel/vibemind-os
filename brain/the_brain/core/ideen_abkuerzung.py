"""Abkuerzung fuer den Ideen-Space: Satz und Kontext der App -> fester Aufruf, ohne Modell.

Die App schickt mit jedem Satz ihren Kontext (``spaces/ideas/ANZEIGE.md``): offene
Bubble, sichtbare Karten, Auswahl, zuletzt geaenderte Karte, vorgemerkte Kennung.
Dieses Modul erkennt die festen Saetze und loest Namen in Kennungen auf. Es raet nie:
passt ein Name auf keine oder auf mehrere Karten, gibt es eine Rueckfrage statt eines
Aufrufs, und ohne Kennung wird nichts aufgerufen.

Ergebnis von ``loese``:
  ``None``                               kein fester Satz -> der bisherige Weg entscheidet
  ``{"rueckfrage": text}``               Antwort an den Menschen, kein Aufruf
  ``{"ereignis", "capability", "argumente", "antwort"}``   ein fester Aufruf

Keine Abhaengigkeiten, damit es ohne das Paket ``core`` testbar bleibt.
"""
from __future__ import annotations

import json
import re
from typing import Any, Optional

_KENNUNG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:_-]{0,127}$")
_I = re.IGNORECASE

_FORMATWORTE = (
    (r"tabelle", "table"),
    (r"aufgabenliste|todo-?liste|to-do-liste|checkliste", "action_list"),
    (r"pro(?:\s+und\s+|\s*/\s*|-|\s+)contra|pro\s+und\s+kontra|vor-?\s*und\s+nachteile", "pros_cons"),
    (r"gliederung|hierarchie", "hierarchy"),
    (r"spezifikation|specs?|technische\s+daten", "technical_specs"),
    (r"kanban(?:-?board)?", "kanban"),
    (r"mindmap", "mindmap"),
    (r"swot(?:-analyse)?", "swot"),
    (r"user\s?story", "user_story"),
    (r"flussdiagramm|ablaufdiagramm|flowchart", "flowchart"),
    (r"notiz|einfache\s+notiz", "note"),
)
_FORMAT_NAMEN = {
    "table": "Tabelle", "action_list": "Aufgabenliste", "pros_cons": "Pro und Contra",
    "hierarchy": "Gliederung", "technical_specs": "Spezifikation", "kanban": "Kanban",
    "mindmap": "Mindmap", "swot": "SWOT", "user_story": "User Story",
    "flowchart": "Flussdiagramm", "note": "Notiz",
}
_FORMAT = "(?P<format>" + "|".join(muster for muster, _ in _FORMATWORTE) + ")"
_ARTIKEL = r"(?:eine[nrm]?\s+|als\s+)?"
# White Paper (core/ideen_dokument). Die Knoepfe der App senden
# "Erstelle ein White Paper aus dieser Bubble" und "Fasse diese Bubble zusammen".
_WP = r"(?:white[\s-]?paper|whitepaper)"
_WP_SATZ = (rf"(?:erstell(?:e)?|mach(?:e)?|schreib(?:e)?)\s+(?:mir\s+)?(?:ein|das)\s+{_WP}"
            r"(?:\s+(?:aus|zu|über|ueber|für|fuer|von)\s+(?:(?:dieser|diese|der|die|dem)\s+bubble"
            r"|der\s+bubble\s+(?P<b>.+?)|(?P<n>.+?)))?"
            r"(?:\s+mit\s+bis\s+zu\s+(?P<z>\d{1,3})\s+l(?:ä|ae)ufen)?")
_WP_ABBRUCH = rf"(?:brich|breche)\s+das\s+{_WP}\s+ab|stopp(?:e)?\s+das\s+{_WP}"
_KARTE = r"(?:(?:die|der|das)\s+)?(?:notiz|karte|idee)\s+"


# Kennungen, wie die Datenbank sie fuehrt: 8 Hex-Zeichen (Bubbles, Karten aus Gehirn und
# Ideen-Server), 32 Hex-Zeichen (Verbindungen) oder eine UUID (Vorgabe der Datenbank,
# vorgemerkte Karten der App). Gemessen am 23.11.2026: von 15 Bubbles 9 mit 8 Hex-Zeichen,
# 6 mit UUID. Eine lokale Zaehlnummer der App ("1") ist keine solche Kennung.
_DB_KENNUNG = re.compile(
    r"^(?:[0-9a-f]{8}|[0-9a-f]{32}|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$",
    re.IGNORECASE,
)


def ist_kennung(wert: Any) -> bool:
    """Eine Kennung aus dem Kontext der App gilt nur in der Form der Datenbank.

    Am 23.11.2026 schickte die App als offene Bubble die lokale Zahl "1". Der Aufruf
    lief damit bis zur Datenbank (supabase_http_error) und die Liste blieb leer, obwohl
    Karten da waren. Was nicht wie eine Datenbank-Kennung aussieht, wird nie benutzt.
    """
    return isinstance(wert, str) and _DB_KENNUNG.fullmatch(wert) is not None


def _normal(text: Any) -> str:
    text = str(text or "").strip().strip("\"'„“”‚‘’«».!?:;, ")
    return re.sub(r"\s+", " ", text).casefold()


_PRAEP = r"(?:zum|zur|zu|über|ueber|von|vom|mit|für|fuer|dem|den|der|des|die|das|im|am)"

# Besitzwoerter und Artikel vor einem Namen gehoeren nicht zum Titel ("meinem Journal").
BESITZ = (r"(?:mein|meine|meinem|meinen|meiner|meines|dein|deine|deinem|deinen|deiner|deines|"
          r"unser|unsere|unserem|unseren|unserer|unseres|der|die|das|dem|den|des)")

# Bereiche, keine Kartentitel (Nachmessung 23.11.2026: "Was steht in meinem Journal?" wurde als
# Notizname gelesen). Wort -> Space der App oder None. core/verstehen.bereichsfrage nutzt dieselbe Liste.
BEREICHSWOERTER = {
    "journal": None, "tagebuch": None, "kalender": None, "termine": None, "kontakte": None,
    "events": "events", "event": "events", "veranstaltungen": "events",
    "kurse": "learning", "kurs": "learning",
}


def ohne_besitzwort(name: Any) -> str:
    """"meinem Journal" -> "Journal"."""
    text = re.sub(r"\s+", " ", str(name or "")).strip()
    return re.sub(rf"^(?:{BESITZ}\s+)+", "", text, flags=re.IGNORECASE).strip()


def bereichswort(name: Any) -> Optional[str]:
    """Das Bereichswort ("journal"), wenn ``name`` nur daraus besteht, sonst ``None``."""
    wort = ohne_besitzwort(name).strip(" ?!.\"'„“”").casefold()
    return wort if wort in BEREICHSWOERTER else None


def _vergleich(text: Any) -> str:
    """Titel zum Vergleichen: klein, Unterstrich als Leerzeichen, ohne Auslassungszeichen."""
    text = _normal(str(text or "").replace("_", " ")).rstrip("…. ").strip()
    return re.sub(r"\s+", " ", text)


def _gekuerzt(titel: Any) -> bool:
    titel = str(titel or "").rstrip()
    return titel.endswith("…") or titel.endswith("...")


def _wortanfaenge(gesucht: list[str], titel: Any) -> bool:
    woerter = re.findall(r"\w+", _vergleich(titel))
    return bool(woerter) and all(any(w.startswith(g) for w in woerter) for g in gesucht)


def _kurztitel(text: str) -> str:
    """Erste sinnvolle Wendung als Titel, hoechstens 60 Zeichen; der ganze Satz wird der Text.

    Vorher wurde der ganze Satz zum Titel ("Halte fest: ..." mit 200 Zeichen), und die
    Karte hatte keinen Text.
    """
    text = text.strip()
    for trenner in (" – ", " - ", ": ", ". ", "; ", ", ", " — "):
        stelle = text.find(trenner)
        if 3 <= stelle <= 60:
            kopf = text[:stelle]
            break
    else:
        kopf = text if len(text) <= 60 else text[:61].rsplit(" ", 1)[0]
    return kopf.strip().rstrip(".,;:–—- ") or text[:60]


def _format_aus(wort: str) -> Optional[str]:
    for muster, name in _FORMATWORTE:
        if re.fullmatch(muster, wort.strip(), _I):
            return name
    return None


class _Rueckfrage(Exception):
    """Rueckfrage an den Menschen. Mit ``kandidaten`` (Karten) und ``name`` (der genannte
    Name) wird sie als offene Rueckfrage gemerkt: die naechste Antwort ("die zweite",
    "2", ein Titel) loest dann genau diesen Namen auf und fuehrt den ersten Satz aus."""

    def __init__(self, text: str, *, kandidaten: Optional[list] = None, name: Optional[str] = None) -> None:
        super().__init__(text)
        self.kandidaten = [k for k in (kandidaten or []) if isinstance(k, dict) and ist_kennung(k.get("id"))]
        self.name = name


# ── Offene Rueckfragen (Arbeitslauf 2: "die zweite" ging an den Chat und kam als
#    Lexikontext zurueck). Gemerkt je offener Bubble im Prozess des Gehirns, kurz.
OFFEN_GILT_S = 300.0
_OFFEN: dict[str, dict] = {}
_ORDNUNG = {"erste": 1, "ersten": 1, "zweite": 2, "zweiten": 2, "dritte": 3, "dritten": 3, "vierte": 4,
            "vierten": 4, "fünfte": 5, "fuenfte": 5, "fünften": 5, "sechste": 6, "sechsten": 6, "siebte": 7,
            "siebten": 7, "achte": 8, "achten": 8, "neunte": 9, "neunten": 9, "zehnte": 10, "zehnten": 10}


def _offen_schluessel(kontext: dict) -> str:
    bubble = kontext.get("bubble") if isinstance(kontext.get("bubble"), dict) else {}
    return str(bubble.get("id")) if ist_kennung(bubble.get("id")) else "-"


def _wahl(text: str, kandidaten: list[dict]) -> Optional[int]:
    """Index der gewaehlten Karte, ``-1`` = sieht aus wie eine Antwort, passt aber nicht,
    ``None`` = keine Antwort auf die Rueckfrage (ein neuer Satz)."""
    roh = _normal(text)
    if len(roh) > 80 or len(roh.split()) > 8:
        return None
    kern = re.sub(rf"^(?:(?:die|den|das|der|nimm|nehme|ich\s+meine|meine|gemeint\s+ist)\s+)+", "", roh)
    kern = re.sub(r"\s+(?:notiz|karte)$", "", kern).strip()
    m = re.fullmatch(r"(?:nummer|nr\.?|#)?\s*(\d{1,2})\.?", kern)
    if m:
        n = int(m.group(1))
        return n - 1 if 1 <= n <= len(kandidaten) else -1
    if kern in _ORDNUNG:
        n = _ORDNUNG[kern]
        return n - 1 if n <= len(kandidaten) else -1
    if kern in ("letzte", "letzten"):
        return len(kandidaten) - 1
    titel = [_vergleich(k.get("title")) for k in kandidaten]
    if kern in titel and titel.count(kern) == 1:
        return titel.index(kern)
    teil = [i for i, t in enumerate(titel) if kern and kern in t]
    if len(teil) == 1:
        return teil[0]
    if teil:
        return -1
    # Kurz und ohne Frage: eher eine Antwort, die nicht passt, als ein neuer Satz.
    return -1 if len(kern.split()) <= 3 and "?" not in text else None


def _bubble_zu(kontext: dict) -> bool:
    """Die App sagt ausdruecklich: keine Bubble offen (``bubble: null``)."""
    return "bubble" in kontext and kontext["bubble"] is None


def _karten(kontext: dict) -> list[dict]:
    if _bubble_zu(kontext):
        # Lauf 9: Nach Rowboat -> Ideen kann die App noch die Karten der zuletzt offenen
        # Bubble mitschicken. Ohne offene Bubble ist keine Karte zu sehen: nie damit aufrufen.
        return []
    karten = kontext.get("cards")
    return [k for k in karten if isinstance(k, dict) and ist_kennung(k.get("id"))] if isinstance(karten, list) else []


_BUBBLE_UNKLAR = "Ich weiß nicht, welche Bubble offen ist. Öffne die Bubble noch einmal und sag es dann erneut."


def _offene_bubble(kontext: dict) -> str:
    bubble = kontext.get("bubble")
    if isinstance(bubble, dict) and ist_kennung(bubble.get("id")):
        return bubble["id"]
    if isinstance(bubble, dict) and bubble:
        # Die App nennt eine Bubble, aber ohne Kennung der Datenbank: nie damit aufrufen.
        raise _Rueckfrage(_BUBBLE_UNKLAR)
    raise _Rueckfrage("Dafür muss eine Bubble offen sein. Öffne eine Bubble und sag es noch einmal.")


def _karte(name: str, kontext: dict) -> dict:
    """Genau eine Karte zu einem Namen, einem Fuerwort oder der Auswahl; sonst Rueckfrage."""
    karten = _karten(kontext)
    nach_id = {k["id"]: k for k in karten}
    wort = _normal(re.sub(rf"^{_KARTE}", "", name.strip(), flags=_I))
    gewaehlt = kontext.get("_gewaehlt")
    if isinstance(gewaehlt, dict) and wort in gewaehlt:
        # Antwort auf eine offene Rueckfrage: genau diese Karte fuer genau diesen Namen.
        return gewaehlt[wort]
    if wort in ("das", "die", "sie", "es", "diese", "dies", "die letzte", "die letzte notiz", "die letzte karte",
                "die notiz", "die karte", "diese notiz", "diese karte", "die markierte", "die auswahl",
                "dieser notiz", "dieser karte", "der notiz", "der karte", "dem", "der", "ihr", "ihm"):
        if _bubble_zu(kontext):
            # Auswahl und letzte Karte stammen dann aus einer Bubble, die nicht mehr offen ist.
            raise _Rueckfrage(keine_bubble_offen(""))
        auswahl = [i for i in kontext.get("selection") or [] if ist_kennung(i)]
        if len(auswahl) == 1:
            return nach_id.get(auswahl[0], {"id": auswahl[0], "title": ""})
        if len(auswahl) > 1:
            markiert = [nach_id.get(i, {"id": i, "title": ""}) for i in auswahl]
            raise _Rueckfrage(f"Es sind {len(auswahl)} Karten markiert: "
                              f"{_aufzaehlung([k.get('title') for k in markiert])}. Welche meinst du?",
                              kandidaten=markiert, name=wort)
        zuletzt = kontext.get("last_id")
        if ist_kennung(zuletzt):
            return nach_id.get(zuletzt, {"id": zuletzt, "title": ""})
        raise _Rueckfrage("Welche Karte meinst du? Nenn ihren Titel oder markiere sie.")
    genannt = re.sub(rf"^{_KARTE}", "", name.strip(), flags=_I).strip().strip("\"'„“”")
    vergleich = _vergleich(genannt)
    ohne_praep = re.sub(rf"^(?:{_PRAEP}\s+)+", "", vergleich, flags=_I)
    # "meiner Notiz Sponsoren", "unserem Bühnenplan": Besitzwoerter gehoeren nicht zum Titel.
    kerne = list(dict.fromkeys((vergleich, ohne_praep, _vergleich(ohne_besitzwort(ohne_praep)))))
    # 1. genau der Titel (Unterstrich = Leerzeichen: "Sponsoren_Liste")
    treffer = [k for k in karten if _vergleich(k.get("title")) in kerne]
    if len(treffer) == 1:
        return treffer[0]
    if len(treffer) > 1:
        raise _Rueckfrage(f"Es gibt {len(treffer)} Notizen mit dem Titel „{genannt}“. Markiere die gemeinte "
                          "oder sag „die erste“, „die zweite“ …", kandidaten=treffer, name=wort)
    # 2. gekuerzter Titel im Kontext (80 Zeichen + "…"): der genannte Name beginnt mit
    #    dem Anfang. Die Kennung zaehlt, nicht der ganze Titel.
    treffer = [k for k in karten if _gekuerzt(k.get("title"))
               and any(kern.startswith(_vergleich(k.get("title"))) for kern in kerne)]
    if len(treffer) == 1:
        return treffer[0]
    # 3. eindeutiger Teilname: jedes genannte Wort ist ein Wortanfang im Titel
    #    ("die Notiz zu den Sponsoren" -> "Sponsoren Vertragsentwurf").
    gesucht = re.findall(r"\w+", kerne[-1])
    if sum(len(w) for w in gesucht) >= 3:
        treffer = [k for k in karten if _wortanfaenge(gesucht, k.get("title"))]
        if len(treffer) == 1:
            return treffer[0]
        if len(treffer) > 1:
            raise _Rueckfrage(f"Zu „{genannt}“ passen {len(treffer)} Notizen: "
                              f"{_aufzaehlung([k.get('title') for k in treffer])}. Welche meinst du?",
                              kandidaten=treffer, name=wort)
    if _bubble_zu(kontext):
        raise _Rueckfrage(keine_bubble_offen(genannt))
    roh = kontext.get("cards")
    if isinstance(roh, list) and any(
            isinstance(k, dict) and not ist_kennung(k.get("id")) and _normal(k.get("title")) == wort for k in roh):
        # Die Karte steht am Canvas, aber die App hat keine Kennung der Datenbank mitgegeben.
        raise _Rueckfrage(f"Die Notiz „{genannt}“ kann ich gerade nicht zuordnen. Öffne die Bubble noch einmal.")
    if _bubble_ohne_kennung(kontext):
        # Lauf 7: Die App meldet eine offene Bubble, aber ohne Kennung. Dann weiss ich nicht, was
        # in ihr liegt: kein "finde ich in dieser Bubble nicht", sondern wie ohne Kontext.
        raise _Rueckfrage(_BUBBLE_UNKLAR)
    if not isinstance(kontext.get("bubble"), dict) or not kontext.get("bubble"):
        # Lauf 9: Nach Rowboat -> Ideen schickt die App bubble: null. Dann ist keine Bubble
        # offen; "finde ich in dieser Bubble nicht" behauptete eine Bubble, die es nicht gibt.
        raise _Rueckfrage(keine_bubble_offen(genannt))
    gesamt = kontext.get("cards_total")
    if isinstance(gesamt, int) and not isinstance(gesamt, bool) and gesamt > len(karten):
        raise _Rueckfrage(f"Unter den sichtbaren Notizen finde ich „{genannt}“ nicht. Markiere die Notiz.")
    raise _Rueckfrage(f"Eine Notiz „{genannt}“ finde ich in dieser Bubble nicht.")


def keine_bubble_offen(genannt: str) -> str:
    """Satz, wenn eine Notiz genannt ist, aber keine Bubble offen ist (``bubble`` fehlt oder null)."""
    if not genannt:
        return "Es ist keine Bubble offen. Öffne die Bubble mit der Notiz und sag es noch einmal."
    return f"Es ist keine Bubble offen. Öffne die Bubble mit der Notiz „{genannt}“ und sag es noch einmal."


def _bubble_ohne_kennung(kontext: dict) -> bool:
    """Die App nennt eine offene Bubble (``bubble`` ist ein Eintrag), aber ohne Kennung der Datenbank."""
    bubble = kontext.get("bubble")
    return isinstance(bubble, dict) and bool(bubble) and not ist_kennung(bubble.get("id"))


def _bubble(name: Optional[str], kontext: dict) -> dict:
    """Die offene Bubble ("diese Bubble") oder genau eine Bubble mit diesem Titel."""
    offen = kontext.get("bubble") if isinstance(kontext.get("bubble"), dict) else None
    if not name or _normal(name) in ("diese", "dieser", "die", "der", "hier"):
        if offen and ist_kennung(offen.get("id")):
            return offen
        if offen:
            raise _Rueckfrage(_BUBBLE_UNKLAR)
        raise _Rueckfrage("Welche Bubble meinst du? Öffne sie oder nenn ihren Titel.")
    alle = [offen] if offen else []
    weitere = kontext.get("bubbles")
    if isinstance(weitere, list):
        alle += [b for b in weitere if isinstance(b, dict)]
    treffer = {b["id"]: b for b in alle if ist_kennung(b.get("id")) and _normal(b.get("title")) == _normal(name)}
    if len(treffer) == 1:
        return next(iter(treffer.values()))
    if len(treffer) > 1:
        raise _Rueckfrage(f"Es gibt {len(treffer)} Bubbles mit dem Titel „{name.strip()}“. Öffne die gemeinte.")
    if any(_normal(b.get("title")) == _normal(name) for b in alle):
        raise _Rueckfrage(_BUBBLE_UNKLAR)
    raise _Rueckfrage(f"Ich finde keine Bubble mit dem Titel „{name.strip()}“.")


# Obergrenzen des Ideen-Servers (spaces/ideas/mcp_server.py: ARRANGE_NODE_CAP,
# AUTO_LINK_NODE_CAP). Ordnen verschiebt jede Karte einzeln, Verbinden rechnet nur.
GRENZEN = {"idea.arrange": 100, "idea.auto_link": 500, "idea.group": 100, "idea.group_layout": 100}
_TAETIGKEIT = {"idea.arrange": "Ordnen", "idea.auto_link": "Verbinden nach gleichen Wörtern",
               "idea.group": "Gruppieren", "idea.group_layout": "Gruppieren"}


def ist_ideen_faehigkeit(capability: Any) -> bool:
    """Faehigkeiten des Ideen-Space, auch in der Schreibweise des Planers (idea_arrange)."""
    name = str(capability or "")
    return (name.startswith(("idea.", "idea_", "bubble.", "bubble_", "ideen_"))
            or name in ("aenderung_zurueck", "bewertung_begruenden"))


def grenzsatz(capability: Any, anzahl: Optional[int], grenze: Optional[int] = None) -> str:
    """"Ordnen geht bis 100 Notizen; hier sind es 101. ..." - sagt, was geht."""
    name = str(capability or "").replace("_", ".", 1)
    grenze = grenze or GRENZEN.get(name, 100)
    was = _TAETIGKEIT.get(name, "Das")
    hier = f"hier sind es {anzahl}" if anzahl else "hier sind es mehr"
    return (f"{was} geht bis {grenze} Notizen; {hier}. "
            "Verschiebe einen Teil der Notizen in eine eigene Bubble, dann geht es dort.")


def _grenze_pruefen(ereignis: str, kontext: dict) -> None:
    """Schon vor dem Aufruf sagen, dass es nicht geht (die App nennt die wahre Zahl)."""
    gesamt = kontext.get("cards_total")
    if isinstance(gesamt, int) and not isinstance(gesamt, bool) and gesamt > GRENZEN[ereignis]:
        raise _Rueckfrage(grenzsatz(ereignis, gesamt))


def _karte_und_rest(text: str, kopf: str, trenner: str, kontext: dict,
                    fuss: str = "") -> Optional[tuple[dict, str]]:
    """``kopf KARTE trenner REST fuss``: jede Trennstelle von links probieren.

    Namen koennen das Trennwort selbst enthalten ("Reise in Berlin"); genommen wird die
    erste Stelle, an der der linke Teil genau eine Karte ergibt. Ergibt keine Stelle eine
    Karte, kommt die erste Rueckfrage.
    """
    m = re.fullmatch(kopf + r"(?P<mitte>.+?)" + fuss, text, _I)
    if not m:
        return None
    mitte = m.group("mitte")
    erste: Optional[_Rueckfrage] = None
    for stelle in re.finditer(trenner, mitte, _I):
        name, rest = mitte[:stelle.start()].strip(), mitte[stelle.end():].strip()
        if not name or not rest:
            continue
        try:
            return _karte(name, kontext), rest
        except _Rueckfrage as frage:
            erste = erste or frage
    if erste is not None:
        raise erste
    return None


def _zahl(text: str) -> int:
    wert = int(text)
    if not 0 <= wert <= 100:
        raise _Rueckfrage("Die Bewertung geht von 0 bis 100.")
    return wert


def karten_im_text(text: str, kontext: dict) -> list[str]:
    """Kennungen der Karten, deren Titel woertlich im Satz steht (Reihenfolge im Satz).

    Laengere Titel zuerst ("Sponsoren Liste" vor "Sponsoren"); ein Titel, den mehrere
    Karten tragen, loest nichts auf (geraten wird nie). Titel unter drei Zeichen zaehlen nicht.
    """
    karten = [k for k in _karten(kontext) if isinstance(k.get("title"), str) and len(k["title"].strip()) >= 3]
    zaehler: dict[str, int] = {}
    for k in karten:
        zaehler[_normal(k["title"])] = zaehler.get(_normal(k["title"]), 0) + 1
    rest, gefunden = text, []
    for k in sorted(karten, key=lambda k: -len(k["title"].strip())):
        titel = k["title"].strip()
        muster = re.compile(r"(?<!\w)" + re.escape(titel).replace(r"\ ", r"\s+") + r"(?!\w)", _I)
        treffer = muster.search(rest)
        if not treffer:
            continue
        rest = rest[:treffer.start()] + " " * (treffer.end() - treffer.start()) + rest[treffer.end():]
        if zaehler[_normal(titel)] == 1:
            gefunden.append((treffer.start(), k["id"]))
    return [i for _, i in sorted(gefunden)]


def _treffer(ereignis: str, argumente: dict, antwort: str, capability: Optional[str] = None) -> dict:
    return {"ereignis": ereignis, "capability": capability or ereignis, "argumente": argumente, "antwort": antwort}


def _titel(karte: dict, ersatz: str = "die Karte") -> str:
    titel = str(karte.get("title") or "").strip()
    return f"„{titel}“" if titel else ersatz


def loese(satz: Any, kontext: Any) -> Optional[dict]:
    """Einen festen Satz des Ideen-Space aufloesen. Wirft nie."""
    if not isinstance(satz, str) or not isinstance(kontext, dict) or kontext.get("space") != "ideas":
        return None
    text = re.sub(r"\s+", " ", satz).strip().rstrip(".!")
    if not text or len(text) > 4000:
        return None
    zeilen = zeilen_text(satz)
    schluessel = _offen_schluessel(kontext)
    try:
        offen = _OFFEN.get(schluessel)
        if offen is not None and _jetzt() - offen["zeit"] > OFFEN_GILT_S:
            _OFFEN.pop(schluessel, None)
            offen = None
        if offen is not None:
            antwort = _auf_rueckfrage(text, offen, kontext)
            if antwort is not None:
                return antwort
        _OFFEN.pop(schluessel, None)
        return _loese(text, {**kontext, "_zeilen": zeilen})
    except _Rueckfrage as frage:
        _merken(schluessel, text, frage, zeilen)
        return {"rueckfrage": str(frage)}
    except Exception:
        return None


def zeilen_text(satz: Any) -> str:
    """Der Satz mit seinen Zeilen: Leerraum je Zeile zusammengezogen, leere Zeilen hoechstens einzeln.

    Erkannt wird ein Satz einzeilig (``text``); der Notiztext beim Festhalten und Ergaenzen
    behaelt die Zeilen (Lauf 13, (41): die Statuszeilen eines Kanban-Textes gingen verloren).
    """
    zeilen = [re.sub(r"[^\S\n]+", " ", z).strip() for z in str(satz or "").replace("\r\n", "\n").split("\n")]
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(zeilen))
    return text.strip().rstrip(".!")


def _mit_zeilen(kurz: str, kontext: dict) -> str:
    """Der Schluss des Satzes (``kurz``, einzeilig) mit den Zeilen des Originals; sonst ``kurz``."""
    zeilen = kontext.get("_zeilen")
    if not isinstance(zeilen, str) or "\n" not in zeilen:
        return kurz
    noetig = len(re.sub(r"\s", "", kurz))
    gezaehlt, start = 0, len(zeilen)
    while start > 0 and gezaehlt < noetig:
        start -= 1
        if not zeilen[start].isspace():
            gezaehlt += 1
    lang = zeilen[start:].strip()
    return lang if re.sub(r"\s+", " ", lang) == kurz else kurz


def _jetzt() -> float:
    import time
    return time.monotonic()


def _merken(schluessel: str, text: str, frage: _Rueckfrage, zeilen: Optional[str] = None) -> None:
    if len(frage.kandidaten) >= 2 and frage.name is not None:
        _OFFEN[schluessel] = {"satz": text, "name": frage.name, "kandidaten": frage.kandidaten,
                              "frage": str(frage), "zeit": _jetzt(), "zeilen": zeilen}
    else:
        _OFFEN.pop(schluessel, None)


def _auf_rueckfrage(text: str, offen: dict, kontext: dict) -> Optional[dict]:
    """Antwort auf die offene Rueckfrage: ``None`` = keine Antwort (neuer Satz, offen verfaellt)."""
    wahl = _wahl(text, offen["kandidaten"])
    if wahl is None:
        return None
    if wahl < 0:
        # Ein fester Satz geht immer vor; sonst noch einmal fragen, mit den Kandidaten.
        try:
            neu = _loese(text, kontext)
        except _Rueckfrage:
            neu = None
        if neu is not None:
            _OFFEN.pop(_offen_schluessel(kontext), None)
            return neu
        titel = [k.get("title") for k in offen["kandidaten"]]
        nummern = "; ".join(f"{n}. „{re.sub(chr(10), ' ', str(t or 'ohne Titel'))[:60]}“"
                            for n, t in enumerate(titel[:9], start=1))
        offen["zeit"] = _jetzt()
        return {"rueckfrage": f"Das passt zu keiner. Welche meinst du? {nummern}"}
    schluessel = _offen_schluessel(kontext)
    _OFFEN.pop(schluessel, None)
    gewaehlt = {**offen.get("gewaehlt", {}), offen["name"]: offen["kandidaten"][wahl]}
    try:
        return _loese(offen["satz"], {**kontext, "_gewaehlt": gewaehlt, "_zeilen": offen.get("zeilen")})
    except _Rueckfrage as frage:
        # Ein zweiter Name im selben Satz ist auch unklar ("Verbinde A mit B"): weiter fragen,
        # die erste Wahl bleibt.
        _merken(schluessel, offen["satz"], frage, offen.get("zeilen"))
        if schluessel in _OFFEN:
            _OFFEN[schluessel]["gewaehlt"] = gewaehlt
        return {"rueckfrage": str(frage)}


def _loese(text: str, kontext: dict) -> Optional[dict]:
    # ── Begruendung festhalten (vor "festhalten", weil der Auftakt aehnlich ist) ──
    m = (re.fullmatch(r"begr(?:ü|ue)ndung\s*:\s*(?P<t>.+)", text, _I)
         or re.fullmatch(r"halt(?:e)?\s+als\s+begr(?:ü|ue)ndung\s+fest\s*[,:]?\s*(?:dass\s+)?(?P<t>.+)", text, _I))
    if m:
        argumente: dict[str, Any] = {"bubble_id": _offene_bubble(kontext), "text": m.group("t").strip()}
        auswahl = [i for i in kontext.get("selection") or [] if ist_kennung(i)]
        # Karten, die der Satz beim Titel nennt, gehoeren dazu (im Betrieb stand sonst karten: []).
        auswahl += karten_im_text(argumente["text"], kontext)
        if auswahl:
            argumente["idea_ids"] = list(dict.fromkeys(auswahl))[:20]
        return _treffer("idea.rate_reason", argumente, "Begründung festgehalten.")

    # ── eine Begruendung loeschen: "Lösche die Begründung 2 [von X]" (gezaehlt wie angezeigt,
    #    neueste zuerst); ruecknehmbar ueber "Zurück", darum ohne Rueckfrage ──
    m = re.fullmatch(r"(?:l(?:ö|oe)sch(?:e)?|entfern(?:e)?)\s+(?:die\s+)?begr(?:ü|ue)ndung\s+"
                     r"(?:nr\.?\s*|nummer\s+)?(?P<n>\d{1,2})(?:\s+(?:von|zu|bei|an)\s+(?P<k>.+))?", text, _I)
    if m:
        argumente = {"bubble_id": _offene_bubble(kontext), "nummer": int(m.group("n"))}
        if not 1 <= argumente["nummer"] <= 50:
            raise _Rueckfrage("Begründungen zähle ich von 1 bis 50, die neueste zuerst.")
        bezug = (m.group("k") or "").strip().strip("\"'„“”")
        offen_titel = str((kontext.get("bubble") or {}).get("title") or "")
        zu = ""
        if bezug and _normal(bezug) not in ("dieser bubble", "der bubble", "diese bubble", "die bubble") \
                and _normal(re.sub(r"^(?:der|die|dieser)\s+bubble\s+", "", bezug, flags=_I)) != _normal(offen_titel):
            karte = _karte(bezug, kontext)
            argumente["idea_id"] = karte["id"]
            zu = f" zu {_titel(karte)}"
        return _treffer("idea.rate_reason_delete", argumente,
                        f"Begründung {argumente['nummer']}{zu} ist gelöscht. Mit „Zurück“ holst du sie wieder.")

    # ── Karte loeschen: nie direkt, zuerst die Rueckfrage (core/loesch_bestaetigung) ──
    m = re.fullmatch(rf"(?:l(?:ö|oe)sch(?:e)?|entfern(?:e)?)\s+(?P<k>{_KARTE}.+)", text, _I)
    if m:
        karte = _karte(m.group("k"), kontext)
        titel = str(karte.get("title") or "").strip()
        name = f"Die Karte „{titel}“" if titel else "Die Karte"
        return {**_treffer("idea.delete", {"id": karte["id"]},
                           f"{name} ist gelöscht, mit ihren Verbindungen. Mit „Zurück“ holst du sie wieder."),
                "loeschen": {"titel": titel, "bubble_id": _offene_bubble(kontext)}}

    # ── Bubble loeschen: nur die offene, nie direkt; die Rueckfrage nennt die Zahl der Karten ──
    m = re.fullmatch(r"(?:l(?:ö|oe)sch(?:e)?|entfern(?:e)?)\s+(?:diese|die)\s+bubble(?:\s+(?P<n>.+))?", text, _I)
    if m:
        bubble_id = _offene_bubble(kontext)
        bubble = kontext["bubble"]
        titel = str(bubble.get("title") or "").strip()
        genannt = (m.group("n") or "").strip().strip("\"'„“”")
        if genannt and _normal(genannt) != _normal(titel):
            raise _Rueckfrage(f"Gelöscht wird nur die offene Bubble. Öffne „{genannt}“ und sag dort "
                              "„Lösche diese Bubble“.")
        gesamt = kontext.get("cards_total")
        if not (isinstance(gesamt, int) and not isinstance(gesamt, bool) and gesamt >= 0):
            roh = kontext.get("cards")
            gesamt = len(roh) if isinstance(roh, list) else None
        name = f"Die Bubble „{titel}“" if titel else "Die Bubble"
        return {**_treffer("bubble.delete", {"id": bubble_id},
                           f"{name} ist gelöscht. Mit „Zurück“ holst du sie wieder, mit allen Karten."),
                "loeschen": {"art": "bubble", "titel": titel, "bubble_id": bubble_id, "karten": gesamt}}

    # ── festhalten ──
    m = re.fullmatch(
        r"(?:halt(?:e)?\s+fest|notiz|notiere|schreib(?:e)?\s+auf|merk(?:e)?\s+dir)\s*[:,]?\s*(?:dass\s+)?(?P<t>.+)",
        text, _I)
    if m:
        satz = _mit_zeilen(m.group("t").strip(), kontext)
        titel = _kurztitel(satz.split("\n", 1)[0])
        argumente = {"bubble_id": _offene_bubble(kontext), "title": titel}
        if titel != satz:
            argumente["content"] = satz
        if ist_kennung(kontext.get("new_id")):
            argumente["id"] = kontext["new_id"]
        return _treffer("idea.create", argumente, f"Notiz „{titel}“ festgehalten.")

    # ── ergaenzen (fester Aufruf statt Agentenlauf; der Server haengt an) ──
    for kopf, trenner in (
        (r"(?:erg(?:ä|ae)nze|f(?:ü|ue)ge)\s+(?:bei|zu|in|an)\s+", r"\s*(?:hinzu\s*)?:\s*"),
        (r"schreib(?:e)?\s+(?:bei|zu|in)\s+", r"\s*(?:dazu\s*)?:\s*"),
        (r"h(?:ä|ae)nge?\s+an\s+", r"\s+an\s*:\s*"),
        (r"erg(?:ä|ae)nze\s+", r"\s*:\s*"),
        (r"erg(?:ä|ae)nze\s+", r"\s+um\s*:?\s*"),
    ):
        gefunden = _karte_und_rest(text, kopf, trenner, kontext)
        if gefunden:
            karte, zusatz = gefunden
            zusatz = _mit_zeilen(zusatz, kontext)
            return _treffer("idea.update", {"id": karte["id"], "content_append": zusatz},
                            f"{_titel(karte)} ergänzt.")

    # ── umbenennen ──
    for kopf, trenner, fuss in (
        (r"(?:be)?nenne\s+", r"\s+um\s+in\s+", ""),
        (r"(?:be)?nenne\s+", r"\s+in\s+", r"\s+um"),
        (r"(?:ä|ae)ndere\s+den\s+(?:titel|namen)\s+(?:von\s+)?", r"\s+(?:in|zu|auf)\s+", ""),
        (r"gib\s+", r"\s+den\s+(?:titel|namen)\s+", ""),
    ):
        gefunden = _karte_und_rest(text, kopf, trenner, kontext, fuss)
        if gefunden:
            karte, neu = gefunden
            neu = neu.strip("\"'„“” ")
            return _treffer("idea.update", {"id": karte["id"], "title": neu},
                            f"{_titel(karte)} heißt jetzt „{neu}“.")

    # ── Urteil ueber die ganze Bubble (Lauf 5: ging an den Planer mit 30 s): Auftrag ueber den
    #    Rueckweg der Zusammenfassung, die Frage des Menschen ist der Auftrag (core/ideen_dokument).
    if urteilsfrage(text):
        return _treffer("idea.summarize", {"bubble_id": _offene_bubble(kontext), "einschaetzen": True},
                        "", capability="idea_summarize")

    # ── Modellauftraege mit Rueckweg (core/ideen_rueckweg): Karte hier aufloesen, der
    #    Agent bekommt nur Daten, das Gehirn schreibt. Antwort ist die Quittung des Auftrags.
    m = (re.fullmatch(r"formulier(?:e)?\s+(?P<k>.+?)\s+(?:noch\s+)?aus", text, _I)
         or re.fullmatch(r"schreib(?:e)?\s+(?P<k>.+?)\s+(?:noch\s+)?aus", text, _I))
    if m:
        karte = _karte(m.group("k"), kontext)
        return _treffer("idea.expand", {"id": karte["id"]}, "", capability="idea_expand")
    m = (re.fullmatch(r"was\s+h(?:ä|ae)ltst\s+du\s+von\s+(?P<k>.+?)\s*\??", text, _I)
         or re.fullmatch(r"sch(?:ä|ae)tz(?:e)?\s+(?P<k>.+?)\s+ein\s*\??", text, _I))
    if m:
        karte = _karte(m.group("k"), kontext)
        return _treffer("idea.explain", {"id": karte["id"]}, "", capability="idea_explain")

    # ── Zusammenfassung und White Paper (core/ideen_dokument): Bubble hier aufloesen,
    #    Karten, Wurzel, Teile und Budget prueft das Gehirn beim Anlegen des Auftrags.
    if re.fullmatch(_WP_ABBRUCH, text, _I):
        offen = kontext.get("bubble") if isinstance(kontext.get("bubble"), dict) else None
        bubble_id = offen.get("id") if offen and ist_kennung(offen.get("id")) else None
        return _treffer("idea.whitepaper_abbrechen", {"bubble_id": bubble_id}, "")
    if re.fullmatch(r"fasse\s+(?:die\s+)?markierten\s+(?:karten|notizen)\s+zusammen", text, _I):
        auswahl = list(dict.fromkeys(i for i in kontext.get("selection") or [] if ist_kennung(i)))
        if len(auswahl) < 2:
            raise _Rueckfrage("Markiere mindestens zwei Karten, dann fasse ich sie zusammen.")
        return _treffer("idea.summarize", {"bubble_id": _offene_bubble(kontext), "idea_ids": auswahl[:200]},
                        "", capability="idea_summarize")
    # Eine einzelne Karte (Lauf 5 der Pruefung: "Fasse die Notiz X zusammen" wurde als Bubble-Name
    # gelesen). Mit "Notiz/Karte" davor immer eine Karte; ein blosser Name nur, wenn genau eine
    # Karte so heisst und keine Bubble im Kontext.
    m = re.fullmatch(r"fasse\s+(?:die|diese|meine|unsere)\s+(?:notiz|karte)\s+(?P<k>.+?)\s+zusammen", text, _I)
    if m is None:
        n = re.fullmatch(r"fasse\s+(?P<k>.+?)\s+zusammen", text, _I)
        if n and not re.match(r"(?:die|diese)\s+bubble\b", n.group("k"), _I) and any(
                _vergleich(k.get("title")) == _vergleich(n.group("k")) for k in _karten(kontext)) and not any(
                isinstance(b, dict) and _vergleich(b.get("title")) == _vergleich(n.group("k"))
                for b in (kontext.get("bubbles") or [])):
            m = n
    if m:
        karte = _karte(m.group("k"), kontext)
        return _treffer("idea.summarize", {"bubble_id": _offene_bubble(kontext), "idea_ids": [karte["id"]]},
                        "", capability="idea_summarize")
    m = re.fullmatch(r"fasse\s+(?:(?:diese|die)\s+bubble|die\s+bubble\s+(?P<b>.+?)|(?P<n>.+?))\s+zusammen", text, _I)
    if m:
        bubble = _bubble(m.group("b") or m.group("n"), kontext)
        return _treffer("idea.summarize", {"bubble_id": bubble["id"]}, "", capability="idea_summarize")
    m = re.fullmatch(_WP_SATZ, text, _I)
    if m:
        bubble = _bubble(m.group("b") or m.group("n"), kontext)
        argumente = {"bubble_id": bubble["id"]}
        if m.group("z"):
            laeufe = int(m.group("z"))
            if not 1 <= laeufe <= 30:
                raise _Rueckfrage("Ein White Paper darf 1 bis 30 Läufe brauchen.")
            argumente["max_laeufe"] = laeufe
        # Nie direkt starten: der Router fragt zuerst mit dem Umfang (core/whitepaper_bestaetigung).
        return {**_treffer("idea.whitepaper", argumente, "", capability="idea_whitepaper"), "bestaetigen": True}

    # ── Wurzel der Bubble festlegen (fuer das White Paper, geht der berechneten vor) ──
    if re.fullmatch(r"(?:l(?:ö|oe)sch(?:e)?|entfern(?:e)?)\s+die\s+(?:feste|festgelegte|gesetzte)?\s*wurzel"
                    r"(?:\s+(?:dieser|der)\s+bubble)?|keine\s+feste\s+wurzel(?:\s+mehr)?", text, _I):
        return _treffer("idea.root", {"bubble_id": _offene_bubble(kontext)},
                        "Die festgelegte Wurzel ist weg; das White Paper berechnet sie wieder selbst.")
    m = (re.fullmatch(r"mach(?:e)?\s+(?P<k>.+?)\s+zur\s+wurzel(?:\s+(?:dieser|der)\s+bubble)?", text, _I)
         or re.fullmatch(r"setz(?:e)?\s+(?P<k>.+?)\s+als\s+wurzel(?:\s+(?:dieser|der)\s+bubble)?(?:\s+fest)?",
                         text, _I))
    if m:
        karte = _karte(m.group("k"), kontext)
        return _treffer("idea.root", {"bubble_id": _offene_bubble(kontext), "id": karte["id"]},
                        f"{_titel(karte)} ist jetzt die Wurzel dieser Bubble; das White Paper beginnt dort.")

    # ── formatieren ──
    m = (re.fullmatch(rf"mach(?:e)?\s+aus\s+(?P<k>.+?)\s+{_ARTIKEL}{_FORMAT}", text, _I)
         or re.fullmatch(rf"zeig(?:e)?\s+(?P<k>.+?)\s+als\s+{_ARTIKEL}{_FORMAT}", text, _I)
         or re.fullmatch(rf"formatiere\s+(?P<k>.+?)\s+als\s+{_ARTIKEL}{_FORMAT}", text, _I))
    if m:
        fmt = _format_aus(m.group("format"))
        if fmt is not None:
            karte = _karte(m.group("k"), kontext)
            return _treffer("idea.format", {"id": karte["id"], "format": fmt},
                            f"{_titel(karte)} ist jetzt als {_FORMAT_NAMEN[fmt]} dargestellt.")

    # ── ordnen (nur Raster und feste Reihenfolgen; "nach Thema" braucht ein Modell) ──
    m = re.fullmatch(r"sortiere\s+(?:die\s+)?(?:karten|notizen)\s+nach\s+(?P<o>bewertung|punkten|titel|name|namen)", text, _I)
    if m:
        order = "score" if m.group("o").casefold() in ("bewertung", "punkten") else "title"
        bubble_id = _offene_bubble(kontext)
        _grenze_pruefen("idea.arrange", kontext)
        return _treffer("idea.arrange", {"bubble_id": bubble_id, "order": order},
                        "Karten nach " + ("Bewertung" if order == "score" else "Titel") + " geordnet.")
    if (re.fullmatch(r"r(?:ä|ae)um(?:e)?\s+(?:das\s+canvas|die\s+karten|die\s+notizen|hier)\s+auf", text, _I)
            or re.fullmatch(r"ordne\s+(?:die\s+)?(?:notizen|karten)(?:\s+im\s+raster)?", text, _I)):
        bubble_id = _offene_bubble(kontext)
        _grenze_pruefen("idea.arrange", kontext)
        return _treffer("idea.arrange", {"bubble_id": bubble_id}, "Karten ins Raster geordnet.")

    # ── verlinken ──
    if re.fullmatch(r"(?:verlinke|verbinde|verkn(?:ü|ue)pfe)\s*,?\s+was\s+zusammen\s?geh(?:ö|oe)rt", text, _I) \
            or re.fullmatch(r"finde\s+verbindungen(?:\s+in\s+dieser\s+bubble)?", text, _I):
        bubble_id = _offene_bubble(kontext)
        _grenze_pruefen("idea.auto_link", kontext)
        return _treffer("idea.auto_link", {"bubble_id": bubble_id},
                        "Verbindungen nach gleichen Wörtern gesucht.")
    m = re.fullmatch(
        r"(?:verlinke|verbinde|verkn(?:ü|ue)pfe)\s+die\s+(?:beiden|zwei)(?:\s+(?:notizen|karten|markierten))?", text, _I)
    if m:
        auswahl = list(dict.fromkeys(i for i in kontext.get("selection") or [] if ist_kennung(i)))
        if len(auswahl) != 2:
            raise _Rueckfrage("Markiere genau zwei Karten, dann verbinde ich sie.")
        return _treffer("idea.connect", {"from_id": auswahl[0], "to_id": auswahl[1]},
                        "Die beiden Karten sind verbunden.", capability="idea_connect")
    m = re.fullmatch(r"(?:verlinke|verbinde|verkn(?:ü|ue)pfe)\s+(?P<a>.+?)\s+(?:mit|und)\s+(?P<b>.+)", text, _I)
    if m:
        a, b = _karte(m.group("a"), kontext), _karte(m.group("b"), kontext)
        if a["id"] == b["id"]:
            raise _Rueckfrage("Das ist zweimal dieselbe Karte. Welche zwei meinst du?")
        return _treffer("idea.connect", {"from_id": a["id"], "to_id": b["id"]},
                        f"{_titel(a)} und {_titel(b)} sind verbunden.", capability="idea_connect")

    # ── bewerten (nur mit genannter Zahl; ein Urteil braucht ein Modell) ──
    m = (re.fullmatch(r"bewerte\s+(?:diese\s+bubble|die\s+bubble(?:\s+(?P<b>.+?))?)\s+mit\s+(?P<z>\d{1,3})(?:\s+punkten)?",
                      text, _I)
         or re.fullmatch(r"gib\s+(?:dieser\s+bubble|der\s+bubble(?:\s+(?P<b>.+?))?)\s+(?P<z>\d{1,3})\s+punkte",
                         text, _I))
    if m:
        bubble = _bubble(m.group("b"), kontext)
        wert = _zahl(m.group("z"))
        return _treffer("bubble.rate", {"id": bubble["id"], "score": wert},
                        f"Bubble {_titel(bubble, 'ohne Titel')} mit {wert} bewertet.")
    m = (re.fullmatch(r"bewerte\s+(?P<k>.+?)\s+mit\s+(?P<z>\d{1,3})(?:\s+punkten)?", text, _I)
         or re.fullmatch(r"gib\s+(?P<k>.+?)\s+(?P<z>\d{1,3})\s+punkte", text, _I))
    if m:
        karte = _karte(m.group("k"), kontext)
        wert = _zahl(m.group("z"))
        return _treffer("idea.rate", {"id": karte["id"], "score": wert}, f"{_titel(karte)} mit {wert} bewertet.")

    # ── befoerdern (nur ganze Bubbles) ──
    m = (re.fullmatch(r"mach(?:e)?\s+aus\s+(?:dieser\s+bubble|der\s+bubble(?:\s+(?P<b>.+?))?)\s+ein\s+projekt", text, _I)
         or re.fullmatch(r"bef(?:ö|oe)rdere\s+(?:diese\s+bubble|die\s+bubble(?:\s+(?P<b>.+?))?)", text, _I))
    if m:
        bubble = _bubble(m.group("b"), kontext)
        return _treffer("bubble.promote", {"bubble_id": bubble["id"]},
                        f"Bubble {_titel(bubble, 'ohne Titel')} ist jetzt ein Projekt.")

    # ── lesen ──
    if (re.fullmatch(r"zeig(?:e)?\s+(?:mir\s+)?die\s+(?:notizen|karten)(?:\s+dieser\s+bubble)?", text, _I)
            or re.fullmatch(r"was\s+steht\s+hier\??", text, _I)):
        return _treffer("idea.list", {"bubble_id": _offene_bubble(kontext)}, "")
    # Zaehl- und Suchfragen ueber die Karten der offenen Bubble (Lauf 5: gingen an den Planer).
    # Fester Leseweg ueber die ganze Liste; Zahl und Treffer entstehen ohne Modell.
    frage = kartenfrage(text)
    if frage is not None:
        treffer = _treffer("idea.list", {"bubble_id": _offene_bubble(kontext), "full": True, "limit": 100}, "")
        treffer["kartenfrage"] = frage
        return treffer
    # Inhalt einer Karte (Arbeitslauf 2: ging als "nicht verstanden" oder an das Lexikon).
    # Fester Leseweg ueber die ganze Liste; der Satz zeigt nur die gefragte Karte.
    m = (re.fullmatch(r"was\s+steht\s+(?:in|bei|auf)\s+(?P<k>.+?)\s*\??", text, _I)
         or re.fullmatch(r"worum\s+geht\s+es\s+(?:in|bei)\s+(?P<k>.+?)\s*\??", text, _I)
         or re.fullmatch(r"(?:lies|lese)\s+(?:mir\s+)?(?P<k>.+?)\s+vor", text, _I)
         or re.fullmatch(r"zeig(?:e)?\s+(?:mir\s+)?(?:den\s+)?(?:inhalt|text)\s+(?:von\s+)?(?P<k>.+?)", text, _I))
    if m and _normal(m.group("k")) not in ("hier", "dieser bubble", "der bubble"):
        if bereichswort(m.group("k")) and not any(
                _vergleich(k.get("title")) in (_vergleich(m.group("k")), _vergleich(ohne_besitzwort(m.group("k"))))
                for k in _karten(kontext)):
            # "Was steht in meinem Journal?": ein Bereich, keine Karte (core/verstehen.bereichsfrage).
            return None
        karte = _karte(m.group("k"), kontext)
        treffer = _treffer("idea.list", {"bubble_id": _offene_bubble(kontext), "full": True, "limit": 100}, "")
        treffer["lies"] = {"id": karte["id"], "title": str(karte.get("title") or "")}
        return treffer
    # ── nach Thema gruppieren: Modellauftrag mit Rueckweg "gruppieren" (core/ideen_dokument).
    #    Der Agent nennt nur Gruppen mit Kuerzeln; Lage, Ueberschriften und (auf Wunsch)
    #    Verbindungen setzt das Gehirn in einer zuruecknehmbaren Aenderung (idea.group_layout).
    if (re.match(r"(?:bitte\s+)?(?:gruppiere|clustere|cluster)\b", text, _I)
            or re.fullmatch(r"(?:ordne|sortiere)\s+(?:die\s+)?(?:notizen|karten|ideen)\s+nach\s+(?:thema|themen|inhalt)",
                            text, _I)
            or re.fullmatch(r"(?:bilde|mach(?:e)?)\s+(?:themen)?gruppen(?:\s+.*)?", text, _I)
            or re.fullmatch(r"was\s+geh(?:ö|oe)rt\s+(?:hier\s+)?zusammen\s*\??", text, _I)):
        bubble_id = _offene_bubble(kontext)
        _grenze_pruefen("idea.group", kontext)
        verbinden = re.search(r"\bverbind\w*|\bverlink\w*|\bverkn(?:ü|ue)pf\w*", text, _I) is not None
        return _treffer("idea.group", {"bubble_id": bubble_id, **({"verbinden": True} if verbinden else {})},
                        "", capability="idea_group")
    # ── Bild an eine Karte: dafuer gibt es im Ideen-Space keinen Weg (das Ereignis
    #    idea.add_image zeigte auf ein Werkzeug, das es nicht gibt). Ehrlich sagen.
    if (re.search(r"\b(?:ein(?:e|en)?\s+|das\s+|die\s+)?(?:bild|bilder|foto|fotos|grafik|image|picture)\b", text, _I)
            and re.search(r"\b(?:f(?:ü|ue)g\w*|h(?:ä|ae)ng\w*|setz\w*|pack\w*|lad\w*|add|attach|einf(?:ü|ue)g\w*)\b",
                          text, _I)
            and re.search(r"\b(?:karte|notiz|idee|bubble|card|note|an\s+\w+|zu\s+\w+|in\s+\w+)\b", text, _I)):
        raise _Rueckfrage(BILD_NOCH_NICHT)
    # ── Recherche zu einer Bubble (core/recherche_bubble): erst die Vorschau als Rueckfrage,
    #    "Ja, recherchieren" startet den Lauf im Recherche-Server (spaces-research), der die
    #    Recherche-Karte selbst anlegt. Der alte Pruefweg research:* bleibt aus (Container und
    #    Agent sehen nie dieselbe Platte). "Recherchiere im Web/Netz/Internet ..." bleibt beim
    #    Browser-Weg und wird hier nicht abgefangen.
    m = re.match(r"(?:bitte\s+)?(?:recherchier\w*|mach(?:e)?\s+(?:eine\s+)?recherche|starte\s+(?:eine\s+)?recherche)"
                 r"\s+(?:zu|zum|zur|über|ueber|für|fuer|bei|an)\b\s*(?P<rest>.*)", text, _I)
    if m and not re.search(r"\bim\s+(?:web|netz|internet)\b", text, _I):
        return {"recherche": _recherche_argumente(m.group("rest"), kontext)}
    return None


RECHERCHE_THEMA_ZEICHEN = 500


def _recherche_argumente(rest: str, kontext: dict) -> dict:
    """Bubble und Thema aus "zu dieser Bubble[: Thema]", "zu der Bubble X[: Thema]" oder "zu THEMA"."""
    rest = rest.strip()
    thema = ""
    m = re.fullmatch(r"(?:dieser|diese|der|die)\s+bubble(?:\s+(?P<name>[^:]+?))?\s*(?::\s*(?P<thema>.+))?", rest, _I)
    if m:
        name = m.group("name")
        bubble = _bubble(name if name and _normal(name) not in ("hier",) else None, kontext)
        thema = (m.group("thema") or "").strip()
    else:
        bubble = _bubble(None, kontext)
        thema = rest
    if not thema:
        thema = f"das Thema der Bubble „{str(bubble.get('title') or '').strip() or 'ohne Titel'}“"
    if len(thema) > RECHERCHE_THEMA_ZEICHEN:
        raise _Rueckfrage(f"Das Thema ist zu lang (höchstens {RECHERCHE_THEMA_ZEICHEN} Zeichen). Fass es kürzer.")
    return {"bubble_id": bubble["id"], "bubble_titel": str(bubble.get("title") or ""), "thema": thema}


BILD_NOCH_NICHT = ("Das kann ich noch nicht: Bilder an Karten hängen. Im Ideen-Space gibt es dafür noch "
                   "keinen Weg; geändert habe ich nichts.")
LESE_ZEICHEN = 1500


_K = r"(?:karten|notizen|ideen)"
_ZAEHL_FUELLWORTE = frozenset(
    "gibt es sind stehen hat habe ich haben wir hier in dieser diese der die bubble da drin darin "
    "insgesamt gerade aktuell eigentlich noch jetzt schon".split())
_SUCHE = (
    rf"(?:welche|wie\s+viele)\s+{_K}\s+(?:handeln|gehen|sprechen|reden)\s+(?:von|(?:ü|ue)ber|um)\s+(?P<w>.+)",
    rf"(?:welche|wie\s+viele)\s+{_K}\s+(?:erw(?:ä|ae)hnen|enthalten|nennen)\s+(?P<w>.+)",
    rf"(?:welche|wie\s+viele)\s+{_K}\s+haben\s+(?:etwas\s+|was\s+)?mit\s+(?P<w>.+?)\s+zu\s+tun",
    rf"(?:welche|wie\s+viele)\s+{_K}\s+gibt\s+es\s+(?:zu|zum|zur|(?:ü|ue)ber)\s+(?P<w>.+)",
    rf"in\s+welche[nr]?\s+(?:karte|notiz|idee|{_K})\s+(?:steht|stehen|geht\s+es\s+um|kommt|kommen)\s+"
    rf"(?:etwas\s+|was\s+)?(?:(?:ü|ue)ber|zu|von)?\s*(?P<w>.+?)(?:\s+vor)?",
    r"wo\s+(?:steht|stand)\s+(?:etwas|was)\s+(?:(?:ü|ue)ber|zu|zum|zur|von)\s+(?P<w>.+)",
    rf"gibt\s+es\s+(?:eine\s+|schon\s+eine\s+)?(?:karte|notiz|idee|{_K})\s+(?:zu|zum|zur|(?:ü|ue)ber|mit)\s+(?P<w>.+)",
    rf"(?:durch)?such(?:e)?\s+(?:in\s+)?(?:den|meinen|die)\s+{_K}\s+nach\s+(?P<w>.+)",
    # Lauf 8: "Suche Marketing" / "Finde Marketing" gingen nicht in die Kartensuche.
    rf"(?:such(?:e)?|finde?)\s+(?:nach\s+)?(?:(?:die|alle)\s+)?(?:(?:{_K}|karte|notiz|idee)\s+(?:zu|zum|zur|mit|(?:ü|ue)ber)\s+)?"
    rf"(?P<w>(?!heraus\b|im\s|in\s+(?:web|netz|internet)\b|(?:dir|mir|mal|bitte|uns)\b)[^\s].*?)"
    rf"(?:\s+(?:hier|in\s+(?:dieser|der)\s+bubble|in\s+den\s+{_K}))?",
    rf"zeig(?:e)?\s+(?:mir\s+)?(?:alle\s+)?{_K}\s+(?:mit|zu|zum|zur|(?:ü|ue)ber)\s+(?P<w>.+)",
)
_SUCHWORT_WOERTER = 4


_SCHWAECHE = (r"(?:risiken|schw(?:ä|ae)chen|schwachstellen|st(?:ä|ae)rken|l(?:ü|ue)cken|probleme|chancen|"
              r"widerspr(?:ü|ue)che)")
_HIER = r"(?:\s+(?:hier|darin|da|noch|in\s+(?:dieser|der)\s+bubble|bei\s+(?:dieser|der)\s+bubble))*"
_URTEIL = (
    rf"welche\s+{_SCHWAECHE}\s+(?:siehst|erkennst|findest)\s+du{_HIER}",
    rf"was\s+(?:sind|w(?:ä|ae)ren)\s+(?:hier\s+)?(?:die\s+)?(?:gr(?:ö|oe)(?:ß|ss)ten\s+)?{_SCHWAECHE}{_HIER}",
    rf"(?:siehst|erkennst)\s+du\s+(?:hier\s+)?{_SCHWAECHE}{_HIER}",
    rf"was\s+fehlt{_HIER}",
    # "Was haeltst du davon?" bleibt beim bisherigen Weg: "davon" meint oft das Gesagte im Chat.
    rf"was\s+h(?:ä|ae)ltst\s+du\s+von\s+(?:dieser|der)\s+bubble",
    rf"sch(?:ä|ae)tz(?:e)?\s+(?:diese|die)\s+bubble\s+ein",
    rf"wie\s+sch(?:ä|ae)tzt\s+du\s+(?:diese|die)\s+bubble\s+ein",
    # Lauf 7: ohne Zahl fiel "Bewerte diese Bubble" auf bubble.evaluate (Auftrag an brain-bubbles,
    # ohne Werkzeug). Mit Zahl bleibt es der feste Satz bubble.rate.
    rf"bewerte\s+(?:diese|die)\s+bubble",
    rf"(?:ist|w(?:ä|ae)re)\s+(?:diese|die)\s+bubble\s+(?:schon\s+)?(?:reif|fertig|bereit)"
    rf"(?:\s+f(?:ü|ue)r\s+(?:die\s+|das\s+|den\s+|ein\s+|eine\s+)?\w+)?",
)


def urteilsfrage(text: Any) -> bool:
    """Eine Frage nach einem Urteil ueber die ganze offene Bubble ("Welche Risiken siehst du?")."""
    if not isinstance(text, str):
        return False
    text = re.sub(r"\s+", " ", text).strip().rstrip(".!?").strip()
    return any(re.fullmatch(muster, text, _I) for muster in _URTEIL)


def _stichwort(wort: str) -> str:
    wort = re.sub(r"\s+", " ", wort).strip().strip("?\"'„“”‚‘’«» ")
    return re.sub(r"^(?:dem|den|der|die|das|des|einem|einer|einen|eine|ein)\s+", "", wort, flags=_I).strip()


def kartenfrage(text: Any) -> Optional[dict]:
    """Zaehl- oder Suchfrage ueber die Karten der offenen Bubble, sonst ``None``.

    ``{"art": "zahl"}`` oder ``{"art": "suche", "wort": "Preise"}``. Ein Urteil
    ("Welche Risiken siehst du?") ist keine Kartenfrage.
    """
    if not isinstance(text, str):
        return None
    text = re.sub(r"\s+", " ", text).strip().rstrip(".!?").strip()
    for muster in _SUCHE:
        m = re.fullmatch(muster, text, _I)
        if m:
            wort = _stichwort(m.group("w"))
            if len(wort.split()) > _SUCHWORT_WOERTER or re.search(r"\b(?:web|netz|internet|google)\b", wort, _I):
                return None
            return {"art": "suche", "wort": wort} if len(wort) >= 2 else None
    m = re.fullmatch(rf"wie\s+viele\s+{_K}(?P<rest>(?:\s+\w+)*)", text, _I)
    if m and all(w.casefold() in _ZAEHL_FUELLWORTE for w in m.group("rest").split()):
        return {"art": "zahl"}
    return None


def _kartentext(zeile: dict) -> str:
    struktur = zeile.get("content_json") if isinstance(zeile.get("content_json"), dict) else {}
    teile = [zeile.get("title"), zeile.get("content"), struktur.get("text")]
    return " ".join(t for t in teile if isinstance(t, str))


def _falte(text: Any) -> str:
    """Zum Suchen: klein, Umlaute ausgeschrieben, ß als ss, ohne Binde- und Unterstrich."""
    text = _normal(text)
    for a, b in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss"), ("-", ""), ("_", " ")):
        text = text.replace(a, b)
    return text


def _kerne(wort: str) -> list[str]:
    """Das Stichwort und, ab fuenf Buchstaben, seine Form ohne Endung ("Preisen" findet "Preis",
    "Planungen" findet "Planung"). Gross/klein und Umlaut-Schreibung zaehlen nicht."""
    wort = _falte(wort)
    kerne = [wort]
    for endung in ("ungen", "ern", "en", "er", "es", "n", "e", "s"):
        if " " not in wort and wort.endswith(endung) and len(wort) - len(endung) >= 4:
            kern = wort[: -len(endung)] + ("ung" if endung == "ungen" else "")
            if kern not in kerne:
                kerne.append(kern)
    return kerne


WOERTLICH = "Gesucht habe ich nach dem Wort und seinen Formen, nicht nach der Bedeutung."


def kartenantwort(zeilen: list, frage: dict, ort: str, gesamt: Optional[int], gekuerzt: bool) -> str:
    """Der Satz zu einer Zaehl- oder Suchfrage aus den ganzen Zeilen von idea_list (full)."""
    karten = [z for z in zeilen if isinstance(z, dict)]
    if frage.get("art") == "zahl":
        if gesamt is not None and gesamt >= len(karten):
            anzahl = gesamt
        elif gekuerzt:
            return f"In {ort} gibt es mindestens {_mehrzahl(len(karten), 'Notiz', 'Notizen')}."
        else:
            anzahl = len(karten)
        return (f"In {ort} gibt es noch keine Notizen." if anzahl == 0
                else f"In {ort} gibt es {_mehrzahl(anzahl, 'Notiz', 'Notizen')}.")
    wort = str(frage.get("wort") or "")
    kerne = _kerne(wort)
    gefunden = [z.get("title") for z in karten if any(k in _falte(_kartentext(z)) for k in kerne)]
    zusatz = ""
    if (gesamt is not None and gesamt > len(karten)) or (gesamt is None and gekuerzt):
        von = f" von {gesamt}" if gesamt is not None and gesamt > len(karten) else ""
        zusatz = f" Durchsucht habe ich nur die ersten {len(karten)}{von} Notizen."
    if not gefunden:
        return f"Keine Notiz in {ort} erwähnt „{wort}“. {WOERTLICH}{zusatz}"
    verb = "erwähnt" if len(gefunden) == 1 else "erwähnen"
    return (f"{_mehrzahl(len(gefunden), 'Notiz', 'Notizen')} in {ort} {verb} „{wort}“: "
            f"{_aufzaehlung(gefunden, zeige=5)}.{zusatz}")


def karteninhalt(zeilen: Any, karte: Any) -> Optional[str]:
    """Der Satz zu "Was steht bei X?" aus den ganzen Zeilen von idea_list (full)."""
    if not isinstance(karte, dict) or not isinstance(zeilen, list):
        return None
    titel = karte.get("title") or ""
    name = f"„{titel}“" if titel else "Die Karte"
    zeile = next((z for z in zeilen if isinstance(z, dict) and z.get("id") == karte.get("id")), None)
    if zeile is None:
        return f"{name} kann ich gerade nicht lesen."
    titel = str(zeile.get("title") or titel or "")
    name = f"„{titel}“" if titel else "Die Karte"
    struktur = zeile.get("content_json") if isinstance(zeile.get("content_json"), dict) else {}
    text = struktur.get("text") if isinstance(struktur.get("text"), str) else ""
    if not text.strip() or text.strip() == titel.strip():
        text = zeile.get("content") if isinstance(zeile.get("content"), str) else ""
    text = text.strip()
    if not text or text == titel.strip():
        return f"{name} hat noch keinen Text."
    if len(text) > LESE_ZEICHEN:
        text = text[:LESE_ZEICHEN].rstrip() + " …"
    return f"{name}:\n{text}"


_FELDNAMEN = {
    "bubble_id": "die Bubble", "id": "die Karte", "from_id": "die erste Karte", "to_id": "die zweite Karte",
    "title": "der Titel", "content": "der Inhalt", "text": "der Text", "score": "die Zahl",
    "format": "das Format", "idea_ids": "die verlinkten Karten", "order": "die Reihenfolge",
    "columns": "die Spaltenzahl", "limit": "die Anzahl", "aenderung_id": "die Kennung der Änderung",
}
_WERKZEUGFEHLER = {
    "idea_not_found": "Diese Karte gibt es nicht mehr.",
    "bewertung_not_found": "Eine Begründung mit dieser Nummer gibt es hier nicht. Gezählt wird die neueste zuerst.",
    "bubble_not_found": "Diese Bubble gibt es nicht mehr.",
    "idea_not_in_bubble": "Eine der Karten gehört nicht zu dieser Bubble.",
    "too_many_ideas": "In dieser Bubble liegen mehr als 100 Karten. Das geht nur bis 100.",
    "idea_format_nothing_to_revert": "Für diese Karte gibt es kein früheres Format.",
    "idea_connect_node_unverified": "Eine der beiden Karten gibt es nicht mehr.",
    "supabase_unreachable": "Die Datenbank ist gerade nicht erreichbar.",
    "supabase_http_error": "Die Datenbank hat die Änderung abgelehnt.",
    "supabase_invalid_response": "Die Datenbank hat unlesbar geantwortet.",
    "configuration_error": "Dem Ideen-Dienst fehlt der Zugang zur Datenbank.",
    "bubble_promotion_create_failed": "Das Befördern ist gescheitert. Es wurde kein Projekt angelegt.",
    "bubble_promotion_link_failed": "Das Befördern ist gescheitert. Die Bubble ist unverändert.",
    "bubble_promotion_create_unverified": "Das Befördern ließ sich nicht bestätigen. Bitte prüfe die Projekte.",
}


def _feld(name: str) -> str:
    return _FELDNAMEN.get(name, f"„{name}“")


# Fehlertexte, bei denen nichts schiefging: das Gehirn hat bewusst nichts geaendert und
# sagt warum (Hinweis) oder fragt nach (Rueckfrage). Die App zeigt das ruhig, nicht als ✕.
_HINWEISE = ("idea_format_table_no_columns", "idea_format_no_content", "Läufe; die Grenze ist",
             "mehr als 30 starte ich nicht", "starte ich nur nach Rückfrage",
             "Gelöscht wird nur nach Rückfrage")
_RUECKFRAGEN = ("aenderung_zurueck_frage: ", "mehrere Kennungen")


def fehlerart(fehler: Any) -> Optional[str]:
    """``"hinweis"``, ``"rueckfrage"`` oder ``None`` (ein echter Fehler)."""
    text = str(fehler or "")
    if any(m in text for m in _RUECKFRAGEN):
        return "rueckfrage"
    if any(m in text for m in _HINWEISE):
        return "hinweis"
    return None


def fehlersatz(fehler: Any, capability: Any = None) -> str:
    """Ein deutscher Satz fuer den Chat aus dem Fehlertext eines festen Schritts.

    Der technische Text bleibt im Ergebnis des Schritts stehen; hier entsteht nur, was
    der Mensch liest. Es kommen Feldnamen und Fehlernamen durch, nie Werte. Gilt auch fuer
    den Planer-Weg (``capability`` dort z. B. "idea_arrange").
    """
    text = str(fehler or "")
    m = re.search(r"ideen_rueckweg: (?P<satz>[^\n]{1,300})", text)
    if m:
        # Der Rueckweg nennt schon einen deutschen Satz ohne Werte (core/ideen_rueckweg).
        return m.group("satz").strip()
    m = re.search(r"Gelöscht wird nur nach Rückfrage\.[^\n]{0,200}", text)
    if m:
        # core/loesch_bestaetigung: ohne "Ja, löschen" direkt nach der Rueckfrage wird nichts geloescht.
        return m.group(0).strip()
    if "too_many_ideas" in text:
        anzahl = re.search(r"(?:anzahl=|: )(\d{1,6})(?: ideas)?", text)
        grenze = re.search(r"(?:grenze=|limit )(\d{1,6})", text)
        return grenzsatz(capability, int(anzahl.group(1)) if anzahl else None,
                         int(grenze.group(1)) if grenze else None)
    if "idea_format_table_no_columns" in text:
        return ("Der Text dieser Karte hat keine Spalten; geändert habe ich nichts. Aus freiem Text eine "
                "Tabelle bauen kann ich noch nicht. Schreib je Zeile „Name: Wert“ oder trenne die Spalten "
                "mit „;“, dann geht „Mach aus … eine Tabelle“.")
    if "idea_format_no_content" in text:
        return ("Diese Karte hat noch keinen Text, aus dem ich eine Tabelle oder Liste bauen kann. "
                "Ergänze erst Inhalt, z. B. „Ergänze bei … : …“.")
    if re.search(r"Idea '[^']*' not found", text):
        # Aelterer Planer-Weg (supabase_ideas_ops): der Name kam nicht an.
        return "Diese Notiz finde ich nicht. Nenn ihren Titel oder markiere sie."
    if "Ideen-Dienst nicht erreichbar" in text:
        return ("Der Ideen-Dienst läuft gerade nicht. Bitte prüfe das Canvas, bevor du es wiederholst."
                if "transport_closed" in text
                else "Der Ideen-Dienst hat nicht geantwortet. Es ist unklar, ob etwas geändert wurde.")
    if "aenderung_zurueck_frage: " in text:
        # core/zurueck_vorsicht: nennt, was zurueckgenommen wuerde; nichts geaendert.
        return text.split("aenderung_zurueck_frage: ", 1)[1].strip()
    if "aenderung_konflikt" in text:
        return "Das lässt sich nicht zurücknehmen: Ein Eintrag wurde danach noch einmal geändert."
    if "aenderung_unbekannt" in text:
        return "Diese Änderung kenne ich nicht."
    if "aenderung_nicht_zuruecknehmbar" in text:
        return "Diese Änderung lässt sich nicht zurücknehmen."
    if "mehrere Kennungen" in text:
        return "Du nennst mehrere Änderungen. Welche soll ich zurücknehmen?"
    if "keine lesbare Antwort" in text or "keine gueltige Quittung" in text:
        return "Der Ideen-Dienst hat unlesbar geantwortet. Bitte prüfe das Canvas."
    m = re.search(r"Ideen-Werkzeug meldet einen Fehler(?: \((?P<name>[a-z_]+)(?:: (?P<feld>[a-z_]+))?\))?", text)
    if m:
        name, feld = m.group("name"), m.group("feld")
        if name == "invalid_arguments":
            return (f"Das Werkzeug hat eine Angabe abgelehnt: {_feld(feld)}." if feld
                    else "Das Werkzeug hat die Angaben abgelehnt.")
        if name in _WERKZEUGFEHLER:
            return _WERKZEUGFEHLER[name]
        return f"Das Werkzeug meldet einen Fehler ({name})." if name else "Das Werkzeug meldet einen Fehler."
    m = re.search(r"clarification required: (?:unknown field (?P<fremd>[a-z_]+)|(?P<feld>[a-z_]+) is invalid"
                  r"|one of (?P<eins>[a-z_, ]+)|(?P<pflicht>[a-z_, ]+))", text)
    if m:
        if m.group("fremd"):
            return f"Diese Angabe kennt das Werkzeug nicht: {_feld(m.group('fremd'))}."
        if m.group("feld"):
            return f"Diese Angabe ist ungültig: {_feld(m.group('feld'))}."
        namen = [n.strip() for n in (m.group("eins") or m.group("pflicht") or "").split(",") if n.strip()]
        bekannt = [_feld(n) for n in namen if re.fullmatch(r"[a-z_]+", n)]
        if bekannt:
            return "Mir fehlt eine Angabe: " + (", ".join(bekannt[:-1]) + " oder " + bekannt[-1]
                                                if m.group("eins") and len(bekannt) > 1
                                                else ", ".join(bekannt)) + "."
        return "Mir fehlt eine Angabe."
    if "truth unverified" in text:
        return "Ich konnte nicht bestätigen, dass die Änderung gespeichert ist. Bitte prüfe das Canvas."
    if "validator error" in text or "validator unavailable" in text:
        return "Die Nachprüfung war nicht möglich. Die Änderung ist nicht bestätigt."
    if "approval pending" in text or "pending_approval" in text:
        return "Das wartet auf eine Freigabe."
    if re.search(r"time[\s_-]?out|timed out|Zeitüberschreitung|Zeitueberschreitung", text, re.IGNORECASE):
        return ("Der Dienst hat nicht rechtzeitig geantwortet. Es ist unklar, ob etwas geändert wurde; "
                "bitte prüfe das Canvas, bevor du es wiederholst.")
    if "outcome_unknown" in text or "in_progress" in text:
        return "Es ist unklar, ob die Änderung angekommen ist. Bitte prüfe das Canvas, bevor du es wiederholst."
    if "isError=true" in text:
        return "Das Werkzeug hat den Aufruf abgelehnt."
    if "not registered" in text or "execution denied" in text or "PermissionError" in text:
        return "Der Agent darf dieses Werkzeug nicht benutzen."
    if "OpenFang" in text or "OPENFANG" in text:
        return "Der Agenten-Dienst ist gerade nicht erreichbar."
    return "Das hat nicht geklappt."


def _mcp_json(ergebnis: Any) -> Any:
    """Der JSON-Inhalt aus der Antwort eines Werkzeugs, sonst ``None``."""
    if not isinstance(ergebnis, dict):
        return None
    teile = ergebnis.get("content")
    if not isinstance(teile, list) or len(teile) != 1 or not isinstance(teile[0], dict):
        return None
    text = teile[0].get("text")
    if not isinstance(text, str):
        return None
    try:
        return json.loads(text)
    except ValueError:
        return None


def _aufzaehlung(titel: list[str], zeige: int = 3, gesamt: Optional[int] = None,
                 unbekannt_mehr: bool = False) -> str:
    """„A“, „B“, „C“ (und 9 weitere) - nur Titel, nie Kennungen.

    ``gesamt`` ist die wahre Zahl, wenn die Liste gekuerzt ist; ``unbekannt_mehr``: es gibt
    mehr, aber die Zahl ist nicht bekannt.
    """
    sauber = [re.sub(r"\s+", " ", str(t or "")).strip() for t in titel]
    sauber = [(t if len(t) <= 60 else t[:57].rstrip() + "…") or "ohne Titel" for t in sauber]
    kopf = ", ".join(f"„{t}“" for t in sauber[:zeige])
    rest = (gesamt if gesamt is not None else len(sauber)) - min(zeige, len(sauber))
    if unbekannt_mehr:
        return kopf + " (und weitere)"
    return kopf + (f" (und {rest} weitere)" if rest > 0 else "")


def _ganzzahl(wert: Any) -> Optional[int]:
    return wert if isinstance(wert, int) and not isinstance(wert, bool) and wert >= 0 else None


# So viele Karten liefert idea_list ohne "limit"; eine aeltere Server-Form (blosse Liste)
# mit genau so vielen Eintraegen kann gekuerzt sein.
IDEA_LIST_VORGABE = 50


def _mehrzahl(anzahl: int, eins: str, viele: str) -> str:
    return f"{anzahl} {eins if anzahl == 1 else viele}"


def zurueck_was(inhalt: dict, anzahl: int) -> str:
    """Lauf 18 (51): was "zurück" zurückgenommen hat, mit Karte und Art statt nur der Zahl.

    ``eintraege`` (Tabelle, Vorgang, Titel) kommen aus der Datenbank, ``arten`` (Zahl je Art:
    text, titel, position, ueberschrift, karte_neu, ...) vom Ideen-Server, wenn er sie lesen konnte.
    Fehlt beides, bleibt es bei der Zahl.
    """
    zahl = _mehrzahl(anzahl, "Änderung", "Änderungen")
    eintraege = [e for e in inhalt.get("eintraege") or [] if isinstance(e, dict)]
    roh = inhalt.get("arten")
    arten = {k: v for k, v in roh.items() if _ganzzahl(v)} if isinstance(roh, dict) else {}
    gruppe = inhalt.get("gruppe") if isinstance(inhalt.get("gruppe"), str) else ""

    def name(e: dict) -> str:
        t = re.sub(r"\s+", " ", str(e.get("titel") or e.get("name") or "")).strip()
        return f"„{t if len(t) <= 60 else t[:57].rstrip() + '…'}“" if t else ""

    if anzahl == 1 and len(eintraege) == 1:
        e = eintraege[0]
        tabelle, vorgang, n = e.get("tabelle"), e.get("vorgang"), name(e)
        if tabelle == "public.canvas_nodes" and n:
            if vorgang == "INSERT":
                return f"die neue Karte {n}"
            if vorgang == "DELETE":
                return f"das Löschen von {n}; die Karte ist wieder da"
            art = next(iter(arten)) if len(arten) == 1 else ""
            if art == "text":
                # Den Text einer Karte aendert ein Folgeplan (Plan-Kennung mit "-r") nur beim Ausformulieren.
                return (f"der ausformulierte Text von {n}" if gruppe.endswith("-r")
                        else f"die Textänderung an {n}")
            if art == "titel":
                return f"die Umbenennung; die Karte heißt wieder {n}"
            if art == "position":
                return f"die Verschiebung von {n}"
            return f"die Änderung an {n}"
        if tabelle == "public.canvas_edges":
            return {"INSERT": "die neue Verbindung", "DELETE": "das Lösen einer Verbindung; sie ist wieder da"}.get(
                vorgang, "die Änderung an einer Verbindung")
        if tabelle == "public.ideas" and n:
            return {"INSERT": f"die neue Bubble {n}",
                    "DELETE": f"das Löschen der Bubble {n}; sie ist wieder da"}.get(vorgang, f"die Änderung an der Bubble {n}")
        return zahl
    positionen = arten.get("position", 0)
    if positionen and arten.get("ueberschrift"):
        return f"die Gruppierung ({_mehrzahl(positionen, 'Position', 'Positionen')})"
    if positionen and set(arten) == {"position"}:
        return f"die Anordnung ({_mehrzahl(positionen, 'Position', 'Positionen')})"
    titel = list(dict.fromkeys(t for t in (str(e.get("titel") or e.get("name") or "").strip() for e in eintraege) if t))
    if titel and len(eintraege) == anzahl:
        return f"{zahl} an {_aufzaehlung(titel)}"
    return zahl


def ziel_fuer_schritt(capability: Any, ok: bool, argumente: Any, ergebnis: Any,
                      kontext: Any = None) -> Optional[dict]:
    """(33) Wohin die App nach einem festen Schreibaufruf springt: ``{space, bubble_id, node_id}``.

    Nur wenn der Aufruf geklappt hat, schreibt und eine Bubble kennt (aus den Argumenten oder
    der offenen bzw. im Satz genannten Bubble des Kontexts). ``node_id`` ist die Karte, die
    geschrieben wurde, sonst ``None``. Wirft nie.
    """
    try:
        from .ideen_fest_contract import FESTE_EREIGNISSE

        spec = FESTE_EREIGNISSE.get(str(capability or ""))
        if not ok or not spec or spec.get("liest_nur"):
            return None
        if isinstance(argumente, str):
            try:
                argumente = json.loads(argumente)
            except ValueError:
                argumente = {}
        argumente = argumente if isinstance(argumente, dict) else {}
        bubble_id = argumente.get("bubble_id")
        if str(capability).startswith("bubble.") and not ist_kennung(bubble_id):
            bubble_id = argumente.get("id")          # bubble.rate: die Kennung ist die Bubble
        if not ist_kennung(bubble_id) and isinstance(kontext, dict) and isinstance(kontext.get("bubble"), dict):
            bubble_id = kontext["bubble"].get("id")
        if not ist_kennung(bubble_id):
            return None
        node_id = None
        if str(capability) == "bubble.delete":
            # Die Bubble ist weg: kein Sprung hinein (die App verlaesst sie selbst).
            return None
        if str(capability) in ("idea.delete", "idea.rate_reason_delete"):
            # Die Karte (bzw. die Begruendung) ist weg: in die Bubble springen, nie auf die Kennung der geloeschten Karte.
            return {"space": "ideas", "bubble_id": bubble_id, "node_id": None}
        if str(capability).startswith("idea."):
            node_id = argumente.get("id")
            if not ist_kennung(node_id):
                # Lauf 11 (37): Der Ideen-Server antwortet beim Anlegen mit der neuen Zeile als
                # Liste ("[{...}]", PostgREST), beim schon vorhandenen Titel mit der Zeile selbst.
                # Gelesen wie in der Nachpruefung (core/ideen_fest_contract).
                from .ideen_fest_contract import _erste_zeile, _mcp_inhalt

                inhalt, gefunden = _mcp_inhalt(ergebnis)
                if not gefunden and isinstance(ergebnis, str):
                    try:
                        inhalt = json.loads(ergebnis)
                    except ValueError:
                        inhalt = None
                elif not gefunden:
                    inhalt = ergebnis
                zeile = _erste_zeile(inhalt)
                node_id = zeile.get("id") if zeile else None
        return {"space": "ideas", "bubble_id": bubble_id, "node_id": node_id if ist_kennung(node_id) else None}
    except Exception:  # noqa: BLE001
        return None


def antwort_fuer_schritt(capability: Any, ok: bool, ergebnis: Any, fehler: Any,
                         vorgabe: str = "", kontext: Any = None) -> Optional[str]:
    """Der deutsche Satz fuer den Chat nach einem festen Schritt.

    ``vorgabe`` ist der Satz der Abkuerzung ("Notiz ... festgehalten."). Wo das Ergebnis
    mehr sagt (Liste, Anzahl), entsteht der Satz aus dem Ergebnis. ``None`` heisst: kein
    fester Satz moeglich, der Aufrufer nimmt seinen bisherigen Weg. Wirft nie.
    """
    try:
        if not ok:
            return fehlersatz(fehler, capability)
        if capability == "idea.list":
            # Die Zahl im Satz ist die Gesamtzahl aus der Zaehlung des Ideen-Servers, nie die
            # Laenge der gekuerzten Liste (23.11.2026: "50 Notizen" bei einer Bubble mit mehr Karten).
            inhalt = _mcp_json(ergebnis)
            if isinstance(inhalt, dict) and isinstance(inhalt.get("karten"), list):
                zeilen = inhalt["karten"]
                gekuerzt = inhalt.get("gekuerzt") is True
                gesamt = _ganzzahl(inhalt.get("gesamt"))
            elif isinstance(inhalt, list):  # aeltere Server-Form: blosse Liste, ohne Zaehlung
                zeilen = inhalt
                gekuerzt = len(zeilen) >= IDEA_LIST_VORGABE
                gesamt = None if gekuerzt else len(zeilen)
            else:
                return vorgabe or None
            if isinstance(kontext, dict) and isinstance(kontext.get("_lies_karte"), dict):
                return karteninhalt(zeilen, kontext["_lies_karte"])
            bubble = kontext.get("bubble") if isinstance(kontext, dict) else None
            ort = (str(bubble.get("title") or "").strip() if isinstance(bubble, dict) else "")
            ort = f"„{ort}“" if ort else "dieser Bubble"
            if isinstance(kontext, dict) and isinstance(kontext.get("_kartenfrage"), dict):
                return kartenantwort(zeilen, kontext["_kartenfrage"], ort, gesamt, gekuerzt)
            if not zeilen:
                return f"In {ort} gibt es noch keine Notizen."
            titel = [z.get("title") for z in zeilen if isinstance(z, dict)]
            if gesamt is None or gesamt < len(titel):
                if gekuerzt:
                    return f"Mindestens {len(titel)} Notizen in {ort}: {_aufzaehlung(titel, unbekannt_mehr=True)}."
                gesamt = len(titel)
            return f"{_mehrzahl(gesamt, 'Notiz', 'Notizen')} in {ort}: {_aufzaehlung(titel, gesamt=gesamt)}."
        if capability == "idea.auto_link":
            inhalt = _mcp_json(ergebnis)
            anzahl = inhalt.get("anzahl") if isinstance(inhalt, dict) else None
            if isinstance(anzahl, int) and not isinstance(anzahl, bool):
                return ("Ich habe keine neuen Verbindungen gefunden." if anzahl == 0
                        else f"{_mehrzahl(anzahl, 'Verbindung', 'Verbindungen')} angelegt.")
            return vorgabe or None
        if capability == "idea.arrange":
            inhalt = _mcp_json(ergebnis)
            if isinstance(inhalt, dict) and isinstance(inhalt.get("anzahl"), int):
                if inhalt["anzahl"] == 0:
                    return "Hier gibt es noch keine Karten zum Ordnen."
                if inhalt.get("verschoben") == 0:
                    return "Die Karten stehen schon so."
            return vorgabe or None
        if capability == "aenderung_zurueck":
            inhalt = _mcp_json(ergebnis)
            anzahl = inhalt.get("anzahl") if isinstance(inhalt, dict) else None
            if not isinstance(anzahl, int) or isinstance(anzahl, bool):
                return None
            return ("Es gibt nichts zurückzunehmen." if anzahl == 0
                    else f"Zurückgenommen: {zurueck_was(inhalt, anzahl)}.")
        if capability == "bubble_list":
            # Der aeltere Leseweg liefert Text: "15 bubbles:\n- Titel (id=..., score=...)".
            if not isinstance(ergebnis, str):
                return None
            if ergebnis.strip().lower().startswith("no bubbles found"):
                return "Du hast noch keine Bubbles."
            kopf = re.match(r"\s*(\d+) bubbles:", ergebnis)
            titel = re.findall(r"^- (.*?) \(id=[^)\n]*\)\s*$", ergebnis, flags=re.MULTILINE)
            if not kopf or not titel:
                return None
            return f"{_mehrzahl(int(kopf.group(1)), 'Bubble', 'Bubbles')}: {_aufzaehlung(titel, zeige=5)}."
        return vorgabe or None
    except Exception:
        return None


def arg_vorlage(argumente: dict) -> str:
    """Die Argumente als Text fuer ``HopSpec.arg_template``.

    Der Plan-Ausfuehrer ersetzt ``{{...}}`` in dieser Vorlage. Geschweifte Klammern in
    Texten des Menschen werden deshalb als JSON-Zeichenfolge geschrieben; nach dem
    Einlesen steht wieder der urspruengliche Text da.
    """
    roh = json.dumps(argumente, ensure_ascii=False)
    # Nur innerhalb von Zeichenfolgen ersetzen: verschachtelte Objekte (Gruppen) bleiben JSON.
    return re.sub(r'"(?:[^"\\]|\\.)*"',
                  lambda m: m.group(0).replace("{", "\\u007b").replace("}", "\\u007d"), roh)


BUBBLE_TITEL_ZEICHEN = 200
_NEUE_BUBBLE = re.compile(
    r"^\s*(?:bitte\s+)?(?:(?P<leg>leg(?:e)?)\s+|erstell(?:e)?\s+|mach(?:e)?\s+)?(?:mir\s+|uns\s+)?"
    r"(?:eine\s+)?neue\s+bubble\s*(?::\s*|(?:mit\s+(?:dem\s+)?(?:titel|namen)|namens|called|named)\s*:?\s*)?"
    r"(?P<t>.+?)\s*$", re.IGNORECASE | re.DOTALL)


def bubble_titel(satz: Any) -> str:
    """Der Titel aus "Lege eine neue Bubble X an" / "Erstelle eine neue Bubble X" / "Neue Bubble: X",
    Wort für Wort (Klammern und Zusätze bleiben). ``""``: kein solcher Satz oder kein Titel.

    Lauf 7: "Lege eine neue Bubble X (Prüfung) an" legte eine Bubble "Prüfung" an; die alte
    Abkürzung nahm das letzte großgeschriebene Wort. Wirft nie.
    """
    try:
        if not isinstance(satz, str):
            return ""
        text = re.sub(r"\s+", " ", satz).strip().rstrip(".!")
        m = _NEUE_BUBBLE.match(text)
        if not m:
            return ""
        titel = m.group("t").strip()
        if m.group("leg"):
            # "Lege ... an": das trennbare "an" gehört nicht zum Titel.
            titel = re.sub(r"(?:^|\s+)an$", "", titel, flags=re.IGNORECASE).strip()
        if len(titel) >= 2 and titel[0] in "„\"'“" and titel[-1] in "“\"'”":
            titel = titel[1:-1].strip()
        if not titel or len(titel) > BUBBLE_TITEL_ZEICHEN:
            return ""
        return titel
    except Exception:  # noqa: BLE001
        return ""


def listen_suchwort(satz: Any) -> str:
    """Suchwort fuer "zeige meine Bubbles": nur, was der Mensch in Anfuehrungszeichen setzt.

    Sonst nahm die Abkuerzung das letzte grossgeschriebene Wort ("Ideenbereich",
    "Bubbles") als Suchwort, und die Liste blieb leer.
    """
    if not isinstance(satz, str):
        return ""
    m = re.search(r"['\"„“]([^'\"„“”]+)['\"“”]", satz)
    return m.group(1).strip() if m else ""
