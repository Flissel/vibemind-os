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

    `felder` bildet jede schreibbare Spalte auf einen Feldtyp ab (Einfuegereihenfolge =
    Anzeige-Reihenfolge): ``text``, ``datumzeit``, ``ganzzahl``, ``url`` (http/https),
    ``auswahl:<wert>,<wert>``, ``kontakt`` (Verweis auf `kontakt.id` derselben Firma),
    ``ref:<registry_eintrag>`` (Verweis auf eine Zeile eines anderen Eintrags desselben
    Events) oder ``uuid`` (nur ohne Verweis-Bedeutung). Die Fachschicht prueft ``kontakt``
    und ``ref:*`` wie ``uuid``; ob der Verweis passt, sichern die Fremdschluessel.
    `sortierung` ist eine Spaltenliste (kommagetrennt). Alle Namen stammen aus der
    Registry, nie aus Eingaben.
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
             "status": "auswahl:offen,in_arbeit,erledigt",
             "kategorie_id": "ref:aufgaben_kategorien", "verantwortlich_id": "kontakt",
             "faellig": "datumzeit"},
            frozenset({"titel"}), "angelegt, id",
        ),
        BausteinTabelle(
            "aufgaben_kategorien", "aufgabe_kategorie", {"name": "text"},
            frozenset({"name"}), "name, id", baustein="aufgaben",
        ),
        BausteinTabelle(
            "orga_team", "orga_team", {"kontakt_id": "kontakt", "rolle": "text"},
            frozenset({"kontakt_id"}), "angelegt, id",
        ),
        BausteinTabelle(
            "teilnehmende", "teilnehmende",
            {"kontakt_id": "kontakt", "zusage": "auswahl:offen,zugesagt,vielleicht,abgesagt"},
            frozenset({"kontakt_id"}), "angelegt, id",
        ),
        BausteinTabelle(
            "ablauf", "ablauf",
            {"beginn": "datumzeit", "ende": "datumzeit", "titel": "text", "ort": "text",
             "verantwortlich_id": "kontakt"},
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
