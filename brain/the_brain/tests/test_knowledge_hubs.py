from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

from core.knowledge import hubs, index
from core.knowledge.schema import Beleg, Dokument, Fakt
from core.knowledge.tresor import Tresor

T = datetime(2026, 9, 23, tzinfo=timezone.utc)


def dok(id, titel, links=()):
    return Dokument(typ="bubble", id=id, titel=titel, stand=T,
                    fakten=[Fakt(schluessel="s", wert="1", beleg=1)],
                    belege=[Beleg(nr=1, quelle="supabase", ziel="x", feld="s", wert="1", gemessen=T)],
                    links=list(links))


def _punkt(payload):
    return SimpleNamespace(payload=payload)


def test_hub_sortiert_nach_aktivierung_mit_wikilinks():
    docs = [dok("aaaaaa1", "Alpha"), dok("bbbbbb2", "Beta")]
    akt = {"doc::bubble::aaaaaa1": 0.4, "doc::bubble::bbbbbb2": 3.0}
    out = hubs.hub_notizen(docs, akt)
    text = out["Bubbles.md"]
    assert text.index("[[Beta (bbbbbb)]]") < text.index("[[Alpha (aaaaaa)]]")


def test_verknuepfen_ersetzt_nur_linked_ideas(tmp_path):
    """Fix-Runde 1, Finding 1: set_payload ersetzt in Qdrant nur TOP-LEVEL-
    Keys - ein blindes {"linked": {"ideas": [...]}} wuerde also linked.bubbles/
    thoughts/... loeschen, die _build_edges bereits geschrieben hat.
    verknuepfen muss deshalb read-merge-write machen: bestehenden linked-Dict
    lesen, NUR linked.ideas ersetzen, alles andere unangetastet lassen."""
    t = Tresor(tmp_path)
    t.schreiben(dok("aaaaaa1", "Alpha"))
    t.schreiben(dok("bbbbbb2", "Beta", links=["Alpha (aaaaaa)"]))
    kg = MagicMock()
    kg.client.retrieve.return_value = [_punkt({"linked": {"bubbles": ["X"], "ideas": ["stale"]}})]

    n = index.verknuepfen(kg, t)

    assert n == 1
    kw = kg.client.set_payload.call_args.kwargs
    assert kw["payload"]["linked"]["ideas"] == ["doc::bubble::aaaaaa1"]
    assert kw["payload"]["linked"]["bubbles"] == ["X"], "andere Kantenlisten bleiben unangetastet"


def test_verknuepfen_entfernter_wikilink_verschwindet_aus_linked_ideas(tmp_path):
    t = Tresor(tmp_path)
    t.schreiben(dok("aaaaaa1", "Alpha"))
    t.schreiben(dok("bbbbbb2", "Beta"))  # Wikilink zu Alpha wurde im Text entfernt
    kg = MagicMock()
    kg.client.retrieve.return_value = [_punkt({"linked": {"ideas": ["doc::bubble::aaaaaa1"]}})]

    index.verknuepfen(kg, t)

    kw = kg.client.set_payload.call_args.kwargs
    assert kw["payload"]["linked"]["ideas"] == [], \
        "entfernter Wikilink darf nicht mehr in linked.ideas stehen"


def test_verknuepfen_ueberspringt_fehlenden_punkt(tmp_path):
    t = Tresor(tmp_path)
    t.schreiben(dok("aaaaaa1", "Alpha"))
    t.schreiben(dok("bbbbbb2", "Beta", links=["Alpha (aaaaaa)"]))
    kg = MagicMock()
    kg.client.retrieve.return_value = []  # Punkt existiert noch nicht in Qdrant

    n = index.verknuepfen(kg, t)

    assert n == 0
    kg.client.set_payload.assert_not_called()


def test_schreiben_ruft_scroll_mit_erwarteten_keyword_namen(tmp_path):
    """Finding 2b: dieselben Keyword-Namen wie der Rest von core/ (siehe
    decision_self_prior.py/discourse_engine.py): collection_name, scroll_filter,
    limit, offset, with_payload, with_vectors."""
    t = Tresor(tmp_path)
    t.schreiben(dok("aaaaaa1", "Alpha"))
    kg = MagicMock()
    kg.client.scroll.return_value = ([], None)

    hubs.schreiben(t, kg)

    kw = kg.client.scroll.call_args.kwargs
    assert set(kw.keys()) == {"collection_name", "scroll_filter", "limit", "offset",
                              "with_payload", "with_vectors"}


def test_hubs_datei_ohne_yaml_kopf_wird_von_tresor_ignoriert(tmp_path):
    """Controller-Vorgabe: Hubs/*.md haben keinen YAML-Kopf und duerfen nicht
    als Wissensdokument auftauchen - 'Hubs' steht nicht in ORDNER, also
    ignorieren Tresor.alle()/bekannte_namen() den Ordner bereits von selbst.
    Diese Probe haelt das fest."""
    t = Tresor(tmp_path)
    t.schreiben(dok("aaaaaa1", "Alpha"))
    hub_ordner = tmp_path / "Hubs"
    hub_ordner.mkdir(parents=True, exist_ok=True)
    (hub_ordner / "Bubbles.md").write_text("# Bubbles\n\n- [[Alpha (aaaaaa)]]\n", encoding="utf-8")

    alle = t.alle()
    assert len(alle) == 1
    assert t.bekannte_namen() == {"Alpha (aaaaaa)"}


def test_schreiben_atomar_und_nur_bei_aenderung(tmp_path):
    """Controller-Vorgabe: wie der Tresor atomar schreiben (versteckte .tmp +
    os.replace) und nur, wenn sich der Inhalt aendert - sonst loest jeder
    15-Minuten-Volllauf Rowboats Datei-Watcher unnoetig aus."""
    t = Tresor(tmp_path)
    t.schreiben(dok("aaaaaa1", "Alpha"))
    kg = MagicMock()
    kg.client.scroll.return_value = ([], None)

    n1 = hubs.schreiben(t, kg)
    assert n1 == 1
    pfad = tmp_path / "Hubs" / "Bubbles.md"
    assert pfad.exists()
    assert not (tmp_path / "Hubs" / ".Bubbles.md.tmp").exists()
    mtime1 = pfad.stat().st_mtime_ns

    n2 = hubs.schreiben(t, kg)
    assert n2 == 0, "unveraenderter Inhalt darf nicht neu geschrieben werden"
    assert pfad.stat().st_mtime_ns == mtime1
