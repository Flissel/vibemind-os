"""Phase A — Semantischer Difficulty-Router (Qwen-Cosine).

Stuft einen Intent in vier Schwierigkeitsgrade ein und wählt damit das passende
„Geschütz" (statt des fragilen Verb-Zählens in _looks_like_multi_action):

    easy   → direkte Chat-Antwort (kein Planer)
    medium → Capability-Shortcut (1-Hop)
    hard   → SoM-Planner (mehrstufig, robust)
    insane → AutoGen SelectorGroupChat (vage/explorativ, hartes Geschütz)

SEMANTISCH, nicht keyword-zählend (User-Vorgabe 2026-06-08): der Intent wird im
selben Qwen3-Embedding-Raum (1024-dim, multilingual DE/EN) eingebettet, in dem
der Brain ohnehin denkt (core.qdrant_kg.Embedder), und per Cosine gegen kuratierte
Anker-Beispiele je Level verglichen — nächster Anker-Cluster gewinnt. Kein
Per-Call-LLM (Embedder ist lokal/in-process). „erstelle Excel" landet so bei hard,
obwohl es nur 1 Verb ist und keine Komplexitäts-Keywords enthält — genau der Fall,
an dem das alte Verb-Zählen scheiterte.

Fallback (Embedder/sentence-transformers nicht verfügbar): TaskFeatureRouter.
estimate_complexity-Heuristik. Nie ein Crash — schlimmstenfalls grober Bucket.

Reuse: core.qdrant_kg.Embedder (Embedder.get().encode → normalisiert), optional
core.task_feature_router.TaskFeatureRouter (Heuristik). Embedder + anchors sind
injizierbar (Test/Tuning ohne Modell-Load).
"""

from __future__ import annotations

import logging
import os
from typing import Any, Optional

import numpy as np

logger = logging.getLogger("brain.difficulty_router")

LEVELS = ("easy", "medium", "hard", "insane")

# level → handler im multihop_execute-Dispatch
_HANDLER = {
    "easy": "chat",
    "medium": "shortcut",
    "hard": "som",
    "insane": "autogen",
}


def handler_for(level: str) -> str:
    """Mappt ein Schwierigkeits-Level auf den Dispatch-Handler."""
    return _HANDLER.get(level, "som")  # unbekannt → sicher mehrstufig (SoM)


# Kuratierte Anker je Level (DE + EN). Der Cosine-nächste Cluster bestimmt das
# Level. Bewusst breit + zweisprachig — die Anker sind die Qualität des Routers.
DEFAULT_ANCHORS: dict[str, list[str]] = {
    "easy": [
        "danke", "wie geht's", "wie spät ist es", "was ist die hauptstadt von frankreich",
        "thanks", "what time is it", "tell me a joke", "wer bist du", "hallo",
        "erklär mir kurz was X bedeutet", "what does this word mean",
    ],
    "medium": [
        "öffne example.com im browser", "schalte das licht im wohnzimmer an",
        "speichere diese datei", "suche nach dem letzten bericht",
        "open the dashboard", "send a message to anna", "starte den container",
        "zeig mir den status", "lies die neueste email vor",
    ],
    "hard": [
        "erstelle eine excel mit den spalten name und betrag auf dem desktop",
        "plane meine bewerbung und bereite die unterlagen vor",
        "baue ein anschreiben und eine tabelle und leg sie ab",
        "create a report from the data and export it as pdf",
        "richte eine pipeline ein die die daten verarbeitet und speichert",
        "mach erst A, dann B und dann C", "generiere ein dokument mit mehreren abschnitten",
    ],
    "insane": [
        "wie baue ich am besten einen kompletten newsletter-flow auf",
        "entwirf eine strategie für meinen produkt-launch",
        "überlege dir wie wir das marketing automatisieren könnten",
        "design an end-to-end architecture for the whole system",
        "finde heraus was wir verbessern könnten und schlage etwas vor",
        "plane und orchestriere ein mehrteiliges projekt von grund auf",
        "research the topic broadly and synthesize recommendations",
    ],
}


class DifficultyRouter:
    """Semantischer Schwierigkeits-Klassifikator mit Heuristik-Fallback."""

    def __init__(
        self,
        embedder: Any = None,
        anchors: Optional[dict[str, list[str]]] = None,
        complexity_fn: Any = None,
        disable_semantic: bool = False,
    ) -> None:
        """
        embedder: Objekt mit encode(text)->vec (+ optional encode_batch). None →
                  versucht core.qdrant_kg.Embedder.get(); scheitert das → Heuristik.
        anchors: {level: [beispiele]} — default DEFAULT_ANCHORS.
        complexity_fn: callable(text)->0..1 für den Fallback; None → lazy
                       TaskFeatureRouter.estimate_complexity.
        disable_semantic: True → NIE den Embedder nutzen (reiner Heuristik-Pfad,
                          für Test/Kill-Switch). Hat Vorrang vor embedder.
        """
        self._anchors = anchors or DEFAULT_ANCHORS
        self._disable_semantic = disable_semantic
        self._embedder = None if disable_semantic else embedder
        # _embedder_tried True = nicht mehr auto-resolven. Bei disable_semantic
        # oder bereits injiziertem Embedder ist die Auflösung „erledigt".
        self._embedder_tried = disable_semantic or (embedder is not None)
        self._complexity_fn = complexity_fn
        self._anchor_mat: Optional[np.ndarray] = None   # (n_anchors, dim)
        self._anchor_levels: list[str] = []

    # ── Embedder lazy auflösen (nie beim Import Modell laden) ──────────────────
    def _get_embedder(self) -> Any:
        if self._embedder is not None:
            return self._embedder
        if self._embedder_tried:
            return None
        self._embedder_tried = True
        if os.environ.get("DIFFICULTY_SEMANTIC", "1") in ("0", "false", "False"):
            return None
        try:
            from core.qdrant_kg import Embedder
            self._embedder = Embedder.get()
        except Exception as e:  # noqa: BLE001 — kein Embedder → Heuristik
            logger.warning(f"[difficulty] Embedder nicht verfügbar ({e}), Heuristik-Fallback")
            self._embedder = None
        return self._embedder

    def _ensure_anchor_matrix(self, embedder: Any) -> bool:
        """Bettet die Anker einmalig ein (encode_batch falls vorhanden)."""
        if self._anchor_mat is not None:
            return True
        texts: list[str] = []
        levels: list[str] = []
        for lvl in LEVELS:
            for ex in self._anchors.get(lvl, []):
                texts.append(ex)
                levels.append(lvl)
        if not texts:
            return False
        try:
            if hasattr(embedder, "encode_batch"):
                vecs = embedder.encode_batch(texts)
            else:
                vecs = [embedder.encode(t) for t in texts]
            mat = np.asarray(vecs, dtype=np.float32)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[difficulty] Anker-Embedding fehlgeschlagen ({e})")
            return False
        self._anchor_mat = mat
        self._anchor_levels = levels
        return True

    @staticmethod
    def _norm(v: np.ndarray) -> np.ndarray:
        n = np.linalg.norm(v)
        return v / n if n > 0 else v

    # ── Haupteinstieg ─────────────────────────────────────────────────────────
    def classify(self, intent: str) -> dict[str, Any]:
        """-> {level, score, reason, method}."""
        intent = (intent or "").strip()
        if not intent:
            return {"level": "easy", "score": 0.0, "method": "default",
                    "reason": "leerer Intent"}

        embedder = self._get_embedder()
        if embedder is not None and self._ensure_anchor_matrix(embedder):
            try:
                qv = self._norm(np.asarray(embedder.encode(intent), dtype=np.float32))
                # Anker sind (Qwen) bereits normalisiert; Cosine = Dot-Produkt.
                sims = self._anchor_mat @ qv
                best = int(np.argmax(sims))
                level = self._anchor_levels[best]
                score = float(sims[best])
                return {"level": level, "score": score, "method": "qwen-cosine",
                        "reason": f"naechster Anker '{self._anchors[level][0][:40]}' (cos={score:.2f})"}
            except Exception as e:  # noqa: BLE001 — Embedding-Fehler → Heuristik
                logger.warning(f"[difficulty] Cosine fehlgeschlagen ({e}), Heuristik")

        return self._classify_heuristic(intent)

    # ── Fallback: estimate_complexity-Heuristik auf 4 Stufen mappen ───────────
    def _classify_heuristic(self, intent: str) -> dict[str, Any]:
        fn = self._complexity_fn
        if fn is None:
            try:
                from core.task_feature_router import TaskFeatureRouter
                fn = TaskFeatureRouter().estimate_complexity
                self._complexity_fn = fn
            except Exception:  # noqa: BLE001
                fn = None
        if fn is None:
            # Allerletzter Fallback: Länge als grober Proxy
            n = len(intent)
            c = 0.2 if n < 25 else 0.5 if n < 80 else 0.8
        else:
            try:
                c = float(fn(intent))
            except Exception:  # noqa: BLE001
                c = 0.5
        # 0..1 → 4 Buckets (konservativ: lieber hard als insane fehlrouten)
        if c < 0.25:
            level = "easy"
        elif c < 0.55:
            level = "medium"
        elif c < 0.8:
            level = "hard"
        else:
            level = "insane"
        return {"level": level, "score": c, "method": "heuristic",
                "reason": f"complexity={c:.2f} (Heuristik-Fallback)"}


# Modul-Singleton für den Brain-Handler (Anker einmal eingebettet, Embedder geteilt)
_DEFAULT: Optional[DifficultyRouter] = None


def get_router() -> DifficultyRouter:
    global _DEFAULT
    if _DEFAULT is None:
        # DIFFICULTY_SEMANTIC=0 → reiner Heuristik-Pfad (kein Modell-Load)
        disable = os.environ.get("DIFFICULTY_SEMANTIC", "1") in ("0", "false", "False")
        _DEFAULT = DifficultyRouter(disable_semantic=disable)
    return _DEFAULT
