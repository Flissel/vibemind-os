"""Feste Antworten auf blossen Gruss, Dank und Zustimmung, ohne Modell.

Lauf 22 der Pruefung: "Danke", "ok", "Hallo" bekamen die allgemeine Rueckfrage "Das habe ich
nicht verstanden. In Ideen kann ich z. B. ...", obwohl nichts zu verstehen war. Die festen
Hoeflichkeitsantworten der App griffen nie, weil das Gehirn zuerst antwortete.

Nur der ganze Satz zaehlt ("Danke, und jetzt ordne die Karten" ist kein blosser Dank).
Ein blosses "ja"/"ok" antwortet nur, wenn keine Rueckfrage offen ist; sonst laeuft der
bisherige Weg (eine offene Loesch- oder White-Paper-Frage braucht ihr eigenes "Ja, ...").
"""
from __future__ import annotations

import re
import time
from typing import Any, Optional

GRUSS = "Hallo! Wie kann ich dir helfen?"
DANK = "Gern geschehen!"
ZUSTIMMUNG = "Alles klar."
# Pruefung 25.11.2026: ein blosses "Ja" ohne offene Rueckfrage bekam "Alles klar." und klang
# wie eine Bestaetigung, obwohl nichts geschah; "nein" lief ins Chat-Ereignis.
JA_OHNE_FRAGE = "Gerade ist nichts zu bestätigen; geändert habe ich nichts."
NEIN_OFFEN = "Gut, dann lasse ich es. Geändert habe ich nichts."
NEIN_OHNE_FRAGE = "Gerade ist keine Frage offen; geändert habe ich nichts."

# Pruefung 25.11.2026: kopiert als  Ja, erledigen“.  ging die feste Form nicht und lief 16 s ins
# Modell. Bestaetigungen gelten auch mit umgebenden Anfuehrungszeichen und Satzzeichen.
_RAND_VORN = r"""^[\s"'„“”‚‘’«»]*"""
_RAND_HINTEN = r"""[\s.!?…"'„“”‚‘’«»]*$"""
_ENDE = r"""[\s.!?:)…"'„“”‚‘’«»]*$"""
_ARTEN = (
    ("gruss", re.compile(
        _RAND_VORN + r"(?:hallo|hallöchen|hi|hey|moin(?:\s+moin)?|servus|grüß\s+(?:dich|gott)|gruess\s+dich|"
        r"guten\s+(?:morgen|tag|abend)|hello|good\s+(?:morning|evening))"
        r"(?:\s*,?\s*(?:zusammen|du|claude|gehirn))?" + _ENDE, re.IGNORECASE)),
    ("dank", re.compile(
        _RAND_VORN + r"(?:danke(?:\s*(?:schön|schoen|sehr|dir|vielmals))*|dankeschön|dankeschoen|vielen\s+dank|"
        r"besten\s+dank|merci|thanks?(?:\s+you)?|thank\s+you|thx)" + _ENDE, re.IGNORECASE)),
    ("ja", re.compile(_RAND_VORN + r"(?:ja|jo|jep|jawohl|ok|okay|okey|oki)" + _ENDE, re.IGNORECASE)),
    ("zustimmung", re.compile(
        _RAND_VORN + r"(?:alles\s+klar|gut|super|passt|prima|verstanden)" + _ENDE, re.IGNORECASE)),
    ("nein", re.compile(
        _RAND_VORN + r"(?:nein(?:\s*,?\s*danke)?|nee|ne|nö|noe|lieber\s+nicht|doch\s+nicht|abbrechen|"
        r"lass\s+es|stopp?)" + _ENDE, re.IGNORECASE)),
)
_ANTWORT = {"gruss": GRUSS, "dank": DANK, "zustimmung": ZUSTIMMUNG}


def art(satz: Any) -> Optional[str]:
    """"gruss", "dank", "zustimmung" oder ``None``. Wirft nie."""
    if not isinstance(satz, str) or len(satz) > 60:
        return None
    for name, muster in _ARTEN:
        if muster.match(satz):
            return name
    return None


def etwas_offen() -> bool:
    """Wartet irgendeine Rueckfrage auf eine Antwort? Nur lesen, nichts verwerfen. Wirft nie."""
    try:
        jetzt_wand, jetzt_mono = time.time(), time.monotonic()
        from . import befoerdern_bestaetigung as bf
        from . import coding_auftrag as ca
        from . import loesch_bestaetigung as lb
        from . import recherche_bubble as rb
        from . import verstehen
        from . import whitepaper_bestaetigung as wp
        from . import todo_bestaetigung as td
        from . import ideen_abkuerzung as ab

        for offen in (lb._offen, wp._offen, td._offen, rb._offen, bf._offen, verstehen._bubble_offen):
            if offen and float(offen.get("bis", jetzt_wand + 1) or 0) >= jetzt_wand:
                return True
        # Bauauftraege haben je Rueckfrage eine eigene frage_id (core/coding_auftrag).
        if ca.etwas_offen(jetzt_wand):
            return True
        return any(jetzt_mono - float(eintrag.get("zeit", 0)) <= ab.OFFEN_GILT_S
                   for eintrag in list(ab._OFFEN.values()) if isinstance(eintrag, dict))
    except Exception:  # noqa: BLE001
        return True                                    # im Zweifel nichts vorwegnehmen


def antwort(satz: Any, offen: bool) -> Optional[str]:
    """Der feste Satz, oder ``None`` (dann der bisherige Weg)."""
    gefunden = art(satz)
    if gefunden is None:
        return None
    if gefunden == "nein":
        # Schliesst jede offene Rueckfrage (die Bestaetigungen verwerfen sie bei jedem anderen
        # Satz); immer ein fester Satz, nie der Chat.
        return NEIN_OFFEN if offen else NEIN_OHNE_FRAGE
    if gefunden in ("ja", "zustimmung") and offen:
        return None
    if gefunden == "ja":
        return JA_OHNE_FRAGE
    return _ANTWORT[gefunden]
