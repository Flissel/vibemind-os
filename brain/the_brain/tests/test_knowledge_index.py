from datetime import datetime, timezone
from unittest.mock import MagicMock

from core.knowledge import index
from core.knowledge.schema import Beleg, Dokument, Fakt
from core.knowledge.tresor import Tresor

T = datetime(2026, 9, 23, tzinfo=timezone.utc)


def dok(id="a1b2c3d4", titel="Marketing", links=None):
    return Dokument(typ="bubble", id=id, titel=titel, stand=T,
                    fakten=[Fakt(schluessel="status", wert="raw", beleg=1)],
                    belege=[Beleg(nr=1, quelle="supabase", ziel="x", feld="status",
                                  wert="raw", gemessen=T)], links=links or [])


def test_eintragen_schreibt_dokument_und_begriff():
    kg = MagicMock()
    kg._upsert_point.return_value = "pid"
    index.eintragen(kg, dok(links=["Andere (aaaaaa)"]))
    aufrufe = [c.kwargs for c in kg._upsert_point.call_args_list]
    assert aufrufe[0]["external_id"] == "doc::bubble::a1b2c3d4"
    assert aufrufe[0]["node_type"] == "knowledge_doc"
    assert aufrufe[0]["payload_extra"]["wikilinks"] == ["Andere (aaaaaa)"]
    assert aufrufe[0]["payload_extra"]["datei"] == "Bubbles/Marketing (a1b2c3).md"
    assert aufrufe[1]["external_id"] == "concept::bubble::a1b2c3d4"
    assert aufrufe[1]["node_type"] == "concept"


def test_neu_aufbauen_nur_aus_dateien(tmp_path):
    t = Tresor(tmp_path)
    t.schreiben(dok())
    t.schreiben(dok(id="ffffff11", titel="Sales"))
    kg = MagicMock()
    kg._upsert_point.return_value = "pid"
    stats = index.neu_aufbauen(kg, t)
    assert stats == {"dokumente": 2, "geschrieben": 2, "fehler": 0}
