"""Wochenbild je Firma: KW-Rechnung und Markdown fuer die letzte abgeschlossene ISO-Woche (rein).

Nur Zahlen und Titel, nie Namen von Kontakten. Gesperrte Kontakte (privat, Loeschantrag) zaehlen
nirgends, archivierte nicht in der Pipeline. Zeitraum: Montag 00:00 bis naechster Montag 00:00 in
Europe/Berlin (halboffen), so stimmt die Grenze auch in der Woche der Zeitumstellung.
"""
from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from quelle import Ereignis, Kontakt, Medium
from steckbrief import STUFEN

BERLIN = ZoneInfo("Europe/Berlin")
ERZEUGT_VON = "rowboat-geschaeftsdaten"
_LEERRAUM = re.compile(r"\s+")
_AUSLOESER = re.compile(r"@(rowboat)", re.IGNORECASE)
MEDIEN_ARTEN = {"reel": "Reel", "podcast": "Podcast"}


def letzte_kw(heute: date) -> tuple[int, int]:
    """(ISO-Jahr, KW) der letzten abgeschlossenen Woche vor `heute`."""
    sonntag = heute - timedelta(days=heute.isoweekday())
    jahr, kw, _ = sonntag.isocalendar()
    return jahr, kw


def zeitraum(jahr: int, kw: int) -> tuple[datetime, datetime]:
    """[Montag 00:00, naechster Montag 00:00) in Berlin, tz-aware."""
    montag = date.fromisocalendar(jahr, kw, 1)
    return (datetime.combine(montag, time(0), tzinfo=BERLIN),
            datetime.combine(montag + timedelta(days=7), time(0), tzinfo=BERLIN))


def stamm(jahr: int, kw: int) -> str:
    return f"{jahr}-KW{kw:02d}"


def _einzeilig(text: str | None) -> str:
    return _AUSLOESER.sub(r"@ \1", _LEERRAUM.sub(" ", text or "").strip())


def _dauer(sekunden: float | None) -> str:
    if sekunden is None or sekunden < 0:
        return ""
    s = int(round(sekunden))
    return f" ({s // 60}:{s % 60:02d})"


def _termine(ereignisse: list[Ereignis], von: date, bis: date) -> int:
    """Termine mit Datum in [von, bis), je uid der spaeteste Eintrag, ohne abgesagte."""
    abgesagt = {e.termin_uid for e in ereignisse if e.typ == "termin_abgesagt" and e.termin_uid}
    je_uid: dict[str, Ereignis] = {}
    ohne_uid: list[Ereignis] = []
    for e in sorted(ereignisse, key=lambda e: e.zeit):
        if e.typ != "termin":
            continue
        if e.termin_uid:
            je_uid[e.termin_uid] = e
        else:
            ohne_uid.append(e)
    anzahl = 0
    for e in [*je_uid.values(), *ohne_uid]:
        if e.termin_uid in abgesagt:
            continue
        try:
            tag = date.fromisoformat(e.termin_datum or "")
        except ValueError:
            continue
        anzahl += von <= tag < bis
    return anzahl


def wochenbild(firma: str, jahr: int, kw: int, kontakte: list[Kontakt], ereignisse: list[Ereignis],
               newsletter: list[tuple[str, int]], medien: list[Medium]) -> str:
    """Markdown des Wochenbilds. `newsletter` sind (Link-Ziel ohne `.md`, Empfaenger) der Woche;
    `medien` werden hier auf den Zeitraum gefiltert."""
    von, bis = zeitraum(jahr, kw)
    sonntag = (bis - timedelta(days=1)).date()
    gesperrt = {k.id for k in kontakte if k.gesperrt}
    offen = [k for k in kontakte if not k.gesperrt]
    eigene = [e for e in ereignisse if e.lead_id not in gesperrt]

    pipeline: dict[str, int] = {}
    for k in offen:
        if not k.archiviert:
            stufe = STUFEN.get(k.status or "", _einzeilig(k.status) or "Ohne Stufe")
            pipeline[stufe] = pipeline.get(stufe, 0) + 1
    reihenfolge = [s for s in STUFEN.values() if s in pipeline] + sorted(set(pipeline) - set(STUFEN.values()))

    neue = sum(1 for k in offen if von <= k.created_at < bis)
    wechsel = sum(1 for e in eigene if e.typ == "stufenwechsel" and von <= e.zeit < bis)
    termine = _termine(eigene, von.date(), bis.date())
    woche_medien = sorted((m for m in medien if von <= m.fertig_am < bis), key=lambda m: (m.fertig_am, m.titel))

    zeilen = [
        "---",
        f"erzeugt_von: {ERZEUGT_VON}",
        "quelle: sales-claw",
        f"schluessel: kw:{jahr}-W{kw:02d}",
        "art: wochenbild",
        "schlagwoerter: [wochenbild]",
        f"stand: {sonntag.isoformat()}",
        "---",
        "",
        f"> Automatisch erzeugt, Quelle sales-claw, Stand {sonntag:%d.%m.%Y}. Nicht hier bearbeiten – der "
        "nächtliche Lauf überschreibt; eigene Anmerkungen in eine eigene Notiz.",
        "",
        f"# Wochenbild {_einzeilig(firma)} – KW {kw:02d}/{jahr}",
        "",
        f"Zeitraum: {von.date():%d.%m.%Y} bis {sonntag:%d.%m.%Y}",
        "",
        "## Pipeline (Stand bei Erstellung)",
        "",
        *[f"- {s}: {pipeline[s]}" for s in reihenfolge],
        f"- Kontakte in der Pipeline: {sum(pipeline.values())}",
        "",
        "## Woche",
        "",
        f"- Neue Kontakte: {neue}",
        f"- Stufenwechsel: {wechsel}",
        f"- Termine: {termine}",
        "",
        "## Newsletter",
        "",
    ]
    zeilen += ([f"- [[{ziel}]] – {n} Empfänger" for ziel, n in newsletter]
               or ["Keine Newsletter in dieser Woche."])
    zeilen += ["", "## Reels und Podcasts", ""]
    zeilen += ([f"- {MEDIEN_ARTEN.get(m.art, m.art)}: {_einzeilig(m.titel)}{_dauer(m.dauer_s)}"
                for m in woche_medien] or ["Keine fertigen Reels oder Podcasts."])
    return "\n".join(zeilen) + "\n"
