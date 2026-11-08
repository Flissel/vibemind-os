"""Ordnungs-Karte fuer Rowboats Wissens-Graph erneuern (Timer `rowboat-vault-map`, alle 10 Minuten).

Im Fern-Modus liest Rowboat `config/vault-map.json` aus dem Datenordner auf der VM (`~/rowboat-data`).
Notizen ohne `parent:` im Kopf bekommen dort ihren Platz in der Hierarchie und damit eine Linie im Graph.
Am PC erneuert `start_desktop.py` die Karte nur ausserhalb des Fern-Modus; ohne diesen Lauf veraltet sie.

Gerechnet wird mit `ehl_schema` (Sync-Kopie unter `spaces/rowboat/schema`, nie von Hand aendern), genau wie
`python -m ehl_schema.cli map`: Karte schreiben (nur bei inhaltlicher Aenderung) und fehlende Strukturnotizen
anlegen, bestehende Notizen nie aendern. Anders als die CLI schreibt dieser Aufruf nur EINE Zeile ins Journal
statt einer je Notiz ohne Ordnerregel.

Lauf: `python3 karte.py [--workdir DIR] [--profil NAME]`. Exit 0 ok (auch: Datei waehrend des Lesens ersetzt,
der naechste Lauf holt es nach), 2 kein Wissensordner oder Profil/Schema unbrauchbar.
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

SCHEMA_ORDNER = Path(__file__).resolve().parents[1] / "schema"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Ordnungs-Karte (config/vault-map.json) fuer Rowboat erneuern")
    parser.add_argument("--workdir", type=Path, default=Path.home() / "rowboat-data", help="Rowboat-Datenordner")
    parser.add_argument("--profil", default="vibemind", help="Profil unter spaces/rowboat/schema/profiles")
    args = parser.parse_args(argv)

    if str(SCHEMA_ORDNER) not in sys.path:
        sys.path.insert(0, str(SCHEMA_ORDNER))
    from ehl_schema.model import DEFAULT_SCHEMA, SchemaError, load_schema
    from ehl_schema.profile import load_profile
    from ehl_schema.vaultmap import run_map

    if not (args.workdir / "knowledge").is_dir():
        print(f"Karte: kein Wissensordner unter {args.workdir}", file=sys.stderr)
        return 2
    try:
        schema = load_schema(DEFAULT_SCHEMA)
        profil = load_profile(args.profil, schema)
    except SchemaError as fehler:
        print(f"Karte: {fehler}", file=sys.stderr)
        return 2
    try:
        ergebnis = run_map(schema, profil, args.workdir)
    except OSError as fehler:  # ein Schreiber hat eine Datei waehrend des Lesens ersetzt
        print(f"Karte: {fehler} - der naechste Lauf holt es nach")
        return 0
    hinweise = Counter(hinweis.code for hinweis in ergebnis.issues)
    print(f"Karte: {ergebnis.notes} Notizen platziert, {ergebnis.excluded} ausgeschlossen, "
          f"{ergebnis.structure} Strukturnotizen geschrieben, vault-map.json "
          f"{'erneuert' if ergebnis.written else 'unveraendert'}; "
          f"Hinweise: {', '.join(f'{c} {n}' for c, n in sorted(hinweise.items())) or 'keine'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
