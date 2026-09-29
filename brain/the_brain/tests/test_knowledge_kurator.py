from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

from core.knowledge.kurator import EREIGNIS_BEREICHE, Kurator
from core.knowledge.schema import Beleg, Dokument, Fakt
from core.knowledge.tresor import Tresor

T = datetime(2026, 9, 23, tzinfo=timezone.utc)


def dok(id="a1b2c3d4", status="raw"):
    return Dokument(typ="bubble", id=id, titel="Marketing", stand=T,
                    fakten=[Fakt(schluessel="status", wert=status, beleg=1)],
                    belege=[Beleg(nr=1, quelle="supabase", ziel="x", feld="status",
                                  wert=status, gemessen=T)])


def leser(**kw):
    basis = dict(bubbles=lambda j: [dok()], coding_projekte=lambda j: [],
                 agents=lambda j: [], pc_zustand=lambda j: None, user=lambda j: None,
                 FEHLER={})
    basis.update(kw)
    return SimpleNamespace(**basis)


def test_ereignis_fuehrt_nur_betroffene_bereiche_nach(tmp_path):
    aufgerufen = []
    k = Kurator(Tresor(tmp_path), leser=leser(
        bubbles=lambda j: aufgerufen.append("b") or [dok()],
        agents=lambda j: aufgerufen.append("a") or []))
    k.bei_ereignis("action_verified", {"capability": "bubble_create"})
    assert aufgerufen == ["b"]
    assert "bubble" in " ".join(EREIGNIS_BEREICHE["action_verified:bubble"])


def test_deutung_mit_gueltigem_beleg_wird_uebernommen(tmp_path):
    t = Tresor(tmp_path)
    k = Kurator(t, deuten=lambda d: "Die Bubble ist noch roh [B1].", leser=leser())
    k.voll_durchlauf()
    assert t.lesen_von(dok()).deutung == "Die Bubble ist noch roh [B1]."


def test_erfundene_deutung_wird_verworfen_fakten_bleiben(tmp_path):
    t = Tresor(tmp_path)
    k = Kurator(t, deuten=lambda d: "Sie wird ein Erfolg. Sehr gut [B9].", leser=leser())
    k.voll_durchlauf()
    gespeichert = t.lesen_von(dok())
    assert gespeichert is not None and gespeichert.deutung == ""
    assert k.stats["deutung_verworfen"] == 1


def test_deuten_fehler_bricht_nichts(tmp_path):
    def kaputt(d):
        raise RuntimeError("OpenFang weg")
    k = Kurator(Tresor(tmp_path), deuten=kaputt, leser=leser())
    k.voll_durchlauf()
    assert k.stats["geschrieben"] == 1


def test_index_wird_nachgezogen(tmp_path):
    kg = MagicMock()
    kg._upsert_point.return_value = "pid"
    Kurator(Tresor(tmp_path), kg=kg, leser=leser()).voll_durchlauf()
    assert kg._upsert_point.call_args_list[0].kwargs["external_id"] == "doc::bubble::a1b2c3d4"


def test_deuten_gespart_wenn_deutung_noch_gueltig_sonst_gerufen(tmp_path):
    """Controller-Ruling (Task 7): self.deuten(dok) wird nur gerufen, wenn
    self.deuten gesetzt ist UND die gespeicherte Deutung nicht mehr gueltig
    waere. Erster Durchlauf: kein gespeichertes Dokument -> deuten() IS
    called. Zweiter Durchlauf: dieselben Belege tragen die gespeicherte
    Deutung weiterhin -> deuten() wird NICHT erneut gerufen."""
    t = Tresor(tmp_path)
    aufrufe = []

    def zaehlend(d):
        aufrufe.append(1)
        return "Die Bubble ist noch roh [B1]."

    k = Kurator(t, deuten=zaehlend, leser=leser())
    k.voll_durchlauf()
    assert aufrufe == [1], "deuten() muss bei fehlendem Dokument gerufen werden"

    k.voll_durchlauf()
    assert aufrufe == [1], "deuten() darf bei noch gueltiger Deutung nicht erneut gerufen werden"


from core import brain_chat
from core import qdrant_kg


def test_leerlauf_ohne_ereignis_erzeugt_keinen_gedanken(monkeypatch):
    monkeypatch.setattr(brain_chat, "CTE_EVENT_ONLY", True)
    cte = brain_chat.ContinuousThinkingEngine()
    assert cte._think_tick() is None


def test_ereignis_ruft_den_kurator(monkeypatch):
    monkeypatch.setattr(brain_chat, "CTE_EVENT_ONLY", True)
    cte = brain_chat.ContinuousThinkingEngine()
    k = MagicMock()
    k.bei_ereignis.return_value = {"geschrieben": 1}
    cte.set_kurator(k)
    cte.record_event("action_verified", {"capability": "bubble_create", "intent": "x"})
    cte._think_tick()
    k.bei_ereignis.assert_called_once_with("action_verified", {"capability": "bubble_create", "intent": "x"})


def test_speicherschwelle_und_duplikate(monkeypatch):
    monkeypatch.setattr(qdrant_kg, "KG_THOUGHT_MIN_RELEVANCE", 0.3)
    kg = qdrant_kg.QdrantKG.__new__(qdrant_kg.QdrantKG)
    gespeichert = []
    kg.upsert_thought = lambda doc: gespeichert.append(doc)
    cb = kg.make_thought_callback()
    cb({"content": "Idle thought: What could I learn next?", "relevance": 0.1, "category": "explore"})
    cb({"content": "Plan X lief verifiziert durch.", "relevance": 0.7, "category": "event"})
    cb({"content": "Plan X lief verifiziert durch.", "relevance": 0.7, "category": "event"})
    assert len(gespeichert) == 2
    assert gespeichert[0].thought_id == gespeichert[1].thought_id  # gleiche ID -> Qdrant legt zusammen
