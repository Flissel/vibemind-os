"""Capability Router — Phase 1 (regex routing only).

Maps incoming intents to a curated subset of OpenFang agents based on
data/capabilities.yaml. Falls back to None on no-match — caller decides
whether to broadcast.

See docs/plans/2026-05-01-capability-router-design.md and
2026-05-01-capability-router-plan.md for the full design.

Phase 1 is deliberately additive:
    - On no-match, route() returns None and the existing broadcast path
      in DiscourseEngine.tick_intent stays in effect (zero regression).
    - On match, returns a CapabilityMatch with agent names; the
      DiscourseEngine resolves them against its loaded OpenFang agents
      and dispatches to a focused 3-5 agent set instead of all 25.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

logger = logging.getLogger(__name__)


@dataclass
class CapabilityMatch:
    """Result of router.route() — routed agents + provenance."""

    capability: str
    description: str
    primary_names: List[str]
    supporting_names: List[str]
    matched_pattern: str
    match_method: str = "regex"          # "regex" | "semantic" (Phase 2) | "fallback"

    # Phase 1.5 fields — populated if YAML has execution_target / feedback_loop;
    # ignored by Phase 1 dispatch path (which only narrows agents).
    execution_target: Optional[str] = None
    arg_extractor: Optional[str] = None
    feedback_loop: Optional[Dict[str, Any]] = None

    @property
    def all_agent_names(self) -> List[str]:
        return list(self.primary_names) + list(self.supporting_names)

    @property
    def is_direct(self) -> bool:
        """True if Phase 1.5 should take over (direct execution path).
        Phase 1 ignores this and uses the agent-based path always."""
        return bool(self.execution_target and self.execution_target.startswith("direct:"))


@dataclass
class _CompiledCapability:
    capability: str
    description: str
    primary_names: List[str]
    supporting_names: List[str]
    patterns: List[re.Pattern] = field(default_factory=list)
    execution_target: Optional[str] = None
    arg_extractor: Optional[str] = None
    feedback_loop: Optional[Dict[str, Any]] = None


class CapabilityRouter:
    """Regex-driven router. Phase 1 — semantic fallback comes in Phase 2."""

    def __init__(self, registry_path: Path):
        self.registry_path = Path(registry_path)
        self._capabilities: List[_CompiledCapability] = []
        self._stats = {
            "matches": 0,
            "regex_matches": 0,
            "no_match": 0,
            "load_errors": 0,
            "bad_patterns": 0,
        }
        self._load()

    # ── Load / reload ─────────────────────────────────────────────────

    def _load(self) -> None:
        if not self.registry_path.exists():
            logger.warning(f"[cap-router] registry not found: {self.registry_path}")
            return
        try:
            data = yaml.safe_load(self.registry_path.read_text(encoding="utf-8")) or []
        except Exception as e:
            logger.error(f"[cap-router] yaml parse failed: {e}")
            self._stats["load_errors"] += 1
            return

        if not isinstance(data, list):
            logger.error(f"[cap-router] registry must be a list, got {type(data).__name__}")
            self._stats["load_errors"] += 1
            return

        compiled: List[_CompiledCapability] = []
        for entry in data:
            if not isinstance(entry, dict):
                logger.warning(f"[cap-router] skipping non-dict entry: {entry!r}")
                self._stats["load_errors"] += 1
                continue
            try:
                cap_id = entry["capability"]
                pats_raw = entry.get("match_patterns") or []
                # Compile each regex; skip individual bad ones rather than
                # losing the whole capability.
                patterns: List[re.Pattern] = []
                for p in pats_raw:
                    try:
                        patterns.append(re.compile(p, re.IGNORECASE))
                    except re.error as re_err:
                        logger.warning(
                            f"[cap-router] bad regex in '{cap_id}': {p!r} ({re_err})"
                        )
                        self._stats["bad_patterns"] += 1
                if not patterns:
                    logger.warning(f"[cap-router] '{cap_id}' has no usable patterns, skipping")
                    self._stats["load_errors"] += 1
                    continue
                agents_block = entry.get("agents") or {}
                compiled.append(_CompiledCapability(
                    capability=cap_id,
                    description=entry.get("description", ""),
                    primary_names=list(agents_block.get("primary") or []),
                    supporting_names=list(agents_block.get("supporting") or []),
                    patterns=patterns,
                    execution_target=entry.get("execution_target"),
                    arg_extractor=entry.get("result_arg_extractor"),
                    feedback_loop=entry.get("feedback_loop"),
                ))
            except Exception as e:
                logger.warning(f"[cap-router] skipping bad entry: {e}")
                self._stats["load_errors"] += 1
        self._capabilities = compiled
        logger.info(
            f"[cap-router] loaded {len(compiled)} capabilities from {self.registry_path}"
        )

    def reload(self) -> None:
        """Force re-read of YAML — useful when watcher detects changes."""
        self._capabilities = []
        # Reset structural counters but keep query counters so reloads don't
        # erase historical match stats.
        self._stats["load_errors"] = 0
        self._stats["bad_patterns"] = 0
        self._load()

    # ── Routing ───────────────────────────────────────────────────────

    def route(self, intent: str) -> Optional[CapabilityMatch]:
        """First regex hit wins. Returns None on no-match."""
        if not intent or not intent.strip():
            self._stats["no_match"] += 1
            return None
        for cap in self._capabilities:
            for pat in cap.patterns:
                if pat.search(intent):
                    self._stats["matches"] += 1
                    self._stats["regex_matches"] += 1
                    return CapabilityMatch(
                        capability=cap.capability,
                        description=cap.description,
                        primary_names=cap.primary_names,
                        supporting_names=cap.supporting_names,
                        matched_pattern=pat.pattern,
                        match_method="regex",
                        execution_target=cap.execution_target,
                        arg_extractor=cap.arg_extractor,
                        feedback_loop=cap.feedback_loop,
                    )
        self._stats["no_match"] += 1
        return None

    # ── Stats / introspection ─────────────────────────────────────────

    def stats_dict(self) -> Dict[str, Any]:
        return {
            "registry_path": str(self.registry_path),
            "registry_size": len(self._capabilities),
            "capabilities": [c.capability for c in self._capabilities],
            **self._stats,
        }

    def list_capabilities(self) -> List[Dict[str, Any]]:
        """Public listing for /api/capabilities/list — useful for the UI
        to render which agents handle which intent kind."""
        return [
            {
                "capability": c.capability,
                "description": c.description,
                "primary": c.primary_names,
                "supporting": c.supporting_names,
                "pattern_count": len(c.patterns),
                "has_execution_target": bool(c.execution_target),
                "has_feedback_loop": bool(c.feedback_loop and c.feedback_loop.get("enabled")),
            }
            for c in self._capabilities
        ]
