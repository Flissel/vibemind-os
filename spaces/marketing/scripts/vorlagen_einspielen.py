"""Startvorlagen in marketing.newsletter_vorlagen einspielen (sales-claw Spec
2026-09-29-newsletter-editor-design.md §3.5). Ohne --wirklich nur pruefen:
je Datei `SELECT marketing.pult_bloecke_fehler(...)`, es wird nichts geaendert.
Mit --wirklich: eine Vorlage, deren gespeicherte Bloecke der Datei gleichen,
bleibt unangetastet ("unveraendert", keine neue Fassung). Eingespielt wird
immer als 'freigegeben' - eine vorhandene Freigabe wird nie zurueckgestuft.

    python -m spaces.marketing.scripts.vorlagen_einspielen [--wirklich]
"""
from __future__ import annotations

import json
import pathlib
import sys

from spaces.marketing.sync import _db

ORDNER = pathlib.Path(__file__).resolve().parents[1] / "vorlagen" / "newsletter"
# Startvorlagen sind freigegeben; pult_vorlage_speichern setzt den Status, den
# es bekommt - deshalb nie etwas Niedrigeres uebergeben.
STATUS = "freigegeben"


def main(wirklich: bool) -> int:
    fehler = 0
    for datei in sorted(ORDNER.glob("*.json")):
        v = json.loads(datei.read_text(encoding="utf-8"))
        if v.get("name") != datei.stem:
            print(f"{datei.name}: UNGUELTIG - name {v.get('name')!r} passt nicht zum Dateinamen")
            fehler += 1
            continue
        doc = _db._sql_literal(json.dumps(v["bloecke"], ensure_ascii=False))
        grund = _db.query_one(f"SELECT marketing.pult_bloecke_fehler({doc}::jsonb) AS f", streng=True)["f"]
        if grund:
            print(f"{datei.name}: UNGUELTIG - {grund}")
            fehler += 1
            continue
        gespeichert = _db.query_one(
            f"SELECT bloecke, status FROM marketing.newsletter_vorlagen WHERE name = {_db._sql_literal(v['name'])}",
            streng=True)
        if gespeichert and gespeichert.get("bloecke") == v["bloecke"]:
            print(f"{datei.name}: unveraendert (Fassung bleibt, Status {gespeichert.get('status')})")
            continue
        if wirklich:
            n = _db.query_one(
                f"SELECT marketing.pult_vorlage_speichern({_db._sql_literal(v['name'])}, "
                f"{_db._sql_literal(v['beschreibung'])}, {doc}::jsonb, 'startvorlagen', {_db._sql_literal(STATUS)}) AS n",
                streng=True)["n"]
            print(f"{datei.name}: eingespielt, Fassung {n}")
        else:
            print(f"{datei.name}: gueltig")
    return 1 if fehler else 0


if __name__ == "__main__":
    sys.exit(main("--wirklich" in sys.argv))
