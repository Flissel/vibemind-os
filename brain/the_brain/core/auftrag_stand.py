"""Stand eines Agenten-Auftrags für die App (Dritter Lauf, Punkte 3 bis 5).

Befund: Der Zusteller am PC (scripts/agent_auftraege_ausfuehrer.py) sendet nur über
Telegram. Aufträge aus der App haben ``antwortkanal.art = "api"``; ihre Zeile bekommt
``zugestellt``, die App aber nichts. Sie erkennt "fertig" bisher nur an einer geänderten
Karte. Einschätzen, Vorschlag und Abbruch ändern keine Karte -> die Standzeile hing.

Deshalb liest die App den Stand selbst: ``GET /api/agent-auftraege/{id}/stand`` (Router).
Hier steht nur die reine Abbildung Zeile -> Stand. Die Dauer gibt es erst mit dem
Abschluss (angelegt bis beendet, beim White Paper über alle Teile).

Stände: ``laeuft`` (ohne Dauer), ``fertig``, ``vorschlag``, ``uebernommen``, ``verworfen``,
``abgebrochen``, ``wartet``, ``ungeprueft``, ``fehler``. ``ok`` ist nur bei ``fehler`` und ``ungeprueft``
False; ``vorschlag``, ``verworfen``, ``abgebrochen`` und ``ungeprueft`` sind Hinweise (``hinweis: true``),
keine Fehler. ``fertig`` gibt es ohne Rückweg nur mit bestätigter Prüfung.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Callable, Mapping, Optional

KETTE_MAX = 32
_KENNUNG = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")

_STATUS_SATZ = {
    "abgelaufen": "Der Auftrag kam nicht rechtzeitig zurück; es wurde nichts geändert.",
    "abgelehnt": "Der Auftrag wurde abgelehnt; es wurde nichts geändert.",
    "fehler": "Der Auftrag ist gescheitert; es wurde nichts geändert.",
}
UEBERNOMMEN_SATZ = "Der Vorschlag ist übernommen."


def ist_kennung(wert: Any) -> bool:
    return isinstance(wert, str) and bool(_KENNUNG.match(wert))


def _zeit(wert: Any) -> Optional[float]:
    if not isinstance(wert, str) or not wert:
        return None
    try:
        return datetime.fromisoformat(wert.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _dauer(von: Any, bis: Any) -> Optional[float]:
    a, b = _zeit(von), _zeit(bis)
    return round(max(0.0, b - a), 1) if a is not None and b is not None else None


def _vorschlag_saetze(zeile: Mapping[str, Any]) -> list[str]:
    """Die festen Sätze zum Vorschlag; genannt wird nur der Satz, der wirklich schreibt.

    Ersetzt das Übernehmen den Text der Notiz, schreibt erst „Vorschlag trotzdem übernehmen“
    (das schlichte „Vorschlag übernehmen“ warnt dort nur). Lässt sich der Vorschlag gar nicht
    mit einem festen Aufruf übernehmen, fehlt der Satz zum Übernehmen.
    """
    from . import ideen_vorschlag as vs

    try:
        if not vs.uebernehmen(zeile)[0]:
            return ["Vorschlag zeigen", "Vorschlag verwerfen"]
        schreibt = vs.TROTZDEM_SATZ if vs.ueberschreibt(zeile) else "Vorschlag übernehmen"
    except Exception:  # noqa: BLE001 - der Stand wirft nie; im Zweifel nur zeigen und verwerfen
        return ["Vorschlag zeigen", "Vorschlag verwerfen"]
    return ["Vorschlag zeigen", schreibt, "Vorschlag verwerfen"]


def ziel_der_zeile(zeile: Mapping[str, Any]) -> Optional[dict[str, Any]]:
    """(33) Wohin die App springt: ``{space, bubble_id, node_id}`` aus dem Rückweg der Zeile.

    ``node_id`` ist die Dokument- oder Einschätzungskarte (``dokument_id``) bzw. die betroffene
    Karte (``karte_id``), beim Gruppieren ``None``. Ohne Bubble kein Ziel. Wirft nie.
    """
    try:
        # Kennungen wie im Rückweg selbst geprüft (core/ideen_rueckweg, core/ideen_dokument).
        from .ideen_abkuerzung import ist_kennung as ist_db_kennung

        uebergabe = zeile.get("uebergabe")
        rueckweg = uebergabe.get("rueckweg") if isinstance(uebergabe, Mapping) else None
        if not isinstance(rueckweg, Mapping) or not ist_db_kennung(rueckweg.get("bubble_id")):
            return None
        pruefung = zeile.get("pruefung") if isinstance(zeile.get("pruefung"), Mapping) else {}
        node_id = next((k for k in (pruefung.get("dokument_id"), rueckweg.get("dokument_id"),
                                    rueckweg.get("karte_id")) if ist_db_kennung(k)), None)
        return {"space": "ideas", "bubble_id": rueckweg["bubble_id"], "node_id": node_id}
    except Exception:  # noqa: BLE001
        return None


def stand_einer_zeile(zeile: Mapping[str, Any]) -> dict[str, Any]:
    """Der Stand genau dieser Zeile (ohne Kette, ohne Dauer)."""
    ergebnis = _stand_einer_zeile(zeile)
    if ergebnis.get("stand") in ("fertig", "vorschlag", "uebernommen"):
        ziel = ziel_der_zeile(zeile)
        if ziel:
            ergebnis["ziel"] = ziel
    return ergebnis


def _stand_einer_zeile(zeile: Mapping[str, Any]) -> dict[str, Any]:
    status = zeile.get("status")
    pruefung = zeile.get("pruefung") if isinstance(zeile.get("pruefung"), Mapping) else None
    if status in ("offen", "laeuft") and not (pruefung and pruefung.get("rueckweg") == "abgebrochen"):
        return {"stand": "laeuft"}
    if pruefung is None or pruefung.get("fortsetzung") == "laeuft":
        # Fertig, aber noch nicht geprüft oder geschrieben: läuft aus Sicht des Menschen weiter.
        return {"stand": "laeuft"} if status == "fertig" else {
            "stand": "fehler", "ok": False, "meldung": _STATUS_SATZ.get(str(status), _STATUS_SATZ["fehler"])}
    rueckweg = pruefung.get("rueckweg")
    meldung = pruefung.get("meldung") if isinstance(pruefung.get("meldung"), str) else ""
    if rueckweg == "weiter":
        return {"stand": "laeuft", "meldung": meldung, "weiter": pruefung.get("naechster_auftrag")}
    if rueckweg == "geschrieben":
        return {"stand": "fertig", "ok": True, "meldung": meldung}
    if rueckweg == "abgenommen" and status == "fertig" and pruefung.get("verified") is True:
        # Bau im Project Space (core/coding_auftrag): die unabhängige Abnahme des Dienstes ist grün.
        return {"stand": "fertig", "ok": True, "meldung": meldung or "Der Bau ist fertig und abgenommen."}
    if rueckweg == "vorschlag" and pruefung.get("vorschlag_erledigt") == "verworfen":
        # Nachmessung 23.11.2026: nach "Vorschlag verwerfen" stand hier weiter "vorschlag".
        from .ideen_vorschlag import verworfen_satz
        return {"stand": "verworfen", "ok": True, "hinweis": True, "meldung": verworfen_satz(zeile)}
    if rueckweg == "vorschlag" and pruefung.get("vorschlag_erledigt") == "uebernommen":
        # Lauf 7: nach dem Übernehmen blieb der Stand auf "vorschlag".
        return {"stand": "uebernommen", "ok": True, "meldung": UEBERNOMMEN_SATZ}
    if rueckweg == "vorschlag":
        return {"stand": "vorschlag", "ok": True, "hinweis": True, "meldung": meldung,
                "saetze": _vorschlag_saetze(zeile)}
    if rueckweg == "abgebrochen":
        return {"stand": "abgebrochen", "ok": True, "hinweis": True,
                "meldung": meldung or "Das White Paper wurde abgebrochen."}
    if rueckweg == "ungeprueft":
        # Bau im Project Space (core/coding_auftrag): fertig laut Dienst, ohne unabhängige Prüfung.
        return {"stand": "ungeprueft", "ok": False, "hinweis": True, "meldung": meldung or UNGEPRUEFT_SATZ}
    if rueckweg == "wartet":
        return {"stand": "wartet", "ok": True, "hinweis": True, "meldung": meldung}
    if rueckweg in ("abgelehnt", "fehler"):
        return {"stand": "fehler", "ok": False, "meldung": meldung or _STATUS_SATZ["fehler"]}
    # Auftrag ohne Rückweg (andere Spaces): Status und Prüfung.
    # Lauf 7 (falsch grün): bubble_evaluate endete mit "Werkzeug ... nicht verfügbar", Prüfung
    # UNVERIFIED, und hier stand "fertig". "fertig" gilt nur, wenn die Prüfung das Ergebnis bestätigt.
    if _werkzeug_fehlt(zeile.get("ergebnis")):
        return {"stand": "fehler", "ok": False, "meldung": WERKZEUG_FEHLT_SATZ}
    if (status == "fertig" and pruefung.get("verified") is True
            and pruefung.get("fortsetzung") != "fehler"):
        return {"stand": "fertig", "ok": True, "meldung": meldung or "Der Auftrag ist fertig."}
    if status == "fertig" and pruefung.get("verified") is None and pruefung.get("fortsetzung") != "fehler":
        # Ohne Prüfer (etwa Coding) ist ein Auftrag oft gelungen: weder grünes ✓ noch rotes ✕, sondern ein Hinweis.
        return {"stand": "ungeprueft", "ok": False, "hinweis": True, "meldung": UNGEPRUEFT_SATZ}
    return {"stand": "fehler", "ok": False,
            "meldung": meldung or _STATUS_SATZ.get(str(status), _STATUS_SATZ["fehler"])}


WERKZEUG_FEHLT_SATZ = "Das hat nicht geklappt: dem Agenten fehlt das Werkzeug. Geändert habe ich nichts."
UNGEPRUEFT_SATZ = ("Der Auftrag kam zurück, aber ich konnte das Ergebnis nicht prüfen. "
                   "Ob sich etwas geändert hat, weiß ich nicht.")
_WERKZEUG_FEHLT = re.compile(
    r"(?:werkzeug|tool)\b[^\n]{0,80}?\b(?:nicht\s+(?:verf(?:ü|ue)gbar|vorhanden|gefunden|freigeschaltet)|"
    r"fehlt|unbekannt|not\s+(?:available|found|allowed|permitted)|unavailable|unknown)"
    r"|(?:unknown|unbekanntes)\s+(?:tool|werkzeug)|no\s+such\s+tool", re.IGNORECASE)


def _werkzeug_fehlt(ergebnis: Any) -> bool:
    """Meldet das Ergebnis des Agenten ein fehlendes Werkzeug? (Text oder {"text"/"result"/"error": ...})"""
    if isinstance(ergebnis, Mapping):
        teile = [ergebnis.get(k) for k in ("text", "result", "error", "meldung", "antwort")]
        return any(_werkzeug_fehlt(t) for t in teile if t is not None)
    if not isinstance(ergebnis, str) or not ergebnis.strip():
        return False
    return bool(_WERKZEUG_FEHLT.search(ergebnis[:4000]))


def _white_paper_teil(zeile: Mapping[str, Any]) -> str:
    """"White Paper: Abschnitt 2 von 3 läuft." aus dem Rückweg der Zeile, sonst leer. Wirft nie."""
    try:
        uebergabe = zeile.get("uebergabe")
        rueckweg = uebergabe.get("rueckweg") if isinstance(uebergabe, Mapping) else None
        if not isinstance(rueckweg, Mapping) or rueckweg.get("vertrag") != "abschnitt":
            return ""
        teile, index = rueckweg.get("teile"), rueckweg.get("index")
        if not isinstance(teile, list) or not isinstance(index, int) or isinstance(index, bool) \
                or not 0 <= index < len(teile):
            return ""
        return f"White Paper: Abschnitt {index + 1} von {len(teile)} läuft."
    except Exception:  # noqa: BLE001
        return ""


def stand(auftrag_id: str, lesen:Callable[[str], Optional[Mapping[str, Any]]]) -> dict[str, Any]:
    """Stand eines Auftrags, beim White Paper über die Kette der Teile. Wirft nie.

    ``lesen(id)`` liefert die Zeile oder ``None``; eine Ausnahme heißt: nicht lesbar.
    """
    if not ist_kennung(auftrag_id):
        return {"stand": "unbekannt", "ok": False, "meldung": "Diesen Auftrag kenne ich nicht."}
    erste = None
    aktuell = auftrag_id
    gesehen: set[str] = set()
    try:
        for _ in range(KETTE_MAX):
            zeile = lesen(aktuell)
            if zeile is None:
                return {"stand": "unbekannt", "ok": False, "meldung": "Diesen Auftrag kenne ich nicht."}
            erste = erste or zeile
            gesehen.add(aktuell)
            ergebnis = stand_einer_zeile(zeile)
            weiter = ergebnis.pop("weiter", None)
            if ergebnis["stand"] == "laeuft" and ist_kennung(weiter) and weiter not in gesehen:
                aktuell = weiter
                continue
            ergebnis["auftrag_id"] = auftrag_id
            if ergebnis["stand"] == "laeuft":
                # Lauf 28: die Standzeile zeigte bis zum Ende die Nummer des ersten Auftrags.
                # Jetzt: der laufende Auftrag der Kette und beim White Paper "Abschnitt n von m".
                if aktuell != auftrag_id:
                    ergebnis["laufender_auftrag"] = aktuell
                teil = _white_paper_teil(zeile)
                if teil:
                    ergebnis["meldung"] = teil
            if ergebnis["stand"] not in ("laeuft",):
                dauer = _dauer(erste.get("angelegt"), zeile.get("beendet"))
                if dauer is not None:
                    ergebnis["dauer_s"] = dauer
            return ergebnis
    except Exception:  # noqa: BLE001
        return {"stand": "unbekannt", "ok": False, "auftrag_id": auftrag_id,
                "meldung": "Der Stand lässt sich gerade nicht lesen."}
    return {"stand": "laeuft", "auftrag_id": auftrag_id}
