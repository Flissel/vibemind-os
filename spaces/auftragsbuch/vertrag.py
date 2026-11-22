"""Auftragsarten aus den Space-Vertraegen laden und pruefen.

Jeder Vertrag (`spaces/_contract/<space>.contract.yaml`) darf auf oberster Ebene
einen Abschnitt `auftragsarten` fuehren. Dieses Modul liest ihn, prueft ihn
streng und stellt die Form-Pruefung fuer Ein- und Ausgabe bereit.

Fehlertexte nennen nur Datei, Art, Feldpfad und verletzte Regel, nie Werte.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

_ART_MUSTER = re.compile(r"^[a-z][a-z0-9_]{2,40}$")
_NACHPRUEFUNG = ("form", "beleg")
_FELDER = {
    "art", "version", "veraendert", "frist_min", "nachpruefung",
    "nachpruefung_grund", "beschreibung", "eingabe", "ergebnis",
}
_PFLICHTFELDER = _FELDER - {"nachpruefung_grund"}


class VertragsFehler(ValueError):
    """Der Vertrag ist unvollstaendig oder verletzt eine Regel."""


class FormFehler(ValueError):
    """Ein Wert passt nicht zum Schema der Auftragsart (ohne den Wert zu nennen)."""


@dataclass(frozen=True)
class Auftragsart:
    space: str
    art: str
    version: int
    veraendert: bool
    frist_min: int
    nachpruefung: str
    nachpruefung_grund: str
    beschreibung: str
    eingabe: dict
    ergebnis: dict


def werkzeugname(a: Auftragsart) -> str:
    """Name des Werkzeugs: `<space>_<art>`, Bindestriche im Space werden Unterstriche."""
    return f"{a.space.replace('-', '_')}_{a.art}"


def _pfad(teile: object) -> str:
    text = "/".join(str(t) for t in teile)
    return text or "(Wurzel)"


def _ist_ganzzahl(wert: object) -> bool:
    return isinstance(wert, int) and not isinstance(wert, bool)


def _pruefe_schema(name: str, schema: object, ort: str) -> dict:
    if not isinstance(schema, dict):
        raise VertragsFehler(f"{ort}: {name} muss ein Objekt (JSON Schema) sein")
    if schema.get("type") != "object":
        raise VertragsFehler(f"{ort}: {name} braucht type: object auf oberster Ebene")
    if schema.get("additionalProperties") is not False:
        raise VertragsFehler(f"{ort}: {name} braucht additionalProperties: false auf oberster Ebene")
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as e:
        raise VertragsFehler(
            f"{ort}: {name} ist kein gueltiges JSON Schema "
            f"(Regel '{e.validator}' bei '{_pfad(e.path)}')"
        ) from None
    return schema


def _baue_art(space: str, roh: object, datei: str) -> Auftragsart:
    if not isinstance(roh, dict):
        raise VertragsFehler(f"{datei}: jeder Eintrag in auftragsarten muss ein Objekt sein")
    art = roh.get("art")
    ort = f"{datei} (art {art if isinstance(art, str) else '?'})"
    unbekannt = set(roh) - _FELDER
    if unbekannt:
        raise VertragsFehler(f"{ort}: unbekannte Felder: {', '.join(sorted(map(str, unbekannt)))}")
    fehlend = _PFLICHTFELDER - set(roh)
    if fehlend:
        raise VertragsFehler(f"{ort}: Pflichtfelder fehlen: {', '.join(sorted(fehlend))}")

    if not isinstance(art, str) or not _ART_MUSTER.match(art):
        raise VertragsFehler(f"{ort}: art muss {_ART_MUSTER.pattern} entsprechen")
    version = roh["version"]
    if not _ist_ganzzahl(version) or version < 1:
        raise VertragsFehler(f"{ort}: version muss eine ganze Zahl >= 1 sein")
    veraendert = roh["veraendert"]
    if not isinstance(veraendert, bool):
        raise VertragsFehler(f"{ort}: veraendert muss true oder false sein")
    frist = roh["frist_min"]
    if not _ist_ganzzahl(frist) or not 1 <= frist <= 120:
        raise VertragsFehler(f"{ort}: frist_min muss eine ganze Zahl von 1 bis 120 sein")
    nachpruefung = roh["nachpruefung"]
    if nachpruefung not in _NACHPRUEFUNG:
        raise VertragsFehler(f"{ort}: nachpruefung muss form oder beleg sein")
    grund = roh.get("nachpruefung_grund", "")
    if not isinstance(grund, str):
        raise VertragsFehler(f"{ort}: nachpruefung_grund muss ein Text sein")
    if veraendert and nachpruefung == "form" and not grund.strip():
        raise VertragsFehler(
            f"{ort}: nachpruefung_grund ist Pflicht, wenn veraendert: true und nachpruefung: form"
        )
    beschreibung = roh["beschreibung"]
    if not isinstance(beschreibung, str) or not beschreibung.strip():
        raise VertragsFehler(f"{ort}: beschreibung muss ein nicht leerer Text sein")

    return Auftragsart(
        space=space,
        art=art,
        version=version,
        veraendert=veraendert,
        frist_min=frist,
        nachpruefung=nachpruefung,
        nachpruefung_grund=grund,
        beschreibung=beschreibung,
        eingabe=_pruefe_schema("eingabe", roh["eingabe"], ort),
        ergebnis=_pruefe_schema("ergebnis", roh["ergebnis"], ort),
    )


def lade_auftragsarten(ordner: Path) -> dict[tuple[str, str], Auftragsart]:
    """Liest alle `*.contract.yaml` im Ordner; Schluessel ist `(space, art)`."""
    ergebnis: dict[tuple[str, str], Auftragsart] = {}
    for datei in sorted(Path(ordner).glob("*.contract.yaml")):
        try:
            vertrag = yaml.safe_load(datei.read_text(encoding="utf-8"))
        except yaml.YAMLError:
            raise VertragsFehler(f"{datei.name}: kein gueltiges YAML") from None
        if not isinstance(vertrag, dict):
            raise VertragsFehler(f"{datei.name}: oberste Ebene muss ein Objekt sein")
        arten = vertrag.get("auftragsarten")
        if arten is None:
            continue
        space = vertrag.get("id")
        if not isinstance(space, str) or not space:
            raise VertragsFehler(f"{datei.name}: id fehlt oder ist kein Text")
        if not isinstance(arten, list):
            raise VertragsFehler(f"{datei.name}: auftragsarten muss eine Liste sein")
        for roh in arten:
            a = _baue_art(space, roh, datei.name)
            if (space, a.art) in ergebnis:
                raise VertragsFehler(f"{datei.name} (art {a.art}): Auftragsart doppelt vorhanden")
            ergebnis[(space, a.art)] = a
    return ergebnis


def _pruefe_form(name: str, schema: dict, wert: object) -> None:
    fehler = sorted(
        Draft202012Validator(schema).iter_errors(wert),
        key=lambda e: (list(map(str, e.absolute_path)), e.validator),
    )
    if fehler:
        teile = [f"Regel '{e.validator}' bei '{_pfad(e.absolute_path)}'" for e in fehler]
        raise FormFehler(f"{name} verletzt das Schema: " + "; ".join(teile))


def pruefe_eingabe(a: Auftragsart, wert: object) -> None:
    _pruefe_form("eingabe", a.eingabe, wert)


def pruefe_ergebnis(a: Auftragsart, wert: object) -> None:
    _pruefe_form("ergebnis", a.ergebnis, wert)


def vertrag_hash(arten: dict) -> str:
    """sha256 ueber die kanonische JSON-Form aller Auftragsarten."""
    kanonisch = {f"{s}/{n}": asdict(a) for (s, n), a in arten.items()}
    text = json.dumps(kanonisch, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
