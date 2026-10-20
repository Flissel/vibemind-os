"""Alle gespeicherten Mail-Fassungen der Collagen verwerfen, nachdem sich seite_mail/seite_streifen/seite_bilder
geaendert haben (RUNBOOK in db/081): sonst bleibt das alte MJML gespeichert und wird weiter verwendet.

Verworfen wird ALLES, unabhaengig vom Status des Inhalts: Marketing kann nicht wissen, ob ein Newsletter schon
verschickt ist, und jede Mail-Fassung entsteht bei Bedarf neu (Versand, Vorschau und Einreichen erzeugen fehlende
Zeilen/Dateien). Je Zeile marketing.pult_seite_mail_verwerfen. Dieses Skript loescht keine Dateien - die Ablage
liegt auf der VM; die verwaisten ms-Dateien raeumt das stuendliche Aufraeumen nach einem Tag ab.

Ohne --ausfuehren nur auflisten (nach Status des Inhalts gruppiert), nichts aendern.

    python -m spaces.marketing.scripts.seite_mail_neu [--ausfuehren]
"""
from __future__ import annotations

import sys
import uuid

from spaces.marketing.sync import _db

# Bewusst ohne WHERE: alle gespeicherten Mail-Fassungen (der Status dient nur der Anzeige).
AUSWAHL = ("SELECT m.inhalt::text AS inhalt, m.fassung, i.status FROM marketing.seite_mailfassungen m "
           "JOIN marketing.inhalte i ON i.id = m.inhalt ORDER BY i.status, m.inhalt, m.fassung")


def _kandidaten() -> list[tuple[str, int, str]]:
    """(inhalt, fassung, status) aller gespeicherten Mail-Fassungen; inhalt/fassung nur in sauberer Form."""
    aus = []
    for z in _db.query_via_docker(AUSWAHL, streng=True):
        aus.append((str(uuid.UUID(str(z["inhalt"]))), int(z["fassung"]), str(z.get("status") or "?")))
    return aus


def gruppiert(kandidaten: list[tuple[str, int, str]]) -> dict[str, list[tuple[str, int]]]:
    """Status -> [(inhalt, fassung)], Status alphabetisch, darin in Abfragereihenfolge."""
    gruppen: dict[str, list[tuple[str, int]]] = {}
    for inhalt, fassung, status in kandidaten:
        gruppen.setdefault(status, []).append((inhalt, fassung))
    return dict(sorted(gruppen.items()))


def main(ausfuehren: bool, ausgabe=print) -> int:
    kandidaten = _kandidaten()
    if not kandidaten:
        ausgabe("Keine gespeicherten Mail-Fassungen.")
        return 0
    for status, zeilen in gruppiert(kandidaten).items():
        ausgabe(f"[{status}] {len(zeilen)} Mail-Fassung(en)")
        for inhalt, fassung in zeilen:
            if not ausfuehren:
                ausgabe(f"  würde verwerfen: {inhalt} Fassung {fassung}")
                continue
            zeile = _db.query_one(
                f"SELECT marketing.pult_seite_mail_verwerfen('{inhalt}'::uuid, {fassung}) AS namen",
                streng=True) or {}
            ausgabe(f"  verworfen: {inhalt} Fassung {fassung} ({len(zeile.get('namen') or [])} Bildstücke)")
    if not ausfuehren:
        ausgabe(f"{len(kandidaten)} Mail-Fassung(en) - nichts geändert (mit --ausfuehren verwerfen).")
    return 0


if __name__ == "__main__":
    sys.exit(main("--ausfuehren" in sys.argv[1:]))
