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
_KARTE = r"(?:(?:die|der|das)\s+)?(?:notiz|karte|idee)\s+"


def ist_kennung(wert: Any) -> bool:
    return isinstance(wert, str) and _KENNUNG.fullmatch(wert) is not None


def _normal(text: Any) -> str:
    text = str(text or "").strip().strip("\"'„“”‚‘’«».!?:;, ")
    return re.sub(r"\s+", " ", text).casefold()


def _format_aus(wort: str) -> Optional[str]:
    for muster, name in _FORMATWORTE:
        if re.fullmatch(muster, wort.strip(), _I):
            return name
    return None


class _Rueckfrage(Exception):
    pass


def _karten(kontext: dict) -> list[dict]:
    karten = kontext.get("cards")
    return [k for k in karten if isinstance(k, dict) and ist_kennung(k.get("id"))] if isinstance(karten, list) else []


def _offene_bubble(kontext: dict) -> str:
    bubble = kontext.get("bubble")
    if isinstance(bubble, dict) and ist_kennung(bubble.get("id")):
        return bubble["id"]
    raise _Rueckfrage("Dafür muss eine Bubble offen sein. Öffne eine Bubble und sag es noch einmal.")


def _karte(name: str, kontext: dict) -> dict:
    """Genau eine Karte zu einem Namen, einem Fuerwort oder der Auswahl; sonst Rueckfrage."""
    karten = _karten(kontext)
    nach_id = {k["id"]: k for k in karten}
    wort = _normal(re.sub(rf"^{_KARTE}", "", name.strip(), flags=_I))
    if wort in ("das", "die", "sie", "es", "diese", "dies", "die letzte", "die letzte notiz", "die letzte karte",
                "die notiz", "die karte", "diese notiz", "diese karte", "die markierte", "die auswahl"):
        auswahl = [i for i in kontext.get("selection") or [] if ist_kennung(i)]
        if len(auswahl) == 1:
            return nach_id.get(auswahl[0], {"id": auswahl[0], "title": ""})
        if len(auswahl) > 1:
            raise _Rueckfrage(f"Es sind {len(auswahl)} Karten markiert. Welche meinst du?")
        zuletzt = kontext.get("last_id")
        if ist_kennung(zuletzt):
            return nach_id.get(zuletzt, {"id": zuletzt, "title": ""})
        raise _Rueckfrage("Welche Karte meinst du? Nenn ihren Titel oder markiere sie.")
    treffer = [k for k in karten if _normal(k.get("title")) == wort]
    if len(treffer) == 1:
        return treffer[0]
    if len(treffer) > 1:
        raise _Rueckfrage(f"Es gibt {len(treffer)} Karten mit dem Titel „{name.strip()}“. Markiere die gemeinte.")
    gesamt = kontext.get("cards_total")
    if isinstance(gesamt, int) and not isinstance(gesamt, bool) and gesamt > len(karten):
        raise _Rueckfrage(f"Unter den sichtbaren Karten finde ich „{name.strip()}“ nicht. Markiere die Karte.")
    raise _Rueckfrage(f"Ich finde keine Karte mit dem Titel „{name.strip()}“ in dieser Bubble.")


def _bubble(name: Optional[str], kontext: dict) -> dict:
    """Die offene Bubble ("diese Bubble") oder genau eine Bubble mit diesem Titel."""
    offen = kontext.get("bubble") if isinstance(kontext.get("bubble"), dict) else None
    if not name or _normal(name) in ("diese", "dieser", "die", "der", "hier"):
        if offen and ist_kennung(offen.get("id")):
            return offen
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
    raise _Rueckfrage(f"Ich finde keine Bubble mit dem Titel „{name.strip()}“.")


def _zahl(text: str) -> int:
    wert = int(text)
    if not 0 <= wert <= 100:
        raise _Rueckfrage("Die Bewertung geht von 0 bis 100.")
    return wert


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
    try:
        return _loese(text, kontext)
    except _Rueckfrage as frage:
        return {"rueckfrage": str(frage)}
    except Exception:
        return None


def _loese(text: str, kontext: dict) -> Optional[dict]:
    # ── Begruendung festhalten (vor "festhalten", weil der Auftakt aehnlich ist) ──
    m = (re.fullmatch(r"begr(?:ü|ue)ndung\s*:\s*(?P<t>.+)", text, _I)
         or re.fullmatch(r"halt(?:e)?\s+als\s+begr(?:ü|ue)ndung\s+fest\s*[,:]?\s*(?:dass\s+)?(?P<t>.+)", text, _I))
    if m:
        argumente: dict[str, Any] = {"bubble_id": _offene_bubble(kontext), "text": m.group("t").strip()}
        auswahl = [i for i in kontext.get("selection") or [] if ist_kennung(i)]
        if auswahl:
            argumente["idea_ids"] = list(dict.fromkeys(auswahl))[:20]
        return _treffer("idea.rate_reason", argumente, "Begründung festgehalten.")

    # ── festhalten ──
    m = re.fullmatch(
        r"(?:halt(?:e)?\s+fest|notiz|notiere|schreib(?:e)?\s+auf|merk(?:e)?\s+dir)\s*[:,]?\s*(?:dass\s+)?(?P<t>.+)",
        text, _I)
    if m:
        titel = m.group("t").strip()
        argumente = {"bubble_id": _offene_bubble(kontext), "title": titel}
        if ist_kennung(kontext.get("new_id")):
            argumente["id"] = kontext["new_id"]
        return _treffer("idea.create", argumente, f"Notiz „{titel}“ festgehalten.")

    # ── umbenennen ──
    m = re.fullmatch(rf"benenne\s+(?P<k>.+?)\s+in\s+(?P<neu>.+?)\s+um", text, _I)
    if m:
        karte = _karte(m.group("k"), kontext)
        neu = m.group("neu").strip().strip("\"'„“")
        return _treffer("idea.update", {"id": karte["id"], "title": neu},
                        f"{_titel(karte)} heißt jetzt „{neu}“.")

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
        return _treffer("idea.arrange", {"bubble_id": _offene_bubble(kontext), "order": order},
                        "Karten nach " + ("Bewertung" if order == "score" else "Titel") + " geordnet.")
    if (re.fullmatch(r"r(?:ä|ae)um(?:e)?\s+(?:das\s+canvas|die\s+karten|die\s+notizen|hier)\s+auf", text, _I)
            or re.fullmatch(r"ordne\s+(?:die\s+)?(?:notizen|karten)(?:\s+im\s+raster)?", text, _I)):
        return _treffer("idea.arrange", {"bubble_id": _offene_bubble(kontext)}, "Karten ins Raster geordnet.")

    # ── verlinken ──
    if re.fullmatch(r"(?:verlinke|verbinde|verkn(?:ü|ue)pfe)\s*,?\s+was\s+zusammen\s?geh(?:ö|oe)rt", text, _I) \
            or re.fullmatch(r"finde\s+verbindungen(?:\s+in\s+dieser\s+bubble)?", text, _I):
        return _treffer("idea.auto_link", {"bubble_id": _offene_bubble(kontext)},
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
    m = re.fullmatch(r"bewerte\s+(?:diese\s+bubble|die\s+bubble(?:\s+(?P<b>.+?))?)\s+mit\s+(?P<z>\d{1,3})(?:\s+punkten)?",
                     text, _I)
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
    return None


def arg_vorlage(argumente: dict) -> str:
    """Die Argumente als Text fuer ``HopSpec.arg_template``.

    Der Plan-Ausfuehrer ersetzt ``{{...}}`` in dieser Vorlage. Geschweifte Klammern in
    Texten des Menschen werden deshalb als JSON-Zeichenfolge geschrieben; nach dem
    Einlesen steht wieder der urspruengliche Text da.
    """
    roh = json.dumps(argumente, ensure_ascii=False)
    return "{" + roh[1:-1].replace("{", "\\u007b").replace("}", "\\u007d") + "}"


def listen_suchwort(satz: Any) -> str:
    """Suchwort fuer "zeige meine Bubbles": nur, was der Mensch in Anfuehrungszeichen setzt.

    Sonst nahm die Abkuerzung das letzte grossgeschriebene Wort ("Ideenbereich",
    "Bubbles") als Suchwort, und die Liste blieb leer.
    """
    if not isinstance(satz, str):
        return ""
    m = re.search(r"['\"„“]([^'\"„“”]+)['\"“”]", satz)
    return m.group(1).strip() if m else ""
