"""Fuellt die Platzhalter {{NAME}} der Saat aus einer lokalen Wertedatei.

Das Repo ist oeffentlich: Event-Name, Datum, Ort, App-Adresse, Telegram-Gruppe
und erlaubte WhatsApp-Nummer stehen in config/ nur als Platzhalter. Die echten
Werte liegen in einer nie eingecheckten JSON-Datei (Vorlage:
werte.beispiel.json); dieses Skript schreibt config/ gefuellt in ein Zielverzeichnis
(ueblich: build/, gitignored), das docker-compose.yml als Saat einhaengt.

Schluessel gehoeren NICHT in die Wertedatei -- sie gehen nur per stdin ins
Laufzeit-Volume (Muster *-anbinden.sh).

Streng in beide Richtungen: ein fehlender Wert und ein Wert ohne Platzhalter
(Tippfehler) sind beide Fehler. Ausgaben nennen nur Namen, nie Werte.

Aufruf:  python deploy/fuellen.py <werte.json> <zielverzeichnis>
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

SPACE = Path(__file__).resolve().parents[1]
QUELLE = SPACE / "config"
PLATZHALTER = re.compile(r"\{\{([A-Z_]+)\}\}")


class FuellFehler(ValueError):
    """Wertedatei oder Ziel unbrauchbar. Die Meldung enthaelt nie Werte."""


def _pruefe_werte(werte: dict[str, object], benutzt: set[str]) -> None:
    fehlend = sorted(benutzt - set(werte))
    if fehlend:
        raise FuellFehler(f"Wert fehlt fuer: {', '.join(fehlend)}")
    ueberzaehlig = sorted(set(werte) - benutzt)
    if ueberzaehlig:
        raise FuellFehler(f"kein Platzhalter fuer: {', '.join(ueberzaehlig)}")
    for name, wert in werte.items():
        if not isinstance(wert, str) or not wert.strip():
            raise FuellFehler(f"{name}: muss ein nicht leerer Text sein")
        if "\n" in wert or "\r" in wert:
            raise FuellFehler(f"{name}: darf keinen Zeilenumbruch enthalten")
        if "{{" in wert or "}}" in wert:
            raise FuellFehler(f"{name}: darf keine Platzhalter-Klammern enthalten")


def fuellen(werte: dict[str, object], quelle: Path, ziel: Path) -> list[str]:
    """Schreibt jede Datei aus `quelle` gefuellt nach `ziel`.

    Gibt die geschriebenen Pfade relativ zu `ziel` (POSIX) zurueck.
    """
    quelle = quelle.resolve()
    ziel = ziel.resolve()
    if ziel == SPACE.resolve() or ziel.is_relative_to(quelle) or quelle.is_relative_to(ziel):
        raise FuellFehler("Ziel darf weder die Saat noch der Space-Ordner selbst sein")

    dateien = sorted(p for p in quelle.rglob("*") if p.is_file())
    texte = {p: p.read_text(encoding="utf-8") for p in dateien}
    benutzt = {name for text in texte.values() for name in PLATZHALTER.findall(text)}
    _pruefe_werte(werte, benutzt)

    geschrieben: list[str] = []
    for pfad, text in texte.items():
        ist_json = pfad.suffix == ".json"

        def ersetzen(treffer: re.Match[str]) -> str:
            wert = str(werte[treffer.group(1)])
            # In JSON stehen Platzhalter immer innerhalb eines Strings.
            return json.dumps(wert)[1:-1] if ist_json else wert

        gefuellt = PLATZHALTER.sub(ersetzen, text)
        relativ = pfad.relative_to(quelle)
        if ist_json:
            try:
                json.loads(gefuellt)
            except json.JSONDecodeError as fehler:
                raise FuellFehler(f"{relativ.as_posix()}: nach dem Fuellen kein JSON mehr "
                                  f"(Zeile {fehler.lineno})") from None
        ausgabe = ziel / relativ
        ausgabe.parent.mkdir(parents=True, exist_ok=True)
        ausgabe.write_text(gefuellt, encoding="utf-8", newline="\n")
        geschrieben.append(relativ.as_posix())
    return geschrieben


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__.strip().splitlines()[-1], file=sys.stderr)
        return 2
    werte_datei, ziel = Path(argv[0]), Path(argv[1])
    try:
        werte = json.loads(werte_datei.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as fehler:
        print(f"Wertedatei unlesbar: {type(fehler).__name__}", file=sys.stderr)
        return 2
    if not isinstance(werte, dict):
        print("Wertedatei muss ein JSON-Objekt sein", file=sys.stderr)
        return 2
    try:
        geschrieben = fuellen(werte, QUELLE, ziel)
    except FuellFehler as fehler:
        print(f"FEHLER: {fehler}", file=sys.stderr)
        return 2
    print(f"{len(geschrieben)} Dateien gefuellt ({len(werte)} Werte): " + ", ".join(geschrieben))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
