"""Fix-Runde 1, Finding 3: Archiv-Collections nicht im Default-Recall.

`COLLECTIONS["episodic_archive"]`/`["semantic_archive"]` (Task 6) sind
Altbestaende, die verschoben, nicht geloescht werden. `QdrantKG.search()`
ohne explizite `collection=`/`node_type=` sowie `mcmp_gardener._locate()`
duerfen sie nicht mit durchsuchen; ueber ein explizites
`collection="episodic_archive"` bleiben sie weiterhin erreichbar.
"""
from __future__ import annotations

import threading
from unittest.mock import MagicMock

from core import qdrant_kg as kgmod
from core.mcmp_gardener import MCMPGardener


def _make_kg():
    kg = kgmod.QdrantKG.__new__(kgmod.QdrantKG)  # __init__ umgehen, kein echtes Qdrant
    kg._qm = __import__("qdrant_client.http.models", fromlist=["models"])
    kg.client = MagicMock()
    kg.client.query_points.return_value = MagicMock(points=[])
    kg._embedder = MagicMock()  # _embedder_ready() gibt ihn direkt zurueck, kein HTTP
    kg._embed_lock = threading.Lock()
    return kg


def test_archive_collections_sind_logische_namen_aus_collections():
    assert kgmod.ARCHIVE_COLLECTIONS == {"episodic_archive", "semantic_archive"}
    for logisch in kgmod.ARCHIVE_COLLECTIONS:
        assert logisch in kgmod.COLLECTIONS


def test_search_default_ueberspringt_archiv_collections():
    kg = _make_kg()
    kg.search("irgendwas")
    aufgerufen = {c.kwargs["collection_name"] for c in kg.client.query_points.call_args_list}
    archiv_namen = {kgmod.COLLECTIONS[c] for c in kgmod.ARCHIVE_COLLECTIONS}
    assert not (aufgerufen & archiv_namen)
    erwartet = {v for k, v in kgmod.COLLECTIONS.items() if k not in kgmod.ARCHIVE_COLLECTIONS}
    assert aufgerufen == erwartet


def test_search_explizite_archiv_collection_funktioniert_weiterhin():
    kg = _make_kg()
    kg.search("irgendwas", collection="episodic_archive")
    aufgerufen = {c.kwargs["collection_name"] for c in kg.client.query_points.call_args_list}
    assert aufgerufen == {kgmod.COLLECTIONS["episodic_archive"]}


def test_search_andere_logische_collection_bleibt_unveraendert():
    kg = _make_kg()
    kg.search("irgendwas", collection="semantic")
    aufgerufen = {c.kwargs["collection_name"] for c in kg.client.query_points.call_args_list}
    assert aufgerufen == {kgmod.COLLECTIONS["semantic"]}


def test_gardener_locate_ueberspringt_archiv_collections():
    kg = MagicMock()
    kg.client.retrieve.return_value = []  # ueberall "nicht gefunden"
    g = MCMPGardener(kg)
    g._locate("irgendeine-referenz")
    aufgerufen = {c.kwargs["collection_name"] for c in kg.client.retrieve.call_args_list}
    archiv_namen = {kgmod.COLLECTIONS[c] for c in kgmod.ARCHIVE_COLLECTIONS}
    assert not (aufgerufen & archiv_namen)
    # alle nicht-archivierten Collections wurden weiterhin durchsucht
    erwartet = {v for k, v in kgmod.COLLECTIONS.items() if k not in kgmod.ARCHIVE_COLLECTIONS}
    assert erwartet <= aufgerufen


# ── Schlusspruefung M1: keine Kanten in/aus dem Archiv ──

def test_build_edges_ueberspringt_archiv_collections():
    kg = _make_kg()
    kg._build_edges("pid-1", "text", kgmod.NT_KNOWLEDGE_DOC, vector=[0.1, 0.2])
    aufgerufen = {c.kwargs["collection_name"] for c in kg.client.query_points.call_args_list}
    archiv_namen = {kgmod.COLLECTIONS[c] for c in kgmod.ARCHIVE_COLLECTIONS}
    assert aufgerufen, "nicht-archivierte Collections werden weiter durchsucht"
    assert not (aufgerufen & archiv_namen)


# ── Schlusspruefung I2: Aktivierung ueberlebt das Neu-Indexieren ──

def _rec(payload):
    r = MagicMock()
    r.payload = payload
    return r


def test_upsert_behaelt_activation_strength_und_edge_weights():
    kg = _make_kg()
    kg._embedder.encode.return_value = [0.1, 0.2]
    kg.client.retrieve.return_value = [_rec({
        "linked": {"ideas": ["x"]}, "activation_strength": 3.0, "edge_weights": {"x": 0.5}})]
    kg._upsert_point(external_id="doc::bubble::a", node_type=kgmod.NT_KNOWLEDGE_DOC,
                     text="t", payload_extra={"titel": "A"})
    payload = kg.client.upsert.call_args.kwargs["points"][0].payload
    assert payload["activation_strength"] == 3.0
    assert payload["edge_weights"] == {"x": 0.5}
    assert payload["linked"] == {"ideas": ["x"]}


def test_upsert_explizite_activation_strength_gewinnt():
    kg = _make_kg()
    kg._embedder.encode.return_value = [0.1, 0.2]
    kg.client.retrieve.return_value = [_rec({"activation_strength": 3.0})]
    kg._upsert_point(external_id="doc::bubble::a", node_type=kgmod.NT_KNOWLEDGE_DOC,
                     text="t", payload_extra={"activation_strength": 0.0})
    payload = kg.client.upsert.call_args.kwargs["points"][0].payload
    assert payload["activation_strength"] == 0.0


def test_upsert_neuer_punkt_ohne_aktivierung():
    kg = _make_kg()
    kg._embedder.encode.return_value = [0.1, 0.2]
    kg.client.retrieve.return_value = []
    kg._upsert_point(external_id="doc::bubble::a", node_type=kgmod.NT_KNOWLEDGE_DOC,
                     text="t", payload_extra={})
    payload = kg.client.upsert.call_args.kwargs["points"][0].payload
    assert "activation_strength" not in payload and "edge_weights" not in payload


# ── Schlusspruefung I2: Gardener-Seeds streuen (zufaelliger Offset) ──

def test_gardener_seeds_scrollen_ab_zufaelligem_offset():
    kg = MagicMock()
    rec = MagicMock()
    rec.id = "p1"
    rec.payload = {}
    kg.client.scroll.return_value = ([rec], None)
    g = MCMPGardener(kg)
    g._sample_seeds(1)
    g._sample_seeds(1)
    offsets = [c.kwargs.get("offset") for c in kg.client.scroll.call_args_list]
    assert len(offsets) == 2
    assert all(o for o in offsets), "jeder Scroll braucht einen Offset"
    assert offsets[0] != offsets[1]


def test_gardener_seeds_leer_ab_offset_dann_von_vorne():
    kg = MagicMock()
    rec = MagicMock()
    rec.id = "p1"
    rec.payload = {}
    kg.client.scroll.side_effect = lambda **kw: ([], None) if kw.get("offset") else ([rec], None)
    g = MCMPGardener(kg)
    seeds = g._sample_seeds(1)
    assert [s[1] for s in seeds] == ["p1"]
    offsets = [c.kwargs.get("offset") for c in kg.client.scroll.call_args_list]
    assert offsets[0] and offsets[1] is None
