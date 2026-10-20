"""Nicht versendete Mail-Fassungen der Collagen verwerfen, nachdem sich seite_mail/seite_streifen/seite_bilder
geaendert haben (RUNBOOK in db/081): sonst bleibt das alte MJML gespeichert und wird weiter verwendet. Die API
erzeugt eine verworfene Mail-Fassung beim naechsten Bedarf neu; die verwaisten ms-Dateien raeumt das stuendliche
Aufraeumen nach einem Tag ab (dieses Skript loescht keine Dateien - die Ablage liegt auf der VM).

Verworfen werden alle Zeilen aus marketing.seite_mailfassungen, deren Fassung weder die eingereichte noch die
freigegebene ihres Inhalts ist (je Zeile marketing.pult_seite_mail_verwerfen). Ohne --ausfuehren wird nur
aufgelistet, nichts geaendert.

    python -m spaces.marketing.scripts.seite_mail_neu [--ausfuehren]
"""
from __future__ import annotations

import sys
import uuid

from spaces.marketing.sync import _db

AUSWAHL = ("SELECT m.inhalt::text AS inhalt, m.fassung FROM marketing.seite_mailfassungen m "
           "JOIN marketing.inhalte i ON i.id = m.inhalt "
           "WHERE m.fassung IS DISTINCT FROM i.eingereichte_fassung "
           "AND m.fassung IS DISTINCT FROM i.freigegebene_fassung "
           "ORDER BY m.inhalt, m.fassung")


def _kandidaten() -> list[tuple[str, int]]:
    aus = []
    for z in _db.query_via_docker(AUSWAHL, streng=True):
        aus.append((str(uuid.UUID(str(z["inhalt"]))), int(z["fassung"])))   # nur saubere Werte ins SQL
    return aus


def main(ausfuehren: bool, ausgabe=print) -> int:
    kandidaten = _kandidaten()
    if not kandidaten:
        ausgabe("Keine ungesendeten Mail-Fassungen.")
        return 0
    for inhalt, fassung in kandidaten:
        if not ausfuehren:
            ausgabe(f"wuerde verwerfen: {inhalt} Fassung {fassung}")
            continue
        zeile = _db.query_one(f"SELECT marketing.pult_seite_mail_verwerfen('{inhalt}'::uuid, {fassung}) AS namen",
                              streng=True) or {}
        ausgabe(f"verworfen: {inhalt} Fassung {fassung} ({len(zeile.get('namen') or [])} Bildstücke)")
    if not ausfuehren:
        ausgabe(f"{len(kandidaten)} Mail-Fassung(en) - nichts geändert (mit --ausfuehren verwerfen).")
    return 0


if __name__ == "__main__":
    sys.exit(main("--ausfuehren" in sys.argv[1:]))
