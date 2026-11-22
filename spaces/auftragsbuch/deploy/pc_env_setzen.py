"""deploy/pc_env_setzen.py -- Name=Wert-Zeilen von stdin zeilensicher in eine .env schreiben.

Laeuft am PC (nur Standardbibliothek, Python 3.11). Fuer die Root-.env des aeusseren Repos, aus der
scripts/mcp_mit_env.py der Bruecke AUFTRAGSBUCH_URL und AUFTRAGSBUCH_SCHLUESSEL reicht:

  printf 'AUFTRAGSBUCH_URL=%s\\nAUFTRAGSBUCH_SCHLUESSEL=%s\\n' "$URL" "$SCHLUESSEL" \\
    | python deploy/pc_env_setzen.py <pfad-zur-.env>
  python deploy/pc_env_setzen.py <pfad-zur-.env> --entfernen AUFTRAGSBUCH_URL AUFTRAGSBUCH_SCHLUESSEL

Steht eine Variable schon in der Datei (nicht auskommentiert), wird ihre Zeile ersetzt, sonst
wird sie angehaengt; endet die Datei nicht mit einem Zeilenende, kommt erst eines dazu. Alle
anderen Zeilen bleiben Byte fuer Byte gleich, neue Zeilen bekommen das Zeilenende der Datei.
Die Datei muss es schon geben. Werte stehen nie als Argument und erscheinen in keiner Ausgabe;
Meldungen nennen nur Namen und Zeilennummern. Exit 2 bei jedem Fehler, die Datei bleibt dann
unveraendert.
"""
from __future__ import annotations

import os
import re
import shutil
import sys
import tempfile
from pathlib import Path
from typing import TextIO

_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


class EnvFehler(ValueError):
    """Bedienfehler; die Meldung nennt nie einen Wert."""


def eingabe_lesen(text: str) -> dict[str, str]:
    """Name=Wert-Zeilen von stdin, in ihrer Reihenfolge."""
    werte: dict[str, str] = {}
    for nummer, zeile in enumerate(text.splitlines(), start=1):
        if not zeile.strip():
            continue
        name, gleich, wert = zeile.partition("=")
        if not gleich or _NAME.fullmatch(name) is None:
            raise EnvFehler(f"Zeile {nummer} von stdin ist keine Zeile NAME=Wert")
        if name in werte:
            raise EnvFehler(f"{name} steht doppelt auf stdin")
        if not wert:
            raise EnvFehler(f"{name} hat einen leeren Wert")
        # scripts/mcp_mit_env.py liest Werte mit strip() und ohne umschliessende Anfuehrungszeichen.
        if wert != wert.strip().strip('"').strip("'"):
            raise EnvFehler(f"{name}: Wert mit Leerzeichen oder Anfuehrungszeichen am Rand wuerde anders gelesen")
        werte[name] = wert
    if not werte:
        raise EnvFehler("keine Zeile NAME=Wert auf stdin")
    return werte


def _name_der_zeile(zeile: str) -> str | None:
    inhalt = zeile.strip().lstrip("﻿")
    if not inhalt or inhalt.startswith("#") or "=" not in inhalt:
        return None
    return inhalt.split("=", 1)[0].strip()


def _zeilenende(inhalt: str) -> str:
    return "\r\n" if "\r\n" in inhalt else "\n"


def setzen(inhalt: str, werte: dict[str, str]) -> str:
    """Ersetzt vorhandene Zeilen der Variablen und haengt fehlende an."""
    ende = _zeilenende(inhalt)
    zeilen = inhalt.splitlines(keepends=True)
    gesetzt: set[str] = set()
    for i, zeile in enumerate(zeilen):
        name = _name_der_zeile(zeile)
        if name in werte:
            eigenes_ende = zeile[len(zeile.rstrip("\r\n")):]
            zeilen[i] = f"{name}={werte[name]}{eigenes_ende}"
            gesetzt.add(name)
    neu = "".join(zeilen)
    fehlend = [n for n in werte if n not in gesetzt]
    if fehlend:
        if neu and not neu.endswith("\n"):
            neu += ende
        neu += "".join(f"{n}={werte[n]}{ende}" for n in fehlend)
    return neu


def entfernen(inhalt: str, namen: list[str]) -> str:
    """Nimmt die Zeilen der genannten Variablen heraus; endet die Datei danach offen, kommt ein Zeilenende dazu."""
    ende = _zeilenende(inhalt)
    neu = "".join(z for z in inhalt.splitlines(keepends=True) if _name_der_zeile(z) not in namen)
    if neu and not neu.endswith("\n"):
        neu += ende
    return neu


def _schreiben(datei: Path, inhalt: str) -> None:
    """Schreibt in einem Zug (temporaere Datei daneben, dann ersetzen); Rechte der Datei bleiben.

    Die temporaere Datei heisst `<datei>.vor-schreiben-<zufall>` und faellt damit unter das Ignore-Muster
    `.env.vor-*`; sie wird mit Rechten nur fuer den Besitzer angelegt und in jedem Ausgang entfernt.
    """
    kennung, name = tempfile.mkstemp(prefix=datei.name + ".vor-schreiben-", dir=datei.parent)
    neu = Path(name)
    try:
        with os.fdopen(kennung, "wb") as ausgang:
            ausgang.write(inhalt.encode("utf-8"))
        shutil.copymode(datei, neu)
        os.replace(neu, datei)
    finally:
        neu.unlink(missing_ok=True)


def _lesen(datei: Path) -> str:
    if not datei.is_file():
        raise EnvFehler(f"{datei.name} gibt es nicht (Pfad pruefen; die Datei wird nicht neu angelegt)")
    try:
        return datei.read_bytes().decode("utf-8")
    except (OSError, UnicodeError):
        raise EnvFehler(f"{datei.name} nicht lesbar") from None


def main(argv: list[str], eingabe: TextIO | None = None, ausgabe: TextIO | None = None) -> int:
    ein = sys.stdin if eingabe is None else eingabe
    aus = sys.stdout if ausgabe is None else ausgabe
    try:
        if len(argv) >= 3 and argv[1] == "--entfernen":
            namen = argv[2:]
            if any(_NAME.fullmatch(n) is None for n in namen):
                raise EnvFehler("--entfernen erwartet Variablennamen")
            datei = Path(argv[0])
            _schreiben(datei, entfernen(_lesen(datei), namen))
            aus.write(f"pc_env_setzen: entfernt (falls vorhanden): {', '.join(namen)}\n")
            return 0
        if len(argv) == 1:
            datei = Path(argv[0])
            if ein.isatty():
                raise EnvFehler("die Werte kommen per Pipe auf stdin, nicht vom Terminal")
            werte = eingabe_lesen(ein.read())
            _schreiben(datei, setzen(_lesen(datei), werte))
            aus.write(f"pc_env_setzen: gesetzt: {', '.join(werte)}\n")
            return 0
    except EnvFehler as fehler:
        print(f"pc_env_setzen: {fehler}", file=sys.stderr)
        return 2
    except OSError as fehler:
        # nur der Typ: die Meldung eines OSError kann Pfade oder Inhalte tragen
        print(f"pc_env_setzen: Schreiben fehlgeschlagen ({type(fehler).__name__}); die Datei ist unveraendert",
              file=sys.stderr)
        return 2
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
