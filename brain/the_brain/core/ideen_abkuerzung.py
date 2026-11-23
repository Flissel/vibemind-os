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
    genannt = re.sub(rf"^{_KARTE}", "", name.strip(), flags=_I).strip().strip("\"'„“”")
    treffer = [k for k in karten if _normal(k.get("title")) == wort]
    if len(treffer) == 1:
        return treffer[0]
    if len(treffer) > 1:
        raise _Rueckfrage(f"Es gibt {len(treffer)} Notizen mit dem Titel „{genannt}“. Markiere die gemeinte.")
    roh = kontext.get("cards")
    if isinstance(roh, list) and any(
            isinstance(k, dict) and not ist_kennung(k.get("id")) and _normal(k.get("title")) == wort for k in roh):
        # Die Karte steht am Canvas, aber die App hat keine Kennung der Datenbank mitgegeben.
        raise _Rueckfrage(f"Die Notiz „{genannt}“ kann ich gerade nicht zuordnen. Öffne die Bubble noch einmal.")
    gesamt = kontext.get("cards_total")
    if isinstance(gesamt, int) and not isinstance(gesamt, bool) and gesamt > len(karten):
        raise _Rueckfrage(f"Unter den sichtbaren Notizen finde ich „{genannt}“ nicht. Markiere die Notiz.")
    raise _Rueckfrage(f"Eine Notiz „{genannt}“ finde ich in dieser Bubble nicht.")


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


_FELDNAMEN = {
    "bubble_id": "die Bubble", "id": "die Karte", "from_id": "die erste Karte", "to_id": "die zweite Karte",
    "title": "der Titel", "content": "der Inhalt", "text": "der Text", "score": "die Zahl",
    "format": "das Format", "idea_ids": "die verlinkten Karten", "order": "die Reihenfolge",
    "columns": "die Spaltenzahl", "limit": "die Anzahl", "aenderung_id": "die Kennung der Änderung",
}
_WERKZEUGFEHLER = {
    "idea_not_found": "Diese Karte gibt es nicht mehr.",
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


def fehlersatz(fehler: Any) -> str:
    """Ein deutscher Satz fuer den Chat aus dem Fehlertext eines festen Schritts.

    Der technische Text bleibt im Ergebnis des Schritts stehen; hier entsteht nur, was
    der Mensch liest. Es kommen Feldnamen und Fehlernamen durch, nie Werte.
    """
    text = str(fehler or "")
    if "Ideen-Dienst nicht erreichbar" in text:
        return ("Der Ideen-Dienst läuft gerade nicht. Bitte prüfe das Canvas, bevor du es wiederholst."
                if "transport_closed" in text
                else "Der Ideen-Dienst hat nicht geantwortet. Es ist unklar, ob etwas geändert wurde.")
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


def _aufzaehlung(titel: list[str], zeige: int = 3) -> str:
    """„A“, „B“, „C“ (und 9 weitere) - nur Titel, nie Kennungen."""
    sauber = [re.sub(r"\s+", " ", str(t or "")).strip() for t in titel]
    sauber = [(t if len(t) <= 60 else t[:57].rstrip() + "…") or "ohne Titel" for t in sauber]
    kopf = ", ".join(f"„{t}“" for t in sauber[:zeige])
    rest = len(sauber) - zeige
    return kopf + (f" (und {rest} weitere)" if rest > 0 else "")


def _mehrzahl(anzahl: int, eins: str, viele: str) -> str:
    return f"{anzahl} {eins if anzahl == 1 else viele}"


def antwort_fuer_schritt(capability: Any, ok: bool, ergebnis: Any, fehler: Any,
                         vorgabe: str = "", kontext: Any = None) -> Optional[str]:
    """Der deutsche Satz fuer den Chat nach einem festen Schritt.

    ``vorgabe`` ist der Satz der Abkuerzung ("Notiz ... festgehalten."). Wo das Ergebnis
    mehr sagt (Liste, Anzahl), entsteht der Satz aus dem Ergebnis. ``None`` heisst: kein
    fester Satz moeglich, der Aufrufer nimmt seinen bisherigen Weg. Wirft nie.
    """
    try:
        if not ok:
            return fehlersatz(fehler)
        if capability == "idea.list":
            zeilen = _mcp_json(ergebnis)
            if not isinstance(zeilen, list):
                return vorgabe or None
            bubble = kontext.get("bubble") if isinstance(kontext, dict) else None
            ort = (str(bubble.get("title") or "").strip() if isinstance(bubble, dict) else "")
            ort = f"„{ort}“" if ort else "dieser Bubble"
            if not zeilen:
                return f"In {ort} gibt es noch keine Notizen."
            titel = [z.get("title") for z in zeilen if isinstance(z, dict)]
            return f"{_mehrzahl(len(zeilen), 'Notiz', 'Notizen')} in {ort}: {_aufzaehlung(titel)}."
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
                    else f"Zurückgenommen: {_mehrzahl(anzahl, 'Änderung', 'Änderungen')}.")
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
