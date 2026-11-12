"""Bekannte Bausteine und Feldtypen der Event-Kategorien; Registry der Kern-Tabellen."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

KERN: tuple[str, ...] = (
    "aufgaben", "orga_team", "teilnehmende", "ablauf", "budget", "nachrichten", "verlauf",
)

ZUSAETZE: tuple[str, ...] = (
    "wettbewerb_teams", "abgaben", "jury", "aussteller", "staende", "sprecher",
    "online_raum", "aufzeichnung", "protokoll", "beschluesse", "verpflegung",
    "programm", "helferschichten", "buehnen",
)

PFLICHT = frozenset({"verlauf"})

FELDTYPEN = frozenset({
    "text", "langtext", "zahl", "ja_nein", "datum", "datumzeit", "auswahl", "url",
})


@dataclass(frozen=True)
class BausteinTabelle:
    """Tabelle eines Kern-Bausteins fuer die generische Fachschicht (`kern`).

    `felder` bildet jede schreibbare Spalte auf einen Feldtyp ab: ``text``, ``uuid``,
    ``datumzeit``, ``ganzzahl`` oder ``auswahl:<wert>,<wert>``. `sortierung` ist eine
    Spaltenliste (kommagetrennt). Alle Namen stammen aus der Registry, nie aus Eingaben.
    `baustein` ist der Schema-Baustein, zu dem die Tabelle gehoert (None = `name`).
    """

    name: str
    tabelle: str
    felder: Mapping[str, str]
    pflicht: frozenset[str]
    sortierung: str
    baustein: str | None = None


REGISTRY: dict[str, BausteinTabelle] = {
    t.name: t
    for t in (
        BausteinTabelle(
            "aufgaben", "aufgabe",
            {"titel": "text", "beschreibung": "text",
             "status": "auswahl:offen,in_arbeit,erledigt", "kategorie_id": "uuid",
             "verantwortlich_id": "uuid", "faellig": "datumzeit"},
            frozenset({"titel"}), "angelegt, id",
        ),
        BausteinTabelle(
            "aufgaben_kategorien", "aufgabe_kategorie", {"name": "text"},
            frozenset({"name"}), "name, id", baustein="aufgaben",
        ),
        BausteinTabelle(
            "orga_team", "orga_team", {"kontakt_id": "uuid", "rolle": "text"},
            frozenset({"kontakt_id"}), "angelegt, id",
        ),
        BausteinTabelle(
            "teilnehmende", "teilnehmende",
            {"kontakt_id": "uuid", "zusage": "auswahl:offen,zugesagt,vielleicht,abgesagt"},
            frozenset({"kontakt_id"}), "angelegt, id",
        ),
        BausteinTabelle(
            "ablauf", "ablauf",
            {"beginn": "datumzeit", "ende": "datumzeit", "titel": "text", "ort": "text",
             "verantwortlich_id": "uuid"},
            frozenset({"beginn", "titel"}), "beginn, id",
        ),
        BausteinTabelle(
            "budget", "budget_posten",
            {"titel": "text", "art": "auswahl:kosten,einnahme", "betrag_cent": "ganzzahl",
             "status": "auswahl:geplant,bestaetigt,bezahlt"},
            frozenset({"titel", "art", "betrag_cent"}), "angelegt, id",
        ),
    )
}
