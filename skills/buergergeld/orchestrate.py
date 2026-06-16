"""orchestrate: Führt die komplette Bürgergeld-Nachreichungs-Pipeline aus.

Dies ist der CLI-Orchestrator, der die einzelnen Skills in Reihenfolge ausführt
und an einem Approval-Gate stoppt. Brain (multihop_execute) spiegelt diese
Sequenz später als Plan; dieser Orchestrator ist die testbare Referenz-Impl.

Pipeline:
  1. parse-eingang     PDF → forderungen.yaml
  2. status-abgleich   forderungen → status_report
  3. anschreiben-gen   status_report → Anschreiben.docx
  4. sammel-pdf        offene Abschnitte → Sammel-PDFs
  5. validate-antwort  Cross-Checks → verdict
  6. APPROVAL-GATE     stoppt, zeigt Zusammenfassung, wartet auf Mensch
  (7. Versand bleibt manuell — kein Auto-Upload)

Aufruf:
    python skills/buergergeld/orchestrate.py --pdf eingang/20260414_Jobcenter_MWS.pdf
    python skills/buergergeld/orchestrate.py --pdf <pdf> --dry-run
    python skills/buergergeld/orchestrate.py --status-only   # nur parse + abgleich
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

_SKILL_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(_SKILL_ROOT))


def _load_skill(folder: str):
    """Lade run.py eines Skill-Unterordners als Modul."""
    run_py = _SKILL_ROOT / folder / "run.py"
    spec = importlib.util.spec_from_file_location(f"bg_skill_{folder.replace('-', '_')}", run_py)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_lib():
    spec = importlib.util.spec_from_file_location(
        "bg_timeline_helper", _SKILL_ROOT / "_lib" / "timeline_helper.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_lib = _load_lib()
buergergeld_root = _lib.buergergeld_root
append_event = _lib.append_event


# Kategorie → Abschnitt-Mapping für Sammel-PDF
KATEGORIE_ABSCHNITT = {
    "persoenliche_daten": "01_Stellungnahme",
    "einkommen": "02_Einkommen",
    "unterkunft": "03_Unterkunft",
    "vermoegen": "04_Vermögen",
}


def run_pipeline(pdf: str, status_only: bool = False, dry: bool = False) -> dict[str, Any]:
    root = buergergeld_root()
    steps: list[dict[str, Any]] = []

    def log_step(name: str, result: Any, ok: bool = True):
        steps.append({"step": name, "ok": ok, "result": result})

    # === HOP 1: parse-eingang ===
    parse = _load_skill("parse-eingang")
    pdf_path = Path(pdf)
    p_result = parse.run(pdf_path, dry=dry)
    log_step("parse-eingang", {
        "n_forderungen": p_result.get("n_forderungen"),
        "frist": p_result.get("frist"),
        "output": p_result.get("output"),
    })
    if dry:
        forderungen_yaml = pdf_path.with_suffix(pdf_path.suffix + ".forderungen.yaml")
    else:
        forderungen_yaml = Path(p_result["output"])

    # === HOP 2: status-abgleich ===
    abgleich = _load_skill("status-abgleich")
    a_result = abgleich.run(forderungen_yaml, dry=dry)
    log_step("status-abgleich", {
        "summary": a_result.get("summary"),
        "report": a_result.get("report_yaml"),
    })

    if status_only:
        return {"steps": steps, "stopped_at": "status-abgleich", "summary": a_result.get("summary")}

    if dry:
        # Im dry-run können wir die Folge-Steps nicht real ausführen (kein report-file)
        return {"steps": steps, "dry_run": True}

    status_report_yaml = Path(a_result["report_yaml"])

    # === HOP 3: anschreiben-generator ===
    anschreiben = _load_skill("anschreiben-generator")
    an_result = anschreiben.run(status_report_yaml, typ="nachreichung", dry=False)
    log_step("anschreiben-generator", {
        "output": an_result.get("output"),
        "n_offen": an_result.get("n_offen"),
    })
    anschreiben_path = Path(an_result["output"])

    # === HOP 4: sammel-pdf-builder (für Abschnitte mit offenen Forderungen) ===
    import yaml
    with status_report_yaml.open("r", encoding="utf-8") as f:
        report = yaml.safe_load(f)
    offen_kategorien = sorted(set(
        f.get("kategorie") for f in report["forderungen"]
        if f["klassifikation"] == "offen"
    ) - {None})
    abschnitte = sorted(set(
        KATEGORIE_ABSCHNITT[k] for k in offen_kategorien if k in KATEGORIE_ABSCHNITT
    ))

    sammel = _load_skill("sammel-pdf-builder")
    sammel_results = []
    for abschnitt in abschnitte:
        try:
            s_result = sammel.run(abschnitt=abschnitt, dry=False)
            sammel_results.append(s_result["jobs"][0] if s_result.get("jobs") else {})
        except Exception as e:  # noqa: BLE001
            sammel_results.append({"abschnitt": abschnitt, "error": str(e)})
    log_step("sammel-pdf-builder", {"abschnitte": abschnitte, "results": sammel_results})

    # === HOP 5: validate-antwort ===
    validate = _load_skill("validate-antwort")
    v_result = validate.run(status_report_yaml, anschreiben_path=anschreiben_path, dry=False)
    log_step("validate-antwort", {
        "verdict": v_result.get("verdict"),
        "summary": v_result.get("summary"),
    })

    # === HOP 6: APPROVAL-GATE ===
    verdict = v_result.get("verdict", "WARN")
    offene_titel = an_result.get("offene_titel", [])
    gate = {
        "status": "BLOCKED" if verdict == "FAIL" else "AWAITING_HUMAN_APPROVAL",
        "verdict": verdict,
        "anschreiben": str(anschreiben_path),
        "sammel_pdfs": [r.get("output") for r in sammel_results if r.get("output")],
        "offene_positionen": offene_titel,
        "warnungen": [f["message"] for f in v_result.get("findings", []) if f["severity"] == "WARN"],
        "naechster_schritt": (
            "FAIL — Bitte Fehler beheben, nicht versenden."
            if verdict == "FAIL" else
            "Bitte Anschreiben + Sammel-PDFs prüfen, unterschreiben, dann manuell "
            "ins Jobcenter-Postfach hochladen. KEIN automatischer Versand."
        ),
    }
    log_step("approval-gate", gate, ok=(verdict != "FAIL"))

    append_event(
        typ="nachreichung_bereit",
        titel=f"Nachreichung vorbereitet (verdict={verdict}): {len(offene_titel)} offene Positionen, "
              f"{len([r for r in sammel_results if r.get('output')])} Sammel-PDFs",
        quelle="skill:buergergeld-orchestrate",
        verdict=verdict,
        anschreiben=str(anschreiben_path.relative_to(root)).replace("\\", "/")
            if str(anschreiben_path).startswith(str(root)) else str(anschreiben_path),
    )

    return {
        "steps": steps,
        "gate": gate,
        "summary": a_result.get("summary"),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdf", required=True, help="Behörden-PDF in eingang/")
    parser.add_argument("--status-only", action="store_true", help="Nur parse + abgleich")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    result = run_pipeline(args.pdf, status_only=args.status_only, dry=args.dry_run)
    print(json.dumps(result, indent=2, default=str, ensure_ascii=False))
