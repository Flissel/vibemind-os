"""Event-Kategorien mit Vererbung: laden, pruefen, wirksames Schema bilden."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

import yaml

from spaces.event_management.system import pruefung
from spaces.event_management.system.bausteine import FELDTYPEN, KERN, PFLICHT, ZUSAETZE

logger = logging.getLogger(__name__)

STANDARD_ORDNER = Path(__file__).resolve().parent.parent / "kategorien"
ECKDATEN = frozenset({"ort_adresse", "ort_link", "ende", "beschreibung"})
_BEKANNT = frozenset(KERN) | frozenset(ZUSAETZE)
_SCHLUESSEL = {"id", "name", "eltern", "abstrakt", "bausteine", "ohne", "felder",
               "begriffe", "eckdaten_pflicht"}


class KategorieFehler(ValueError):
    """Ungueltige Kategorie-Definition."""


@dataclass(frozen=True)
class Feld:
    id: str
    typ: str
    beschriftung: str
    pflicht: bool = False
    vorgabe: object = None
    optionen: tuple[str, ...] = ()


@dataclass(frozen=True)
class Kategorie:
    id: str
    name: str
    eltern: str | None
    abstrakt: bool
    bausteine: tuple[str, ...]
    ohne: tuple[str, ...]
    felder: tuple[Feld, ...]
    begriffe: Mapping[str, str]
    eckdaten_pflicht: tuple[str, ...]


@dataclass(frozen=True)
class WirksamesSchema:
    kategorie: str
    name: str
    kette: tuple[str, ...]
    bausteine: tuple[str, ...]
    felder: tuple[Feld, ...]
    begriffe: Mapping[str, str]
    eckdaten_pflicht: frozenset[str]


def _fehler(kid: str, datei: str, text: str) -> KategorieFehler:
    return KategorieFehler(f"Kategorie '{kid}' ({datei}): {text}")


def _liste(roh: object, kid: str, datei: str, schluessel: str) -> tuple[str, ...]:
    if roh is None:
        return ()
    if not isinstance(roh, list) or not all(isinstance(x, str) for x in roh):
        raise _fehler(kid, datei, f"'{schluessel}' muss eine Liste von Texten sein")
    return tuple(roh)


def _feld(roh: object, kid: str, datei: str) -> Feld:
    if not isinstance(roh, dict):
        raise _fehler(kid, datei, "Feld muss ein Mapping sein")
    unbekannt = set(roh) - {"id", "typ", "beschriftung", "pflicht", "vorgabe", "optionen"}
    if unbekannt:
        raise _fehler(kid, datei, f"unbekannte Feld-Schluessel {sorted(unbekannt)}")
    for schluessel in ("id", "typ", "beschriftung"):
        if not isinstance(roh.get(schluessel), str):
            raise _fehler(kid, datei, f"Feld braucht Text-Schluessel '{schluessel}'")
    if roh["typ"] not in FELDTYPEN:
        raise _fehler(kid, datei, f"unbekannter Feldtyp '{roh['typ']}' bei Feld '{roh['id']}'")
    optionen = _liste(roh.get("optionen"), kid, datei, "optionen")
    if roh["typ"] == "auswahl" and not optionen:
        raise _fehler(kid, datei, f"Feld '{roh['id']}' (auswahl) braucht mindestens eine Option")
    vorgabe = roh.get("vorgabe")
    if vorgabe is not None and not pruefung.metadatum_ok(roh["typ"], optionen, vorgabe):
        raise _fehler(kid, datei,
                      f"vorgabe von Feld '{roh['id']}' passt nicht zum typ '{roh['typ']}'")
    return Feld(roh["id"], roh["typ"], roh["beschriftung"], bool(roh.get("pflicht", False)),
                vorgabe, optionen)


def _parse(pfad: Path) -> Kategorie:
    datei = pfad.name
    kid = pfad.stem
    try:
        roh = yaml.safe_load(pfad.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise _fehler(kid, datei, f"kein gueltiges YAML: {exc}") from exc
    if not isinstance(roh, dict):
        raise _fehler(kid, datei, "Inhalt muss ein Mapping sein")
    if roh.get("id") != kid:
        raise _fehler(kid, datei, f"id '{roh.get('id')}' passt nicht zum Dateinamen")
    unbekannt = set(roh) - _SCHLUESSEL
    if unbekannt:
        raise _fehler(kid, datei, f"unbekannte Schluessel {sorted(unbekannt)}")
    if not isinstance(roh.get("name"), str) or "eltern" not in roh:
        raise _fehler(kid, datei, "'name' und 'eltern' sind Pflicht")
    eltern = roh["eltern"]
    if eltern is not None and not isinstance(eltern, str):
        raise _fehler(kid, datei, "'eltern' muss Text oder null sein")
    if kid == "basis" and eltern is not None:
        raise _fehler(kid, datei, "basis darf keinen Elternteil haben")
    if kid != "basis" and eltern is None:
        raise _fehler(kid, datei, "nur basis darf ohne Elternteil sein")
    bausteine = _liste(roh.get("bausteine"), kid, datei, "bausteine")
    ohne = _liste(roh.get("ohne"), kid, datei, "ohne")
    for b in (*bausteine, *ohne):
        if b not in _BEKANNT:
            raise _fehler(kid, datei, f"unbekannter Baustein '{b}'")
    for b in ohne:
        if b in PFLICHT:
            raise _fehler(kid, datei, f"Baustein '{b}' ist nicht abwaehlbar")
    felder_roh = roh.get("felder") or []
    if not isinstance(felder_roh, list):
        raise _fehler(kid, datei, "'felder' muss eine Liste sein")
    felder = tuple(_feld(f, kid, datei) for f in felder_roh)
    ids = [f.id for f in felder]
    for fid in ids:
        if ids.count(fid) > 1:
            raise _fehler(kid, datei, f"doppelte Feld-id '{fid}'")
    begriffe = roh.get("begriffe") or {}
    if not isinstance(begriffe, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in begriffe.items()):
        raise _fehler(kid, datei, "'begriffe' muss ein Mapping Text->Text sein")
    eckdaten = _liste(roh.get("eckdaten_pflicht"), kid, datei, "eckdaten_pflicht")
    unbekannte_eckdaten = set(eckdaten) - ECKDATEN
    if unbekannte_eckdaten:
        raise _fehler(kid, datei, f"unbekannte Eckdaten {sorted(unbekannte_eckdaten)}")
    return Kategorie(kid, roh["name"], eltern, bool(roh.get("abstrakt", False)), bausteine,
                     ohne, felder, MappingProxyType(dict(begriffe)), eckdaten)


def _kette(kategorien: Mapping[str, Kategorie], kategorie_id: str) -> tuple[Kategorie, ...]:
    if kategorie_id not in kategorien:
        raise KategorieFehler(f"Kategorie '{kategorie_id}' ist unbekannt")
    kette: list[Kategorie] = []
    gesehen: set[str] = set()
    aktuell = kategorien[kategorie_id]
    while True:
        datei = f"{aktuell.id}.yaml"
        if aktuell.id in gesehen:
            raise _fehler(kategorie_id, datei, f"Zyklus in der Vererbung bei '{aktuell.id}'")
        gesehen.add(aktuell.id)
        kette.append(aktuell)
        if aktuell.eltern is None:
            break
        if aktuell.eltern not in kategorien:
            raise _fehler(aktuell.id, datei, f"unbekannter Elternteil '{aktuell.eltern}'")
        aktuell = kategorien[aktuell.eltern]
    kette.reverse()
    return tuple(kette)


def wirksames_schema(kategorien: Mapping[str, Kategorie], kategorie_id: str) -> WirksamesSchema:
    """Fuehrt die Kette von basis bis zur Kategorie zusammen."""
    kette = _kette(kategorien, kategorie_id)
    bausteine: list[str] = []
    felder: dict[str, Feld] = {}
    begriffe: dict[str, str] = {}
    eckdaten: set[str] = set()
    for k in kette:
        datei = f"{k.id}.yaml"
        for b in k.ohne:
            if b not in bausteine:
                raise _fehler(k.id, datei, f"'ohne' nennt nicht geerbten Baustein '{b}'")
        bausteine = [b for b in bausteine if b not in k.ohne]
        bausteine += [b for b in k.bausteine if b not in bausteine]
        for f in k.felder:
            alt = felder.get(f.id)
            if alt is not None and alt.typ != f.typ:
                raise _fehler(k.id, datei,
                              f"Feld '{f.id}' darf den typ nicht von '{alt.typ}' auf '{f.typ}' aendern")
            felder[f.id] = f
        begriffe.update(k.begriffe)
        eckdaten |= set(k.eckdaten_pflicht)
    ziel = kette[-1]
    return WirksamesSchema(ziel.id, ziel.name, tuple(k.id for k in kette), tuple(bausteine),
                           tuple(felder.values()), MappingProxyType(begriffe), frozenset(eckdaten))


def lade_kategorien(ordner: Path | None = None) -> dict[str, Kategorie]:
    """Laedt alle *.yaml, prueft jede Kategorie und baut ihr wirksames Schema.

    Fail-closed: fehlender Ordner, kein einziges *.yaml oder fehlende `basis` sind ein
    KategorieFehler (sonst startete die API ohne waehlbare Kategorien).
    """
    ordner = ordner or STANDARD_ORDNER
    if not ordner.is_dir():
        raise KategorieFehler(f"Kategorien-Ordner fehlt: {ordner.name}")
    kategorien = {k.id: k for k in (_parse(p) for p in sorted(ordner.glob("*.yaml")))}
    if not kategorien:
        raise KategorieFehler(f"Keine Kategorien in {ordner.name}")
    if "basis" not in kategorien:
        raise KategorieFehler("Kategorie 'basis' fehlt")
    for kid in kategorien:
        wirksames_schema(kategorien, kid)
    logger.debug("%d Kategorien geladen aus %s", len(kategorien), ordner)
    return kategorien


def waehlbare(kategorien: Mapping[str, Kategorie]) -> list[Kategorie]:
    """Alle nicht abstrakten Kategorien, nach Name sortiert."""
    return sorted((k for k in kategorien.values() if not k.abstrakt), key=lambda k: k.name)
