"""Rowboat-Lauf am PC (Spec sales-claw 2026-10-09-marke-exakt-logo-wissen §3): nach jeder Uebernahme bringt
Claude die Dokumente mit Firmenbezug auf den Stand des neuen Profils und schreibt das Markenhandbuch neu.
Laeuft in einem EIGENEN Faden des Chat-Arbeiters (Ruling R7, chat_worker.wissen_starten -> ein_durchlauf hier,
naechster mit arten=wissen): ein Lauf dauert Minuten und haelt so nie Chat, Bearbeitung oder Uebernahme auf.
Geschrieben wird nur, was claw/wissen_lauf prueft und sichert; ein Abbruch endet 'fertig' mit dem Hinweis
"teilweise: …"."""
from __future__ import annotations

import math
import os

from spaces.marketing.claw import denkspur, markenprofil, markenwissen, wissen_lauf, wissen_prompt
from spaces.marketing.workers import chat_worker as cw
from spaces.marketing.workers import marken_arbeiter as ma


ARTEN = ("wissen",)


def ein_durchlauf(api, **kw) -> str:
    """Ein Schritt des Wissens-Fadens: holt nur Wissens-Laeufe (die VM filtert) und bearbeitet sie ueber
    denselben Ablauf wie der Marken-Faden (marken_arbeiter.ein_durchlauf)."""
    return ma.ein_durchlauf(api, arten=ARTEN, **kw)


def _anzahl(n: int, einzahl: str, mehrzahl: str) -> str:
    return f"{n} {einzahl if n == 1 else mehrzahl}"


def _sauber(wert):
    """Nicht kodierbare Zeichen (einzelne Surrogate in Dateinamen) ersetzen, damit Meldungen sendbar bleiben."""
    if isinstance(wert, str):
        return wert.encode("utf-8", "replace").decode("utf-8")
    if isinstance(wert, list):
        return [_sauber(x) for x in wert]
    return wert


def _seit(kontext: dict) -> float | None:
    """kontext.seit ist eine Epoche in Sekunden (Migration 066); als Zahl lesen, sonst None."""
    roh = kontext.get("seit")
    if isinstance(roh, bool):
        return None
    try:
        zahl = float(roh)
    except (TypeError, ValueError):
        return None
    return zahl if math.isfinite(zahl) else None


def _neu_text(ordner: str) -> str:
    pfad = markenprofil._marke_pfad(ordner)
    return (markenprofil._datei_text(pfad) or "") if pfad else ""


def _fremde(api, mandant: str, name: str) -> list[str]:
    """Namen und ids der anderen Firmen; der eigene Name (auch bei fremder id) gehoert nie dazu."""
    eigene = {str(mandant).strip().casefold(), str(name).strip().casefold()} - {""}
    namen: list[str] = []
    for f in api.firmen():
        if isinstance(f, dict) and f.get("id") and str(f["id"]) != mandant:
            namen += [str(f["id"]), str(f.get("name") or "")]
    return [n for n in dict.fromkeys(namen) if n.strip() and n.strip().casefold() not in eigene]


def wissen_bearbeiten(api, auftrag: dict, fragen_strom, jetzt, uhr, schlafen, halten_takt_s) -> str:
    aid = str(auftrag["id"])
    spur = denkspur.Spur(cw.spur_senden(api, aid), uhr=uhr)
    try:
        return _lauf(api, auftrag, aid, spur, fragen_strom, jetzt, uhr, schlafen, halten_takt_s)
    except (ma._Aufgeben, cw._Verloren, ma.ApiFehler, OSError, ValueError):
        ma._spur_ende(spur)
        raise


def _schritt(spur, text: str) -> None:
    spur.schritt(_sauber(text))


def _lauf(api, auftrag: dict, aid: str, spur, fragen_strom, jetzt, uhr, schlafen, halten_takt_s) -> str:
    mandant, name = ma._firma(auftrag)
    kontext = auftrag.get("kontext") if isinstance(auftrag.get("kontext"), dict) else {}
    seit = _seit(kontext)
    hinweise: list[str] = []
    wurzel = wissen_lauf.wissen_wurzel()
    with cw.halten(api, aid, halten_takt_s) as halter:
        ordner = markenwissen.ordner_finden(markenwissen.wurzel(), mandant, name)
        if ordner is None:
            raise ma._Aufgeben(f"Wissen-Lauf nicht möglich: companys/{name} fehlt")
        try:                    # Wissenswurzel und Firmenordner muessen dieselbe Wurzel haben
            unter_wurzel = os.path.commonpath([os.path.realpath(ordner), os.path.realpath(wurzel)]) == \
                os.path.realpath(wurzel)
        except ValueError:
            unter_wurzel = False
        if not unter_wurzel:
            raise ma._Aufgeben("Wissen-Lauf nicht möglich: Firmenordner liegt nicht in der Wissensbasis")
        neu_text = _neu_text(ordner)
        if not neu_text.strip():
            raise ma._Aufgeben("Wissen-Lauf nicht möglich: Marke.md fehlt")
        alt_text = wissen_lauf.alt_profil(ordner, seit)
        kandidaten = wissen_lauf.kandidaten(wurzel, ordner, [name, mandant], _fremde(api, mandant, name), hinweise)
    if halter.verloren.is_set():
        return "fehler"
    _schritt(spur, "Kandidaten: " + _anzahl(len(kandidaten), "Dokument", "Dokumente"))
    text_nutzer = _sauber(wissen_prompt.nutzer_text(name, alt_text, neu_text, kandidaten))
    nachrichten = [{"role": "user", "content": text_nutzer}]
    zustand = {"mit_bildern": False, "text_nutzer": text_nutzer, "hinweise": hinweise}
    for versuch in (1, 2):
        text = ma._text_holen(api, aid, fragen_strom, nachrichten, zustand, uhr, schlafen, halten_takt_s, spur,
                              system=wissen_prompt.SYSTEM)
        try:
            erg = wissen_prompt.antwort_lesen(text)
            break
        except wissen_prompt.AntwortFehler as e:
            if versuch == 2:
                raise ma._Aufgeben(cw.NICHT_UMGESETZT + str(e)) from None
            _schritt(spur, f"Antwort geprüft: {e}")
            spur.korrektur()
            nachrichten += [{"role": "assistant", "content": text},
                            {"role": "user", "content": wissen_prompt.korrektur_text(str(e))}]
    if not api.weiter(aid):
        return "fehler"
    with cw.halten(api, aid, halten_takt_s):
        ergebnis = wissen_lauf.anwenden(wurzel, ordner, {k.rel: k for k in kandidaten}, erg["ersetzungen"],
                                        erg["markenhandbuch"], jetzt())
    for rel in ergebnis.geschrieben:
        _schritt(spur, f"Geschrieben: {rel}")
    if ergebnis.handbuch_rel:
        _schritt(spur, "Markenhandbuch geschrieben")
    hinweise += ergebnis.verworfen
    zeilen = ["Wissen aktualisiert: " + _anzahl(len(ergebnis.geschrieben), "Datei", "Dateien")]
    zeilen += [f"- {rel}" for rel in ergebnis.geschrieben]
    if ergebnis.handbuch_rel:
        zeilen.append(f"- Markenhandbuch: {ergebnis.handbuch_rel}")
    if ergebnis.abbruch:
        _schritt(spur, f"Abgebrochen: {ergebnis.abbruch}")
        hinweise.insert(0, f"teilweise: abgebrochen bei {ergebnis.abbruch}; geschrieben: "
                           f"{', '.join(ergebnis.geschrieben) or 'nichts'} – jede Datei ist gesichert")
    spur.ende()
    api.fertig(aid, {"antwort": _sauber("\n".join(zeilen))[:4000], "hinweise": ma._hinweise(_sauber(hinweise))})
    return "fertig"
