"""Bekannte Bausteine und Feldtypen der Event-Kategorien."""

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
