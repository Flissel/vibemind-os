"""Handoff-Bundle fuer Agenten-Auftraege (K1).

Baut aus (plan_id, trace_id, hop_id) ein minimales, schema-konformes
Brain-zu-OpenFang-Bundle, das ``validate_brain_openfang_handoff_bundle``
besteht. Alles ist deterministisch: keine Zufallswerte, keine Uhrzeit.

Festlegungen:
- IDs (plan_/handoff_/space_execution_contract_v1_...) sind 26 Zeichen
  Crockford-Base32 aus dem SHA-256 von (plan_id, trace_id, hop_id).
- Zeitstempel sind die feste Konstante ``FESTER_ZEITPUNKT`` (das Bundle ist
  eine Zulassungsurkunde, kein Ereignisprotokoll; echte Zeiten stehen im
  Auftragslog).
- Der Validator kennt nur 13 kanonische Spaces. ``agent_auftraege`` ist keiner
  (und verletzt das Namensmuster); solche Werte werden auf ``agentfarm``
  abgebildet.
- ``approval_ref``/``cost_ref`` (und die Executor-/Healthcheck-Referenzen) sind
  PLATZHALTER, kein Freigabe- oder Kostenkanal (User-Entscheid 2026-10-09 B).
  Der Validator prueft nur die Form. Der Schutz liegt in der Liste
  freigegebener Agenten im Ausfuehrer (config/agent_budget.yaml) und im
  Budget-Waechter. Ausgestellt wird nur fuer Nutzeranfragen (antwortkanal).
"""
from __future__ import annotations

import hashlib
import re

FESTER_ZEITPUNKT = "2026-01-01T00:00:00Z"
STANDARD_SPACE = "agentfarm"
KANONISCHE_SPACES = frozenset({
    "agentfarm", "bubbles", "coding", "desktop", "flowzen", "ideas", "minibook",
    "mirofish", "n8n", "research", "rowboat", "schedule", "video",
})
_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_ROLLEN = [{"role": "agent-auftrag", "required_agent_count": 1}]


def _ulid26(*teile: str) -> str:
    digest = hashlib.sha256("\x1f".join(teile).encode("utf-8")).digest()
    zahl = int.from_bytes(digest[:17], "big")  # 136 Bit >= 26*5
    zeichen = []
    for _ in range(26):
        zeichen.append(_CROCKFORD[zahl & 31])
        zahl >>= 5
    return "".join(reversed(zeichen))


def _slug(text: str, fallback: str) -> str:
    s = re.sub(r"[^a-z0-9-]+", "-", text.lower()).strip("-")
    if not s or not s[0].isalpha():
        s = f"{fallback}-{s}".strip("-")
    return s[:63].rstrip("-") or fallback


def baue_handoff_bundle(
    *,
    plan_id: str,
    trace_id: str,
    intent: str,
    hop_id: str,
    capability: str,
    agent: str,
    space_id: str = STANDARD_SPACE,
    kanal: str = "api",
) -> dict:
    """Liefert das fuenfteilige Bundle fuer genau einen Hop."""
    space = space_id if space_id in KANONISCHE_SPACES else STANDARD_SPACE
    schluessel = (plan_id, trace_id, hop_id)
    korrelation = f"handoff_v1_{_ulid26('korrelation', *schluessel)}"
    plan_ref = f"plan_v1_{_ulid26('plan', *schluessel)}"
    node_id = _slug(hop_id, "hop")
    nachricht = (intent or "").strip()[:4000] or f"Auftrag {capability}"
    kontext = f"Faehigkeit {capability} ueber Agent {agent} (Kanal {kanal})"

    channel_intent = {
        "contract_version": "v1",
        "correlation_id": korrelation,
        "channel_kind": "desktop-chat",
        "actor_context": {"actor_id": "brain-plan-executor"},
        "session_context": {"session_id": (trace_id or "trace-unbekannt")[:200]},
        "message": nachricht,
        "received_at": FESTER_ZEITPUNKT,
        "requested_space_id": space,
        "user_facing_expectations": {"reply": "optional", "evidence": "not-requested"},
    }
    brain_plan = {
        "contract_version": "v1",
        "plan_id": plan_ref,
        "intent": {
            "summary": nachricht,
            "context": [kontext[:4000]],
            "requested_by": "brain-plan-executor",
        },
        "participating_spaces": [{"space_id": space, "roles": [dict(r) for r in _ROLLEN]}],
        "tasks": [{
            "node_id": node_id,
            "order": 1,
            "summary": f"{capability} ausfuehren"[:4000],
            "space_ids": [space],
            "depends_on": [],
            "success_criteria": ["Der Agent liefert ein Ergebnis."],
            "evidence_requirements": [{
                "evidence_type": "log",
                "description": "Auftragsprotokoll des Agenten liegt vor.",
            }],
        }],
    }
    vertraege = [{
        "contract_version": "v1",
        "contract_id": f"space_execution_contract_v1_{_ulid26('vertrag', *schluessel)}",
        "space_id": space,
        "executor_id": "executor:brain-orchestrator",
        "approval_policy_ref": "approval-policy:standard",
        "cost_policy_ref": "cost-policy:bounded",
        "healthcheck_ref": "healthcheck:agent-auftraege",
        "golden_path_ref": "golden-path:agent-auftrag",
    }]
    lifecycle = {
        "contract_version": "v1",
        "correlation_id": korrelation,
        "plan_id": plan_ref,
        "participating_space_ids": [space],
        "revision": 3,
        "status": "execution_deferred",
        "updated_at": FESTER_ZEITPUNKT,
        "event_history": [
            {"revision": 1, "status": "planned", "occurred_at": FESTER_ZEITPUNKT},
            {"revision": 2, "status": "admitted", "reason_code": "admission-validated",
             "occurred_at": FESTER_ZEITPUNKT},
            {"revision": 3, "status": "execution_deferred",
             "reason_code": "execution-engine-deferred", "occurred_at": FESTER_ZEITPUNKT},
        ],
    }
    handoff = {
        "contract_version": "v1",
        "execution_mode": "cognitive",
        "execution_boundary": "openfang",
        "correlation_id": korrelation,
        "plan_id": plan_ref,
        "lifecycle_revision": 3,
        "brain_task_node_id": node_id,
        "space_id": space,
        "roles": [dict(r) for r in _ROLLEN],
        "approval_ref": "approval-policy:standard",
        "cost_ref": "cost-policy:bounded",
        "retry": {"classification": "none", "max_attempts": 1},
    }
    return {
        "channel_intent": channel_intent,
        "brain_plan": brain_plan,
        "space_execution_contracts": vertraege,
        "lifecycle": lifecycle,
        "handoff": handoff,
    }
