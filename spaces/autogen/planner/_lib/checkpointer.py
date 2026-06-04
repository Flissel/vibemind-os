"""Phase D — durable Checkpointer für den SoM-LangGraph (som_graph).

LangGraph liefert nur einen In-Memory `MemorySaver` (überlebt keinen Prozess-Tod)
und keinen file-Saver. Wir brauchen aber genau das: ein pausierter Run (interrupt
bei needs_input) muss einen Prozess-Neustart überleben — der State liegt ohnehin
pro Run in state/runs/<run_id>/.

`RunDirCheckpointer` ist eine dünne MemorySaver-Subklasse: nach jedem `put`/
`put_writes` serialisiert sie den kompletten In-Memory-Store nach
state/runs/<run_id>/langgraph_checkpoint.json und lädt ihn beim ersten Zugriff
zurück. thread_id == run_id (so landet jeder Run-Checkpoint in seinem Verzeichnis).

Bewusst KEINE eigene BaseCheckpointSaver-Implementierung: MemorySaver kennt das
korrekte Checkpoint-/Writes-Format schon; wir hängen nur Persistenz darunter.
Eigenständig importierbar (nur langgraph + state.py).

SICHERHEIT (pickle): Die Checkpoint-Blobs enthalten nicht-JSON-fähige LangGraph-
Objekte (Pregel-Channel-State, Versions-Maps), daher pickle statt JSON für den
Inhalt. Das ist hier UNBEDENKLICH: gelesen wird AUSSCHLIESSLICH die Datei
state/runs/<run_id>/langgraph_checkpoint.json, die NUR dieser SoM-Worker selbst
lokal geschrieben hat — keine externe/untrusted Quelle, kein Netzwerk, keine
User-Eingabe. Ein korrupter/fehlender Checkpoint wird wie "kein Checkpoint"
behandelt (try/except → Run rechnet von vorn, kein Crash).
"""

from __future__ import annotations

import json
import pickle
from base64 import b64decode, b64encode
from pathlib import Path
from typing import Any

from langgraph.checkpoint.memory import MemorySaver


class RunDirCheckpointer(MemorySaver):
    """MemorySaver, der seinen Store je run_id (=thread_id) in
    state/runs/<run_id>/langgraph_checkpoint.json persistiert.

    run_dir_fn: callable(run_id) -> Path (i.d.R. state.run_dir), damit der
    Checkpointer nicht selbst von der State-Schicht abhängt (testbar/injizierbar).
    """

    _FILE = "langgraph_checkpoint.json"

    def __init__(self, run_dir_fn) -> None:
        super().__init__()
        self._run_dir_fn = run_dir_fn
        self._loaded: set[str] = set()
        import threading
        self._persist_lock = threading.Lock()

    # ── Persistenz ───────────────────────────────────────────────────────────
    def _path(self, run_id: str) -> Path:
        return Path(self._run_dir_fn(run_id)) / self._FILE

    @staticmethod
    def _thread_id(config: dict) -> str | None:
        return (config or {}).get("configurable", {}).get("thread_id")

    def _ensure_loaded(self, run_id: str) -> None:
        """Lädt den Checkpoint dieses Runs einmalig von Platte in den
        In-Memory-Store des MemorySaver (für Resume nach Prozess-Neustart)."""
        if not run_id or run_id in self._loaded:
            return
        self._loaded.add(run_id)
        p = self._path(run_id)
        if not p.exists():
            return
        try:
            blob = json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 — korrupter Checkpoint → wie kein Checkpoint
            return
        # MemorySaver hält seinen State in .storage / .writes / .blobs (dict).
        # Wir haben sie beim Speichern via pickle+b64 serialisiert (Checkpoints
        # enthalten nicht-JSON-Objekte). Zurückspielen:
        for attr in ("storage", "writes", "blobs"):
            raw = blob.get(attr)
            if raw is not None and hasattr(self, attr):
                try:
                    restored = pickle.loads(b64decode(raw.encode("ascii")))
                    getattr(self, attr).update(restored)
                except Exception:  # noqa: BLE001
                    pass

    def _persist(self, run_id: str) -> None:
        if not run_id:
            return
        payload: dict[str, Any] = {}
        for attr in ("storage", "writes", "blobs"):
            store = getattr(self, attr, None)
            if store is not None:
                # nur den Teil dieses threads sichern wäre genauer, ist aber
                # fummelig (verschachtelte dicts je thread_id). Der Store ist
                # klein (1 Run im Worker-Prozess) → kompletten dump reicht.
                try:
                    payload[attr] = b64encode(pickle.dumps(dict(store))).decode("ascii")
                except Exception:  # noqa: BLE001
                    pass
        # Serialisierung (pickle/json) NICHT unter dem Lock — das hielt auf
        # Windows den _persist_lock über teure Arbeit, während ein anderer
        # pregel-Worker-Thread denselben Run schrieb → Deadlock. Erst payload
        # bauen (oben, lock-frei), dann NUR den kurzen Datei-Swap serialisieren.
        blob = json.dumps(payload)
        p = self._path(run_id)
        with self._persist_lock:
            tmp = p.with_suffix(f".{id(blob)}.tmp")
            try:
                tmp.write_text(blob, encoding="utf-8")
                tmp.replace(p)
            finally:
                if tmp.exists():
                    try:
                        tmp.unlink()
                    except OSError:
                        pass

    # ── MemorySaver-API mit Load-before / Persist-after umhüllen ──────────────
    def get_tuple(self, config):  # noqa: ANN001
        self._ensure_loaded(self._thread_id(config))
        return super().get_tuple(config)

    def list(self, config, **kwargs):  # noqa: ANN001
        self._ensure_loaded(self._thread_id(config))
        return super().list(config, **kwargs)

    def put(self, config, checkpoint, metadata, new_versions):  # noqa: ANN001
        rid = self._thread_id(config)
        self._ensure_loaded(rid)
        out = super().put(config, checkpoint, metadata, new_versions)
        self._persist(rid)
        return out

    def put_writes(self, config, writes, task_id, task_path=""):  # noqa: ANN001
        rid = self._thread_id(config)
        out = super().put_writes(config, writes, task_id, task_path)
        self._persist(rid)
        return out
