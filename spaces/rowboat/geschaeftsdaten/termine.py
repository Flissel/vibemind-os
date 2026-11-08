"""Termin-Notizen und Termin-Uebersicht je Firma (rein, ohne DB).

Aus den Ereignissen `termin`/`termin_abgesagt` entstehen aktive `Termin`e (je uid gewinnt der spaetere
Eintrag). `termine_soll` baut daraus je Termin eine Notiz unter `Termine/` und eine Uebersicht
`Termine/_overview.md`. Fremdtext (Name, Thema, Ort) wird einzeilig und gegen Wiki-Links und
`@rowboat`-Ausloeser neutralisiert.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Iterable

from ablage import Soll
from namen import MAIL_MUSTER, dateistamm, eindeutig, relpfad
from quelle import Ereignis
from steckbrief import ERZEUGT_VON, _AUSLOESER, _links, _uebersicht_kopf

TERMIN_PRAEFIX = "termin:"
UEBERSICHT_TERMINE = "termine:uebersicht"
ORDNER = "Termine"
UEBERSICHT_STAMM = "_overview"
VORSCHAU_TAGE = 14  # Uebersicht: heute bis heute + 14 (einschliesslich)
MAX_THEMA = 200
MAX_NAME = 100
_UHRZEIT = re.compile(r"^\d{1,2}:\d{2}$")
_ZAHL = re.compile(r"^[0-9]+$")
_LEERRAUM = re.compile(r"\s+")
_STEUER = re.compile(r"[\x00-\x1f\x7f]")
_URL = re.compile(r"^https://[^\s<>()\[\]\x00-\x1f\x7f]+$")
_ID = re.compile(r"^[A-Za-z0-9_.@-]{1,200}$")
log = logging.getLogger("geschaeftsdaten")
_FEST = datetime(2000, 1, 1, tzinfo=timezone.utc)  # gleiches created_at: Gleichstand entscheidet die uid


@dataclass(frozen=True)
class Termin:
    lead_id: str
    uid: str
    datum: date
    uhrzeit: str | None
    dauer_minuten: int | None
    thema: str | None
    ort: str | None


def termin_schluessel(lead_id: str, uid: str) -> str:
    return f"{TERMIN_PRAEFIX}{lead_id}:{uid}"


def _sortierung(t: Termin) -> tuple[date, str]:
    return t.datum, (t.uhrzeit or "").zfill(5)


def _dauer(wert: str | None) -> int | None:
    text = (wert or "").strip()
    if not _ZAHL.fullmatch(text):
        return None
    n = int(text)
    return n if n > 0 else None


def _id_ok(wert: str) -> bool:
    """Lead-ID bzw. uid landen im Front matter und im Dateinamen: nur unverdaechtige Zeichen, nie `@rowboat`."""
    return bool(_ID.fullmatch(wert)) and "@rowboat" not in wert.casefold()


def termine_aus(ereignisse: list[Ereignis]) -> tuple[list[Termin], set[str]]:
    """(aktive Termine nach Datum und Uhrzeit, abgesagte uids). Je uid gewinnt der spaetere Eintrag; ohne
    gueltiges `termin_datum` wird ein Termin uebersprungen. Eintraege ohne uid bleiben erhalten (uid `""`),
    bekommen aber keine Notiz, weil sie keinen Schluessel haetten."""
    geordnet = sorted(ereignisse, key=lambda e: e.zeit)
    verworfen = sum(1 for e in geordnet if e.typ in ("termin", "termin_abgesagt")
                    and not (_id_ok(e.lead_id) and (not e.termin_uid or _id_ok(e.termin_uid))))
    if verworfen:
        log.warning("%d Termin-Ereignisse mit ungueltiger Lead-ID oder uid verworfen", verworfen)
    geordnet = [e for e in geordnet if e.typ not in ("termin", "termin_abgesagt")
                or (_id_ok(e.lead_id) and (not e.termin_uid or _id_ok(e.termin_uid)))]
    abgesagt = {e.termin_uid for e in geordnet if e.typ == "termin_abgesagt" and e.termin_uid}
    je_uid: dict[str, Ereignis] = {}
    ohne_uid: list[Ereignis] = []
    for e in geordnet:
        if e.typ != "termin":
            continue
        if e.termin_uid:
            je_uid[e.termin_uid] = e
        else:
            ohne_uid.append(e)
    termine: list[Termin] = []
    for e in [*je_uid.values(), *ohne_uid]:
        if e.termin_uid in abgesagt:
            continue
        try:
            tag = date.fromisoformat(e.termin_datum or "")
        except ValueError:
            continue
        uhr = (e.termin_uhrzeit or "").strip()
        termine.append(Termin(e.lead_id, e.termin_uid or "", tag, uhr if _UHRZEIT.fullmatch(uhr) else None,
                              _dauer(e.termin_dauer), e.termin_thema, e.termin_ort))
    termine.sort(key=_sortierung)
    return termine, abgesagt


def _einzeilig(wert: str | None, max_len: int | None = None) -> str | None:
    """Fremdtext einzeilig: Steuerzeichen weg, `[[`/`]]` -> `[`/`]`, `@rowboat` entschaerft."""
    if wert is None:
        return None
    text = _LEERRAUM.sub(" ", _STEUER.sub(" ", wert)).strip()
    text = re.sub(r"\[+", "[", text)  # jede Folge von Klammern wird eine: nie `[[` oder `]]`
    text = re.sub(r"\]+", "]", text)
    text = _AUSLOESER.sub(r"@ \1", text)
    if max_len is not None:
        text = text[:max_len].rstrip()
    return text or None


def _name(wert: str | None, lead_id: str) -> str:
    text = _einzeilig(MAIL_MUSTER.sub("[E-Mail entfernt]", wert or ""), MAX_NAME)
    return text or f"Kontakt {lead_id[:8]}"


def _ort_zeile(ort: str | None) -> str | None:
    if ort is None:
        return None
    roh = ort.strip()
    if _URL.fullmatch(roh) and not _AUSLOESER.search(roh):
        return f"[Jitsi-Raum]({roh})"
    return _einzeilig(ort, MAX_THEMA)


def _hhmm(uhr: str | None) -> str:
    if not uhr:
        return ""
    h, m = uhr.split(":")
    return f" {int(h):02d}{m}"


def _notiz(t: Termin, name: str, kontakt_link: str, eltern: str) -> str:
    zeilen = [
        "---", f"erzeugt_von: {ERZEUGT_VON}", "quelle: sales-claw",
        f"schluessel: {termin_schluessel(t.lead_id, t.uid)}", "art: termin",
        f"parent: {_links([eltern])}", f"kontakt: {_links([kontakt_link])}",
        f"kontakt_id: {t.lead_id}", f"termin_uid: {t.uid}", f"datum: {t.datum.isoformat()}", "---", "",
        "> Automatisch erzeugt aus sales-claw. Bitte nicht bearbeiten – der nächtliche Lauf überschreibt; "
        "eigene Anmerkungen in eine eigene Notiz.",
        "",
        f"# Termin mit {name}",
        "",
        f"- **Datum:** {t.datum:%d.%m.%Y}",
    ]
    felder: list[tuple[str, str | None]] = [
        ("Uhrzeit", t.uhrzeit),
        ("Dauer", f"{t.dauer_minuten} Minuten" if t.dauer_minuten else None),
        ("Thema", _einzeilig(t.thema, MAX_THEMA)),
        ("Ort", _ort_zeile(t.ort)),
        ("Kontakt", f"[[{kontakt_link}]]"),
    ]
    zeilen += [f"- **{titel}:** {wert}" for titel, wert in felder if wert]
    return "\n".join(zeilen) + "\n"


def _uebersicht(firma: str, eintraege: list[tuple[Termin, str]], heute: date, eltern: str) -> str:
    ende = heute + timedelta(days=VORSCHAU_TAGE)
    kommend = [rel for t, rel in eintraege if heute <= t.datum <= ende]
    vergangen = sum(1 for t, _ in eintraege if t.datum < heute)
    firma_text = _einzeilig(firma) or firma
    zeilen = _uebersicht_kopf(UEBERSICHT_TERMINE, eltern) + [
        f"# Termine ({firma_text})",
        "",
        f"Kommende Termine der nächsten {VORSCHAU_TAGE} Tage (Stand {heute:%d.%m.%Y}):",
        "",
    ]
    zeilen += [f"- [[{rel[:-3]}]]" for rel in kommend] or ["Keine."]
    zeilen += ["", f"Vergangene Termine: {vergangen}", "",
               "Die Kalenderansicht liegt in sales-claw unter `/kalender`."]
    return "\n".join(zeilen) + "\n"


def termine_soll(firma: str, termine: list[Termin], pfade: dict[str, str], namen: dict[str, str], *,
                 heute: date, archiv_vorhanden: bool) -> list[Soll]:
    """Termin-Notizen (nach `rel` sortiert), danach die Uebersicht. Termine ohne Steckbrief-Pfad oder ohne
    uid werden uebersprungen. Die Uebersicht entsteht nur, wenn mindestens ein Termin platziert ist oder
    `archiv_vorhanden` gilt."""
    praefix = f"companys/{firma}"
    platziert = [t for t in termine if t.uid and t.lead_id in pfade]
    eintraege = []
    for t in platziert:
        ersatz = f"Kontakt {t.lead_id[:8]}"
        stamm = f"{t.datum.isoformat()}{_hhmm(t.uhrzeit)} {dateistamm(namen.get(t.lead_id), ersatz, MAX_NAME)}"
        # Schluessel beginnt mit der uid (Dateizusatz `(<uid[:8]>)`), die Lead-ID trennt gleiche uids je Kontakt.
        eintraege.append((f"{t.uid}-{t.lead_id}", stamm, _FEST))
    staemme = eindeutig(eintraege)
    eltern = f"{praefix}/{ORDNER}/{UEBERSICHT_STAMM}"
    notizen: list[Soll] = []
    gesetzt: list[tuple[Termin, str]] = []
    belegt: set[str] = set()
    for t in platziert:
        try:
            voll = relpfad(f"{praefix}/{ORDNER}", staemme[f"{t.uid}-{t.lead_id}"])
        except ValueError:
            log.warning("%s: Termin-Notiz hat keinen gueltigen Pfad, uebersprungen", firma)
            continue
        if voll.casefold() in belegt:
            continue
        belegt.add(voll.casefold())
        steckbrief_pfad = pfade[t.lead_id]
        kontakt_link = steckbrief_pfad[:-3] if steckbrief_pfad.endswith(".md") else steckbrief_pfad
        notizen.append(Soll(voll[len(praefix) + 1:], termin_schluessel(t.lead_id, t.uid),
                            _notiz(t, _name(namen.get(t.lead_id), t.lead_id), kontakt_link, eltern)))
        gesetzt.append((t, voll))
    notizen.sort(key=lambda s: s.rel)
    if not gesetzt and not archiv_vorhanden:
        return notizen
    gesetzt.sort(key=lambda g: (_sortierung(g[0]), g[1]))
    uebersicht = Soll(f"{ORDNER}/{UEBERSICHT_STAMM}.md", UEBERSICHT_TERMINE,
                      _uebersicht(firma, gesetzt, heute, f"{praefix}/Bereiche/_overview"))
    return [*notizen, uebersicht]


def gesperrte_termin_schluessel(ist_schluessel: Iterable[str], gesperrte_ids: set[str],
                                abgesagt: set[str]) -> set[str]:
    """Termin-Schluessel aus dem Bestand, deren Kontakt gesperrt oder deren Termin abgesagt ist."""
    treffer: set[str] = set()
    for s in ist_schluessel:
        if not s.startswith(TERMIN_PRAEFIX):
            continue
        teile = s[len(TERMIN_PRAEFIX):].split(":", 1)
        if len(teile) == 2 and (teile[0] in gesperrte_ids or teile[1] in abgesagt):
            treffer.add(s)
    return treffer
