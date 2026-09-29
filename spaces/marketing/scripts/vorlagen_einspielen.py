"""Startvorlagen in marketing.newsletter_vorlagen einspielen (sales-claw Spec
2026-09-29-newsletter-editor-design.md §3.5). Ohne --wirklich nur pruefen:
je Datei `SELECT marketing.pult_bloecke_fehler(...)`, es wird nichts geaendert.

    python -m spaces.marketing.scripts.vorlagen_einspielen [--wirklich]
"""
from __future__ import annotations

import json
import pathlib
import sys

from spaces.marketing.sync import _db

ORDNER = pathlib.Path(__file__).resolve().parents[1] / "vorlagen" / "newsletter"


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
        if wirklich:
            n = _db.query_one(
                f"SELECT marketing.pult_vorlage_speichern({_db._sql_literal(v['name'])}, "
                f"{_db._sql_literal(v['beschreibung'])}, {doc}::jsonb, 'startvorlagen', 'freigegeben') AS n",
                streng=True)["n"]
            print(f"{datei.name}: eingespielt, Fassung {n}")
        else:
            print(f"{datei.name}: gueltig")
    return 1 if fehler else 0


if __name__ == "__main__":
    sys.exit(main("--wirklich" in sys.argv))
