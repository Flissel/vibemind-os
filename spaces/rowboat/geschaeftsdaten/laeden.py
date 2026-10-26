"""Laeden (sales-claw-Instanzen) und ihre Firmenordner in Rowboats Wissen.

Laden -> Marke steht in den Env-Dateien von sales-claw, nicht in der DB: `~/sales-claw/.env`
(Basis-Laden) und `~/sales-claw/deploy/laeden/*.env` (ohne `beispiel.env`). Die Datei wird zeilenweise
gelesen, uebernommen werden aber nur die Schluessel in `SCHLUESSEL`; andere Werte (auch Geheimnisse)
werden verworfen, nie zurueckgegeben und nie protokolliert. Der Basis-Laden nimmt `LADEN_MARKE`, sonst
`MARKETING_MANDANT`; eine Datei unter `deploy/laeden/` braucht `LADEN_MARKE` (kein stiller Rueckfall
auf `MARKETING_MANDANT`, der dort meist die Marketing-Instanz und nicht die Laden-Marke meint). Ohne
Marke, mit ungueltigem Schema oder doppeltem Schema wird ein Laden uebersprungen (fail-closed).
Warnungen nennen nur Dateiname und Grund, nie Werte.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from namen import dateistamm

SCHEMA_MUSTER = re.compile(r"^sales(_[a-z][a-z0-9_]{0,30})?$")
MARKE_MUSTER = re.compile(r"^[a-z][a-z0-9_]{1,30}$")
TEST_SCHEMA = "sales_test"
SAMMEL_SCHLUESSEL = ("INBOX_UNBEKANNT_LEAD_ID", "RECHERCHE_LEAD_ID", "LINKEDIN_POST_LEAD_ID")
SCHLUESSEL = frozenset({"SALES_DB_SCHEMA", "LADEN_MARKE", "MARKETING_MANDANT", *SAMMEL_SCHLUESSEL})
VORLAGE = "beispiel.env"
FIRMEN = "companys"


@dataclass(frozen=True)
class Laden:
    schema: str
    marke: str
    sammel_ids: frozenset[str]


def env_werte(pfad: Path, schluessel: frozenset[str]) -> dict[str, str]:
    """Nur die genannten Schluessel aus einer Env-Datei; Werte ohne Rand-Leerraum (auch CR) und
    ohne umschliessende Anfuehrungszeichen. Der letzte Eintrag gewinnt."""
    werte: dict[str, str] = {}
    for zeile in pfad.read_text(encoding="utf-8", errors="replace").splitlines():
        zeile = zeile.strip()
        if not zeile or zeile.startswith("#") or "=" not in zeile:
            continue
        name, _, wert = zeile.partition("=")
        name = name.strip()
        if name.startswith("export "):
            name = name[len("export "):].strip()
        if name not in schluessel:
            continue
        werte[name] = wert.strip().strip("\"'").strip()
    return werte


def _laden_aus(pfad: Path, basis: bool) -> tuple[Laden | None, str | None]:
    werte = env_werte(pfad, SCHLUESSEL)
    schema = werte.get("SALES_DB_SCHEMA") or "sales"
    if not SCHEMA_MUSTER.match(schema):
        return None, f"{pfad.name}: ungueltiges Schema"
    if schema == TEST_SCHEMA:
        return None, f"{pfad.name}: {TEST_SCHEMA} wird nie gelesen"
    if basis:
        marke = werte.get("LADEN_MARKE") or werte.get("MARKETING_MANDANT") or ""
    else:
        marke = werte.get("LADEN_MARKE") or ""
        if not marke:
            return None, f"{pfad.name}: keine LADEN_MARKE"
    if not marke:
        return None, f"{pfad.name}: keine Marke"
    if not MARKE_MUSTER.match(marke):
        return None, f"{pfad.name}: ungueltige Marke"
    ids = frozenset(w for k in SAMMEL_SCHLUESSEL if (w := werte.get(k, "")))
    return Laden(schema, marke, ids), None


def laeden_finden(sales_claw: Path) -> tuple[list[Laden], list[str]]:
    """Alle Laeden mit Marke (sortiert nach Schema) und die Warnungen."""
    warnungen: list[str] = []
    dateien: list[Path] = []
    basis = sales_claw / ".env"
    if basis.is_file():
        dateien.append(basis)
    else:
        warnungen.append(".env: fehlt")
    laden_ordner = sales_claw / "deploy" / "laeden"
    if laden_ordner.is_dir():
        dateien += sorted(p for p in laden_ordner.glob("*.env") if p.is_file() and p.name != VORLAGE)

    gefunden: dict[str, list[tuple[Path, Laden]]] = {}
    for pfad in dateien:
        laden, warnung = _laden_aus(pfad, basis=pfad == basis)
        if warnung:
            warnungen.append(warnung)
        if laden:
            gefunden.setdefault(laden.schema, []).append((pfad, laden))

    laeden: list[Laden] = []
    for schema in sorted(gefunden):
        eintraege = gefunden[schema]
        if len(eintraege) > 1:
            warnungen += [f"{p.name}: Schema mehrfach vergeben" for p, _ in eintraege]
            continue
        laeden.append(eintraege[0][1])
    return laeden, warnungen


def firmenordner(wissen: Path, marke_name: str) -> Path:
    """Ordner `companys/<Firma>` unter `wissen` (knowledge/). Ein vorhandener Ordner wird ohne
    Gross-/Kleinschreibung gefunden, sonst ist es `companys/<marke_name>` (nicht angelegt).
    Wirft `ValueError` bei ungueltigem Namen, Symlink oder mehrdeutigem Treffer."""
    name = unicodedata.normalize("NFC", marke_name or "")
    if not name or dateistamm(name, "", len(name)) != name:
        raise ValueError("ungueltiger Firmenname")
    firmen = wissen / FIRMEN
    if firmen.is_symlink():
        raise ValueError("companys ist ein Symlink")
    treffer: list[Path] = []
    if firmen.is_dir():
        treffer = [p for p in firmen.iterdir()
                   if unicodedata.normalize("NFC", p.name).casefold() == name.casefold()]
    if len(treffer) > 1:
        genau = [p for p in treffer if unicodedata.normalize("NFC", p.name) == name]
        if len(genau) != 1:
            raise ValueError("Firmenordner mehrdeutig")
        treffer = genau
    if not treffer:
        return firmen / name
    ordner = treffer[0]
    if ordner.is_symlink() or not ordner.is_dir():
        raise ValueError("Firmenordner ist kein echter Ordner")
    return ordner
