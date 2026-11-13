"""Rechtetabelle der Fachschicht (erstes Tor; RLS ist das zweite)."""
from __future__ import annotations

from uuid import UUID

from spaces.event_management.system.kontext import KeinRecht, Kontext, NichtGefunden

AKTIONEN = frozenset({
    "firma_anlegen", "lesen", "event_anlegen", "event_aendern", "event_loeschen",
    "event_absagen", "baustein_schreiben", "einladen", "rolle_aendern", "mitglied_entfernen",
    "nachricht_freigeben",  # Mail-Entwurf zum Versand freigeben: nur Leitung (und Betreiber)
    "gruppe_verbinden",  # Telegram-Gruppe per Code verbinden und bestaetigen: nur Leitung
})
_ROLLEN: dict[str, frozenset[str]] = {
    "leitung": AKTIONEN - {"firma_anlegen"},
    "mitglied": frozenset({"lesen", "baustein_schreiben", "event_aendern"}),
}


def pruefe(kontext: Kontext, aktion: str, firma_id: UUID | None = None) -> None:
    """Wirft ValueError (unbekannte Aktion), KeinRecht oder NichtGefunden (fremde Firma)."""
    if aktion not in AKTIONEN:
        raise ValueError("Unbekannte Aktion")
    if kontext.betreiber:
        return
    if aktion == "firma_anlegen":
        raise KeinRecht(f"Keine Berechtigung fuer {aktion}")
    if firma_id is None or firma_id not in kontext.rollen:
        raise NichtGefunden("Firma nicht gefunden")
    if aktion not in _ROLLEN.get(kontext.rollen[firma_id], frozenset()):
        raise KeinRecht(f"Keine Berechtigung fuer {aktion}")
