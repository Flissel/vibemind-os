"""Duenner Einstieg: ``python -m spaces.coding <werkzeug> [--feld wert ...]``.

Ruft die festen Werkzeuge der Pipeline auf und gibt JSON aus. Startet keinen
Agenten und legt nicht fest, wo die Pipeline spaeter laeuft.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Callable, Dict, List, Optional

from spaces.coding import pipeline


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="spaces.coding", description="Werkzeuge des Project Space")
    befehle = parser.add_subparsers(dest="werkzeug", required=True)

    starten = befehle.add_parser("auftrag_starten", help="Auftrag anlegen (ohne Agentenlauf)")
    starten.add_argument("--aufgabe")
    starten.add_argument("--entwurf", help="Entwurf als Text oder JSON")
    starten.add_argument("--zielordner")
    starten.add_argument("--titel")
    starten.add_argument("--arbeitspaket", help="Kennung des Arbeitspakets bei vibemind.entwurf/1")

    for name in ("stand", "ergebnis", "abbrechen"):
        befehle.add_parser(name).add_argument("--auftrag")
    befehle.add_parser("liste").add_argument("--grenze", type=int, default=20)
    befehle.add_parser("unterbrochene_schliessen", help="nur beim Start des Dienstes, dem die Laeufe gehoeren")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    angaben = vars(_parser().parse_args(argv))
    werkzeug: Callable[..., Dict[str, Any]] = getattr(pipeline, angaben.pop("werkzeug"))
    try:
        antwort: Dict[str, Any] = {"ok": True, "ergebnis": werkzeug(**angaben)}
        code = 0
    except pipeline.PipelineFehler as fehler:
        antwort, code = {"ok": False, "fehler": str(fehler)}, 1
    sys.stdout.write(json.dumps(antwort, ensure_ascii=False, indent=2) + "\n")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
