import json
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

from core.knowledge import kurator as kurator_modul
from core.knowledge.kurator import EREIGNIS_BEREICHE, Kurator, ereignis_melden
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


def test_voll_durchlauf_ruft_verknuepfen_und_hubs_ereignisse_abarbeiten_nicht(tmp_path, monkeypatch):
    """Fix-Runde 1, Finding 2a: 'verknuepfen + hubs' gehoert NUR in den
    Volllauf (Brief Step 6), nicht in ereignisse_abarbeiten - sonst wuerden
    Wikilinks/Hub-Notizen bei jedem einzelnen Cross-Prozess-Ereignis neu
    geschrieben statt nur beim periodischen Volllauf."""
    from core.knowledge import hubs as hubs_modul
    from core.knowledge import index as index_modul

    aufrufe = {"verknuepfen": 0, "hubs": 0}

    def fake_verknuepfen(kg, tresor):
        aufrufe["verknuepfen"] += 1
        return 0

    def fake_hubs(tresor, kg):
        aufrufe["hubs"] += 1
        return 0

    monkeypatch.setattr(index_modul, "verknuepfen", fake_verknuepfen)
    monkeypatch.setattr(hubs_modul, "schreiben", fake_hubs)

    kg = MagicMock()
    k = Kurator(Tresor(tmp_path), kg=kg, leser=leser())

    k.voll_durchlauf()
    assert aufrufe == {"verknuepfen": 1, "hubs": 1}

    pfad = tmp_path / "ereignisse.jsonl"
    offset_pfad = tmp_path / "ereignisse.jsonl.offset"
    pfad.write_text(json.dumps({"kind": "action_verified", "capability": "bubble_create"}) + "\n",
                    encoding="utf-8")
    k.ereignisse_abarbeiten(str(pfad), str(offset_pfad))
    assert aufrufe == {"verknuepfen": 1, "hubs": 1}, \
        "ereignisse_abarbeiten darf verknuepfen/hubs NICHT aufrufen"


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


def test_ereignis_melden_schreibt_nur_gemappte_ereignisse_mit_wenigen_feldern(tmp_path, monkeypatch):
    """Fix-Runde 1, Finding 1: ereignis_melden() ist der Cross-Prozess-Weg -
    schreibt nur, wenn die Datei konfiguriert ist UND das Ereignis einen
    Bereich trifft, und nur kind/capability/ts (Datenschutz: keine Intents)."""
    pfad = tmp_path / "ereignisse.jsonl"
    monkeypatch.setattr(kurator_modul, "KURATOR_EREIGNIS_DATEI", str(pfad))

    ereignis_melden("action_verified", {"capability": "bubble_create", "intent": "geheimer Text"})
    ereignis_melden("self_steer_dispatch", {"capability": "irgendwas"})  # nicht gemappt

    zeilen = pfad.read_text(encoding="utf-8").strip().splitlines()
    assert len(zeilen) == 1
    eintrag = json.loads(zeilen[0])
    assert set(eintrag.keys()) == {"kind", "capability", "ts"}
    assert eintrag["kind"] == "action_verified"
    assert eintrag["capability"] == "bubble_create"


def test_ereignis_melden_ohne_pfad_und_ohne_bereich_tut_nichts(tmp_path, monkeypatch):
    pfad = tmp_path / "ereignisse.jsonl"
    monkeypatch.setattr(kurator_modul, "KURATOR_EREIGNIS_DATEI", "")
    ereignis_melden("action_verified", {"capability": "bubble_create"})
    assert not pfad.exists()

    monkeypatch.setattr(kurator_modul, "KURATOR_EREIGNIS_DATEI", str(pfad))
    ereignis_melden("self_steer_dispatch", {"capability": "x"})  # kein Bereich
    assert not pfad.exists()


def test_ereignisse_abarbeiten_vereint_zwei_ereignisse_in_einem_lauf(tmp_path):
    """Fix-Runde 1, Finding 2: zwei Ereignisse desselben Bereichs duerfen nur
    EINEN _lauf-Aufruf ausloesen, nicht einen je Ereignis."""
    pfad = tmp_path / "ereignisse.jsonl"
    offset_pfad = tmp_path / "ereignisse.jsonl.offset"
    pfad.write_text(
        json.dumps({"kind": "action_verified", "capability": "bubble_create", "ts": "t1"}) + "\n"
        + json.dumps({"kind": "action_verified", "capability": "bubble_edit", "ts": "t2"}) + "\n",
        encoding="utf-8")
    aufrufe = []
    k = Kurator(Tresor(tmp_path / "tresor"), leser=leser(
        bubbles=lambda j: aufrufe.append("b") or [dok()]))

    erg = k.ereignisse_abarbeiten(str(pfad), str(offset_pfad))

    assert aufrufe == ["b"], "beide Ereignisse bilden auf 'bubbles' ab -> EIN _lauf-Aufruf"
    assert erg["ereignisse"] == 2
    assert erg["geschrieben"] == 1
    assert offset_pfad.read_text(encoding="utf-8").strip() == str(pfad.stat().st_size)


def test_ereignisse_abarbeiten_zweiter_lauf_ohne_neue_zeilen_tut_nichts(tmp_path):
    pfad = tmp_path / "ereignisse.jsonl"
    offset_pfad = tmp_path / "ereignisse.jsonl.offset"
    pfad.write_text(json.dumps({"kind": "plan_completed", "capability": ""}) + "\n", encoding="utf-8")
    aufrufe = []
    k = Kurator(Tresor(tmp_path / "tresor"), leser=leser(
        agents=lambda j: aufrufe.append("a") or [],
        pc_zustand=lambda j: aufrufe.append("p") or None))

    erg1 = k.ereignisse_abarbeiten(str(pfad), str(offset_pfad))
    assert erg1["ereignisse"] == 1
    assert aufrufe, "erster Lauf muss die betroffenen Leser rufen"

    aufrufe.clear()
    erg2 = k.ereignisse_abarbeiten(str(pfad), str(offset_pfad))
    assert erg2["ereignisse"] == 0
    assert aufrufe == [], "keine neuen Zeilen -> kein _lauf-Aufruf"


def test_ereignisse_abarbeiten_restart_bei_verkuerzter_datei(tmp_path):
    pfad = tmp_path / "ereignisse.jsonl"
    offset_pfad = tmp_path / "ereignisse.jsonl.offset"
    zeile = json.dumps({"kind": "action_verified", "capability": "bubble_create"}) + "\n"
    pfad.write_text(zeile * 5, encoding="utf-8")
    aufrufe = []
    k = Kurator(Tresor(tmp_path / "tresor"), leser=leser(
        bubbles=lambda j: aufrufe.append("b") or [dok()]))
    k.ereignisse_abarbeiten(str(pfad), str(offset_pfad))
    assert aufrufe == ["b"]

    # Datei "rotiert": jetzt kuerzer als der gespeicherte Offset.
    pfad.write_text(zeile, encoding="utf-8")
    aufrufe.clear()
    erg = k.ereignisse_abarbeiten(str(pfad), str(offset_pfad))
    assert aufrufe == ["b"], "muss bei 0 neu beginnen statt die Zeile zu ueberspringen"
    assert erg["ereignisse"] == 1


def test_ereignisse_abarbeiten_ueberspringt_kaputte_zeile(tmp_path):
    pfad = tmp_path / "ereignisse.jsonl"
    offset_pfad = tmp_path / "ereignisse.jsonl.offset"
    pfad.write_text(
        "{kaputtes json ohne Ende\n"
        + json.dumps({"kind": "action_verified", "capability": "bubble_create"}) + "\n",
        encoding="utf-8")
    aufrufe = []
    k = Kurator(Tresor(tmp_path / "tresor"), leser=leser(
        bubbles=lambda j: aufrufe.append("b") or [dok()]))

    erg = k.ereignisse_abarbeiten(str(pfad), str(offset_pfad))

    assert aufrufe == ["b"]
    assert erg["ereignisse"] == 1, "nur die gueltige Zeile zaehlt, die kaputte wird uebersprungen"


def test_record_event_meldet_kurator_cross_prozess(tmp_path, monkeypatch):
    """Fix-Runde 1, Finding 1: record_event() meldet zusaetzlich an die
    Ereignisdatei, unabhaengig davon, ob dieser CTE-Prozess je tickt."""
    pfad = tmp_path / "ereignisse.jsonl"
    monkeypatch.setattr(kurator_modul, "KURATOR_EREIGNIS_DATEI", str(pfad))
    cte = brain_chat.ContinuousThinkingEngine()

    cte.record_event("action_verified", {"capability": "bubble_create", "intent": "geheim"})

    zeilen = pfad.read_text(encoding="utf-8").strip().splitlines()
    assert len(zeilen) == 1
    eintrag = json.loads(zeilen[0])
    assert eintrag["kind"] == "action_verified"
    assert eintrag["capability"] == "bubble_create"
    assert set(eintrag.keys()) == {"kind", "capability", "ts"}


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


# ── Schlusspruefung I1/I3/I4/I6/M2 ──

def _viele(n):
    def lies(j):
        return [Dokument(typ="bubble", id=f"{i:08d}", titel=f"Bubble {i}", stand=j,
                         fakten=[Fakt(schluessel="status", wert="raw", beleg=1)],
                         belege=[Beleg(nr=1, quelle="supabase", ziel=f"ideas?id=eq.{i}",
                                       feld="status", wert="raw", gemessen=j)])
                for i in range(n)]
    return lies


def test_zweiter_volllauf_ohne_aenderung_schreibt_nichts(tmp_path, monkeypatch):
    import os
    t = Tresor(tmp_path)
    k = Kurator(t, leser=leser(bubbles=_viele(200)))
    k.voll_durchlauf()
    assert k.stats["geschrieben"] == 200

    aufrufe = {"bekannte": 0, "replace": 0}
    echte_namen = Tresor.bekannte_namen
    echter_replace = os.replace

    def namen_zaehlend(self):
        aufrufe["bekannte"] += 1
        return echte_namen(self)

    def replace_zaehlend(src, dst):
        aufrufe["replace"] += 1
        return echter_replace(src, dst)

    monkeypatch.setattr(Tresor, "bekannte_namen", namen_zaehlend)
    monkeypatch.setattr(os, "replace", replace_zaehlend)
    erg = k.voll_durchlauf()
    assert aufrufe == {"bekannte": 1, "replace": 0}
    assert erg["unveraendert"] == 200
    assert erg["geschrieben"] == 0


def test_unveraendertes_dokument_wird_nicht_neu_indexiert(tmp_path, monkeypatch):
    from core.knowledge import hubs as hubs_modul
    from core.knowledge import index as index_modul
    monkeypatch.setattr(index_modul, "verknuepfen", lambda kg, t: 0)
    monkeypatch.setattr(hubs_modul, "schreiben", lambda t, kg: 0)
    kg = MagicMock()
    kg._upsert_point.return_value = "pid"
    k = Kurator(Tresor(tmp_path), kg=kg, leser=leser())
    k.voll_durchlauf()
    n = kg._upsert_point.call_count
    assert n > 0
    k.voll_durchlauf()
    assert kg._upsert_point.call_count == n


def test_unlesbare_datei_zaehlt_unlesbar(tmp_path):
    t = Tresor(tmp_path)
    p = t.pfad(dok())
    p.parent.mkdir(parents=True)
    p.write_text("---\n: [kaputt\n---\n", encoding="utf-8")
    k = Kurator(t, leser=leser())
    erg = k.voll_durchlauf()
    assert erg["unlesbar"] == 1 and erg["geschrieben"] == 0
    assert p.read_text(encoding="utf-8") == "---\n: [kaputt\n---\n"


# ── Schlusspruefung I4: ein unerwarteter Fehler stoppt nichts dauerhaft ──

def test_leser_wirft_valueerror_andere_laufen_offset_vorgerueckt(tmp_path):
    pfad = tmp_path / "ereignisse.jsonl"
    offset_pfad = tmp_path / "ereignisse.jsonl.offset"
    pfad.write_text(json.dumps({"kind": "plan_completed", "capability": ""}) + "\n", encoding="utf-8")
    aufrufe = []

    def kaputt(j):
        raise ValueError("unerwartet")

    k = Kurator(Tresor(tmp_path / "tresor"), leser=leser(
        agents=kaputt, pc_zustand=lambda j: aufrufe.append("p") or None))
    erg = k.ereignisse_abarbeiten(str(pfad), str(offset_pfad))
    assert aufrufe == ["p"], "pc_zustand muss trotz werfendem agents-Leser laufen"
    assert erg["quellenfehler"] == 1
    assert offset_pfad.read_text(encoding="utf-8").strip() == str(pfad.stat().st_size)


def test_dokument_fehler_bricht_den_lauf_nicht_ab(tmp_path):
    t = Tresor(tmp_path)
    echt = t.schreiben

    def schreiben(neu, **kw):
        if neu.id == "00000000":
            raise KeyError("kaputt")
        return echt(neu, **kw)

    t.schreiben = schreiben
    k = Kurator(t, leser=leser(bubbles=_viele(3)))
    erg = k.voll_durchlauf()
    assert erg["abgelehnt"] == 1 and erg["geschrieben"] == 2


def test_halbe_letzte_zeile_wird_beim_naechsten_takt_komplett_gelesen(tmp_path):
    pfad = tmp_path / "ereignisse.jsonl"
    offset_pfad = tmp_path / "ereignisse.jsonl.offset"
    erste = json.dumps({"kind": "action_verified", "capability": "bubble_create"}) + "\n"
    zweite = json.dumps({"kind": "plan_completed", "capability": ""}) + "\n"
    pfad.write_bytes((erste + zweite[:10]).encode("utf-8"))
    aufrufe = []
    k = Kurator(Tresor(tmp_path / "tresor"), leser=leser(
        bubbles=lambda j: aufrufe.append("b") or [],
        agents=lambda j: aufrufe.append("a") or [],
        pc_zustand=lambda j: aufrufe.append("p") or None))
    erg = k.ereignisse_abarbeiten(str(pfad), str(offset_pfad))
    assert erg["ereignisse"] == 1 and aufrufe == ["b"]
    assert offset_pfad.read_text(encoding="utf-8").strip() == str(len(erste.encode("utf-8")))

    with open(pfad, "ab") as f:
        f.write(zweite[10:].encode("utf-8"))
    aufrufe.clear()
    erg = k.ereignisse_abarbeiten(str(pfad), str(offset_pfad))
    assert erg["ereignisse"] == 1
    assert sorted(aufrufe) == ["a", "p"]


def test_takt_schleife_volllauf_fehler_wiederholt_erst_nach_intervall():
    uhr = iter([1000.0, 1030.0, 1060.0, 1900.0])
    volllaeufe, ereignislaeufe = [], []

    class FakeKurator:
        def ereignisse_abarbeiten(self, pfad, offset_pfad):
            ereignislaeufe.append(pfad)
            raise OSError("Ereignis kaputt")

        def voll_durchlauf(self):
            volllaeufe.append(1)
            raise ValueError("Volllauf kaputt")

    kurator_modul.takt_schleife(FakeKurator(), ereignis_datei="e.jsonl", intervall_s=900.0,
                                takt_s=30.0, zeit=lambda: next(uhr), schlafen=lambda s: None,
                                runden=4)
    assert len(ereignislaeufe) == 4, "Ereignis-Fehler darf den Takt nicht stoppen"
    assert volllaeufe == [1, 1], "Volllauf bei t=1000 (Fehler) und erst wieder bei t=1900"


# ── Schlusspruefung I6: Dokumente geloeschter Quellen wandern nach Veraltet/ ──

def test_verschwundene_bubble_wandert_nach_veraltet(tmp_path):
    t = Tresor(tmp_path)
    liefern = {"n": 3}
    k = Kurator(t, leser=leser(bubbles=lambda j: _viele(liefern["n"])(j)))
    k.voll_durchlauf()
    (tmp_path / "Bubbles" / "freie Notiz.md").write_text("# ohne Kopf\n", encoding="utf-8")
    liefern["n"] = 2
    erg = k.voll_durchlauf()
    assert erg["veraltet"] == 1
    assert (tmp_path / "Bubbles" / "Veraltet" / "Bubble 2 (000000).md").exists()
    assert not (tmp_path / "Bubbles" / "Bubble 2 (000000).md").exists()
    assert (tmp_path / "Bubbles" / "freie Notiz.md").exists(), "freie Notiz bleibt liegen"
    assert sorted(d.id for d in t.alle()) == ["00000000", "00000001"]


def test_leser_wirft_nichts_wird_verschoben(tmp_path):
    t = Tresor(tmp_path)
    Kurator(t, leser=leser(bubbles=_viele(2))).voll_durchlauf()

    def kaputt(j):
        raise RuntimeError("Supabase weg")

    erg = Kurator(t, leser=leser(bubbles=kaputt)).voll_durchlauf()
    assert erg["veraltet"] == 0
    assert not (tmp_path / "Bubbles" / "Veraltet").exists()
    assert len(t.alle()) == 2


def test_pc_zustand_und_user_werden_nie_veraltet(tmp_path):
    t = Tresor(tmp_path)
    pc = Dokument(typ="pc_zustand", id="system", titel="PC-Zustand", stand=T,
                  fakten=[Fakt(schluessel="x", wert="1", beleg=1)],
                  belege=[Beleg(nr=1, quelle="http", ziel="http://a", feld="#status",
                                wert="1", gemessen=T)])
    Kurator(t, leser=leser(pc_zustand=lambda j: pc)).voll_durchlauf()
    erg = Kurator(t, leser=leser(pc_zustand=lambda j: None)).voll_durchlauf()
    assert erg["veraltet"] == 0
    assert (tmp_path / "System" / "PC-Zustand (system).md").exists()


# ── Schlusspruefung M2: Index-Fehler werden gezaehlt und laut geloggt ──

def test_index_fehler_gezaehlt_und_warning(tmp_path, monkeypatch, caplog):
    import logging
    from core.knowledge import hubs as hubs_modul
    from core.knowledge import index as index_modul
    monkeypatch.setattr(index_modul, "verknuepfen", lambda kg, t: 0)
    monkeypatch.setattr(hubs_modul, "schreiben", lambda t, kg: 0)
    kg = MagicMock()
    kg._upsert_point.side_effect = RuntimeError("Qdrant weg")
    k = Kurator(Tresor(tmp_path), kg=kg, leser=leser(bubbles=_viele(2)))
    with caplog.at_level(logging.WARNING, logger="core.knowledge.kurator"):
        erg = k.voll_durchlauf()
    assert erg["index_fehler"] == 2 and erg["geschrieben"] == 2
    assert any("Index" in r.getMessage() and r.levelno == logging.WARNING for r in caplog.records)


def test_index_ohne_punkt_id_zaehlt_als_fehler(tmp_path, monkeypatch):
    from core.knowledge import hubs as hubs_modul
    from core.knowledge import index as index_modul
    monkeypatch.setattr(index_modul, "verknuepfen", lambda kg, t: 0)
    monkeypatch.setattr(hubs_modul, "schreiben", lambda t, kg: 0)
    kg = MagicMock()
    kg._upsert_point.return_value = None  # _upsert_point schluckt Fehler und gibt None
    erg = Kurator(Tresor(tmp_path), kg=kg, leser=leser()).voll_durchlauf()
    assert erg["index_fehler"] == 1
