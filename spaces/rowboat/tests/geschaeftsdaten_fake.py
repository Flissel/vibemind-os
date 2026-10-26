"""FakeQuelle und Fabriken fuer die Geschaeftsdaten-Tests (Hilfsmodul, kein Test)."""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "geschaeftsdaten"))

from quelle import DbFehler, Ereignis, Kontakt, Medium, NewsletterInhalt, Versand  # noqa: E402

ID = "aaaaaaaa-0000-4000-8000-000000000001"


def kontakt(**ueberschreiben) -> Kontakt:
    werte = dict(
        id=ID, gesperrt=False, archiviert=False, name="Max Muster", company=None, title=None,
        status="new", consent_status="unknown", source="manuell", email_klein=None, art="person",
        bereiche=("bav",), created_at=datetime(2026, 1, 1, 9, 0, tzinfo=timezone.utc),
        updated_at=datetime(2026, 10, 20, 9, 0, tzinfo=timezone.utc))
    werte.update(ueberschreiben)
    return Kontakt(**werte)


def ereignis(**ueberschreiben) -> Ereignis:
    werte = dict(
        lead_id=ID, typ="nachricht_ausgehend", zeit=datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc),
        termin_uid=None, termin_datum=None, termin_uhrzeit=None, empfohlen_von_name=None,
        empfohlen_von_lead=None)
    werte.update(ueberschreiben)
    return Ereignis(**werte)


def inhalt(**ueberschreiben) -> NewsletterInhalt:
    werte = dict(
        id="bbbbbbbb-0000-4000-8000-000000000001", mandant="vibemind", titel="Oktober-Newsletter",
        format="felder", felder={"betreff": "Neues im Oktober", "abschnitte": [{"titel": "T", "text": "X"}]},
        bloecke=None, seite=None)
    werte.update(ueberschreiben)
    return NewsletterInhalt(**werte)


class FakeQuelle:
    """Quelle aus Listen. `kontakte`, `ereignisse`, `versand` sind je Schema ein dict von Listen;
    `ohne_sichten` sind Schemata ohne Sichten; `fehler=True` laesst jede Abfrage mit DbFehler scheitern."""

    def __init__(self, *, marken: dict[str, str] | None = None,
                 kontakte: dict[str, list[Kontakt]] | None = None,
                 ereignisse: dict[str, list[Ereignis]] | None = None,
                 versand: dict[str, list[Versand]] | None = None,
                 inhalte: list[NewsletterInhalt] | None = None,
                 medien: list[Medium] | None = None,
                 ohne_sichten: tuple[str, ...] = (), fehler: bool = False) -> None:
        self.marken_ = dict(marken or {"vibemind": "VibeMind"})
        self.kontakte_ = {s: list(v) for s, v in (kontakte or {}).items()}
        self.ereignisse_ = {s: list(v) for s, v in (ereignisse or {}).items()}
        self.versand_ = {s: list(v) for s, v in (versand or {}).items()}
        self.inhalte_ = list(inhalte or [])
        self.medien_ = list(medien or [])
        self.ohne_sichten = set(ohne_sichten)
        self.fehler = fehler
        self.aufrufe: list[str] = []

    def _ruf(self, name: str) -> None:
        self.aufrufe.append(name)
        if self.fehler:
            raise DbFehler("psql Exit 2")

    def marken(self) -> dict[str, str]:
        self._ruf("marken")
        return dict(self.marken_)

    def hat_sichten(self, schema: str) -> bool:
        self._ruf("hat_sichten")
        return schema not in self.ohne_sichten

    def kontakte(self, schema: str) -> list[Kontakt]:
        self._ruf("kontakte")
        return list(self.kontakte_.get(schema, []))

    def ereignisse(self, schema: str) -> list[Ereignis]:
        self._ruf("ereignisse")
        return list(self.ereignisse_.get(schema, []))

    def versand(self, schema: str) -> list[Versand]:
        self._ruf("versand")
        return list(self.versand_.get(schema, []))

    def newsletter_inhalte(self, ids: list[str]) -> list[NewsletterInhalt]:
        self._ruf("newsletter_inhalte")
        gesucht = set(ids)
        return [i for i in self.inhalte_ if i.id in gesucht]

    def medien(self, von: datetime, bis: datetime) -> list[Medium]:
        self._ruf("medien")
        return [m for m in self.medien_ if von <= m.fertig_am < bis]

