"""Startvorlagen in marketing.newsletter_vorlagen einspielen (sales-claw Spec
2026-09-29-newsletter-editor-design.md §3.5). Ohne --wirklich nur pruefen:
je Datei `SELECT marketing.pult_bloecke_fehler(...)`, es wird nichts geaendert.
Mit --wirklich: eine Vorlage, deren gespeicherte Bloecke der Datei gleichen,
bleibt unangetastet ("unveraendert", keine neue Fassung). Eingespielt wird
immer als 'freigegeben' - eine vorhandene Freigabe wird nie zurueckgestuft.
Danach (Spec 2026-10-01-newsletter-vorlagen-profi §5.5) wird jede Datei-Vorlage
mit `fuer_alle = true` fuer jeden Laden freigegeben, und die alten Startvorlagen
(ALTE), zu denen es keine Datei mehr gibt, bekommen den Status 'zurueckgezogen' -
nur wenn alle Dateien gueltig waren. Bestehende Entwuerfe bleiben unberuehrt.

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
# Startvorlagen bis 01.10.2026; ohne Datei werden sie fuer neue Newsletter zurueckgezogen.
ALTE = ("newsletter", "ankuendigung", "einladung", "produkt-neuheit", "kurzer-hinweis")


def _schreiben(sql: str) -> str:
    """UPDATE ... RETURNING streng ausfuehren (ein SQL-Fehler bricht ab, statt still leer zu sein)."""
    return _db._run_psql(sql, None, streng=True)


def main(wirklich: bool) -> int:
    fehler = 0
    gueltig: list[str] = []
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
            gueltig.append(v["name"])
            continue
        if wirklich:
            n = _db.query_one(
                f"SELECT marketing.pult_vorlage_speichern({_db._sql_literal(v['name'])}, "
                f"{_db._sql_literal(v['beschreibung'])}, {doc}::jsonb, 'startvorlagen', {_db._sql_literal(STATUS)}) AS n",
                streng=True)["n"]
            print(f"{datei.name}: eingespielt, Fassung {n}")
        else:
            print(f"{datei.name}: gueltig")
        gueltig.append(v["name"])
    for name in gueltig:
        if wirklich:
            _schreiben(f"UPDATE marketing.newsletter_vorlagen SET fuer_alle = true "
                       f"WHERE name = {_db._sql_literal(name)} RETURNING name;")
            print(f"{name}: fuer alle Laeden freigegeben")
        else:
            print(f"{name}: wuerde fuer alle Laeden freigegeben")
    if fehler:
        print("Alte Startvorlagen bleiben, solange eine Datei ungueltig ist.")
        return 1
    for name in ALTE:
        if (ORDNER / f"{name}.json").exists():
            continue
        if not wirklich:
            print(f"{name}: würde zurückgezogen")
            continue
        aus = _schreiben(f"UPDATE marketing.newsletter_vorlagen SET status = 'zurueckgezogen' "
                         f"WHERE name = {_db._sql_literal(name)} AND status <> 'zurueckgezogen' RETURNING name;")
        print(f"{name}: zurückgezogen" if aus.strip() else f"{name}: nicht vorhanden oder schon zurückgezogen")
    return 0


if __name__ == "__main__":
    sys.exit(main("--wirklich" in sys.argv))
