"""SoM Planner — Pydantic-Schemas für die JSON-Outputs der 3 Agent-Rollen.

Jede Rolle (Planner/Executor/Validator) liefert JSON das gegen das passende
Schema validiert wird. Hält claude-code-Output ehrlich (kein Freitext, kein
Halluzinieren der Struktur).
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


# ── PLANNER ────────────────────────────────────────────────────────────────
class PlanStep(BaseModel):
    id: str = Field(..., description="kebab-case eindeutig")
    beschreibung: str
    capability: Optional[str] = Field(None, description="Name aus capabilities.yaml falls passend")
    braucht_daten: list[str] = Field(default_factory=list, description="Welche Eingangsdaten dieser Schritt braucht")
    liefert_daten: list[str] = Field(default_factory=list)
    depends_on: list[str] = Field(default_factory=list)


class PlanSchema(BaseModel):
    intent: str
    rationale: str = ""
    steps: list[PlanStep]
    offene_fragen: list[str] = Field(default_factory=list, description="Was der Planner nicht entscheiden konnte")


# ── EXECUTOR ───────────────────────────────────────────────────────────────
class ExecStep(BaseModel):
    plan_step_id: str = Field(..., description="Verweis auf PlanStep.id")
    execution_target: Optional[str] = Field(None, description="z.B. openfang:agent, skill:..., supabase:...")
    konkrete_args: dict = Field(default_factory=dict)
    reihenfolge: int
    parallel_ok: bool = False


class ExecSchema(BaseModel):
    steps: list[ExecStep]
    benoetigte_daten_fehlen: list[str] = Field(default_factory=list, description="Daten die der Plan braucht aber nicht hat")


# ── VALIDATOR ──────────────────────────────────────────────────────────────
class ValidationFinding(BaseModel):
    severity: Literal["PASS", "WARN", "FAIL"]
    plan_step_id: Optional[str] = None
    message: str


class ApprovalGate(BaseModel):
    plan_step_id: str
    grund: str


class VerdictSchema(BaseModel):
    verdict: Literal["PASS", "WARN", "FAIL"]
    findings: list[ValidationFinding] = Field(default_factory=list)
    approval_gates: list[ApprovalGate] = Field(default_factory=list)
    feedback_fuer_planner: Optional[str] = Field(None, description="Bei FAIL: konkreter Mangel für Korrektur-Runde")


# ── MATRIX (Phase 2) ───────────────────────────────────────────────────────
class MatrixNode(BaseModel):
    id: str
    quelle: Literal["planner", "executor", "validator"]
    beschreibung: str
    braucht_daten: list[str] = Field(default_factory=list)
    liefert_daten: list[str] = Field(default_factory=list)
    approval_noetig: bool = False


class MatrixEdge(BaseModel):
    from_: str = Field(..., alias="from")
    to: str
    daten: str = ""


class MatrixSchema(BaseModel):
    run_id: str
    intent: str
    nodes: list[MatrixNode]
    edges: list[MatrixEdge] = Field(default_factory=list)
    status: Literal["draft", "validated", "ready"] = "draft"


ROLE_SCHEMA = {
    "planner": PlanSchema,
    "executor": ExecSchema,
    "validator": VerdictSchema,
}

# Forward-Refs auflösen (nötig wenn das Modul dynamisch via importlib geladen
# wird — dann sieht Pydantic die nested Models nicht automatisch im Namespace).
for _m in (PlanStep, PlanSchema, ExecStep, ExecSchema, ValidationFinding,
           ApprovalGate, VerdictSchema, MatrixNode, MatrixEdge, MatrixSchema):
    _m.model_rebuild()


if __name__ == "__main__":
    # Selbsttest: jedes Schema mit Minimal-Daten
    p = PlanSchema(intent="x", steps=[PlanStep(id="s1", beschreibung="parse")])
    e = ExecSchema(steps=[ExecStep(plan_step_id="s1", reihenfolge=1)])
    v = VerdictSchema(verdict="PASS")
    m = MatrixSchema(run_id="r1", intent="x", nodes=[MatrixNode(id="s1", quelle="planner", beschreibung="parse")])
    assert p.steps[0].id == "s1"
    assert e.steps[0].plan_step_id == "s1"
    assert v.verdict == "PASS"
    assert m.nodes[0].quelle == "planner"
    print("schemas.py selftest OK")
