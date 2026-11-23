"""(42) Die Liste der Stände in spaces/ideas/ANZEIGE.md stimmt mit core/auftrag_stand überein."""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import auftrag_stand as st  # noqa: E402

ANZEIGE = Path(__file__).resolve().parents[3] / "spaces" / "ideas" / "ANZEIGE.md"

ZEILEN = {
    "laeuft": {"status": "offen"},
    "fertig": {"status": "fertig", "pruefung": {"rueckweg": "geschrieben", "meldung": "Die Notiz ist ausformuliert."}},
    "vorschlag": {"status": "fertig", "pruefung": {"rueckweg": "vorschlag", "meldung": "Geändert."}},
    "uebernommen": {"status": "fertig", "pruefung": {"rueckweg": "vorschlag", "vorschlag_erledigt": "uebernommen"}},
    "verworfen": {"status": "fertig", "pruefung": {"rueckweg": "vorschlag", "vorschlag_erledigt": "verworfen",
                                                   "titel": "Sponsoren"}},
    "abgebrochen": {"status": "abgelehnt", "pruefung": {"rueckweg": "abgebrochen"}},
    "wartet": {"status": "fertig", "pruefung": {"rueckweg": "wartet", "meldung": "Wartet auf Freigabe."}},
    "ungeprueft": {"status": "fertig", "pruefung": {"verified": None}},
    "fehler": {"status": "fertig", "pruefung": {"rueckweg": "abgelehnt", "meldung": "Abgelehnt."}},
}


def _tabelle() -> dict[str, tuple[str, str]]:
    text = ANZEIGE.read_text(encoding="utf-8")
    abschnitt = text[text.index("## Stand eines Agenten-Auftrags"):]
    abschnitt = abschnitt[:abschnitt.index("\n## ", 5)]
    return {m.group(1): (m.group(2), m.group(3))
            for m in re.finditer(r"^\| `(\w+)` \| (`true`|`false`|–) \| (`true`|–) \|", abschnitt, re.MULTILINE)}


def _wert(ergebnis: dict, feld: str) -> str:
    return "–" if feld not in ergebnis else f"`{str(ergebnis[feld]).lower()}`"


def test_die_liste_nennt_jeden_stand_genau_mit_ok_und_hinweis() -> None:
    tabelle = _tabelle()
    ergebnisse = {name: st.stand_einer_zeile(zeile) for name, zeile in ZEILEN.items()}
    ergebnisse["unbekannt"] = st.stand("keine-kennung", lambda i: None)
    assert {e["stand"] for e in ergebnisse.values()} == set(ergebnisse)       # jede Zeile trifft ihren Stand
    assert set(tabelle) == set(ergebnisse)
    for name, ergebnis in ergebnisse.items():
        assert tabelle[name] == (_wert(ergebnis, "ok"), _wert(ergebnis, "hinweis")), name


def test_die_liste_kennt_alle_staende_des_codes() -> None:
    # Jeder Stand, den der Code als Literal setzt, steht in der Liste.
    code = (Path(st.__file__)).read_text(encoding="utf-8")
    im_code = set(re.findall(r'"stand":\s*"(\w+)"', code))
    assert im_code == set(_tabelle())
