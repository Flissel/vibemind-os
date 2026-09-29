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
