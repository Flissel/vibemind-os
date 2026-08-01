"""validate-antwort: Cross-Checks vor dem Versand ans Jobcenter.

Prüft das generierte Anschreiben + Status-Report + Stammdaten auf
Konsistenz und Vollständigkeit. Gibt einen Validation-Report mit
Severity (PASS / WARN / FAIL) aus. FAIL blockiert den Approval-Gate.

Dies ist der haftungsrelevante Skill — er ersetzt KEINE menschliche Prüfung,
sondern fängt offensichtliche Fehler ab bevor Felix gegenzeichnet.

Aufruf:
    python skills/buergergeld/validate-antwort/run.py <status_report.yaml>
    python skills/buergergeld/validate-antwort/run.py <status_report.yaml> --anschreiben <docx>
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any

import yaml

_SKILL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SKILL_ROOT))


def _load_lib_module(name: str, file: str):
    spec = importlib.util.spec_from_file_location(
        f"buergergeld_{name}", _SKILL_ROOT / "_lib" / file
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_timeline = _load_lib_module("timeline_helper", "timeline_helper.py")
append_event = _timeline.append_event
buergergeld_root = _timeline.buergergeld_root
get_stammdaten = _timeline.get_stammdaten


class Check:
    def __init__(self, name: str):
        self.name = name
        self.findings: list[dict[str, str]] = []

    def add(self, severity: str, msg: str):
        self.findings.append({"severity": severity, "message": msg})

    def fail(self, msg: str):
        self.add("FAIL", msg)

    def warn(self, msg: str):
        self.add("WARN", msg)

    def ok(self, msg: str):
        self.add("PASS", msg)


def check_stammdaten_vollstaendig(stammdaten: dict[str, Any]) -> Check:
    c = Check("Stammdaten-Vollständigkeit")
    person = stammdaten.get("person", {})
    jobcenter = stammdaten.get("jobcenter", {})
    adresse = stammdaten.get("adresse", {})

    pflicht = {
        "Vorname": person.get("vorname"),
        "Nachname": person.get("nachname"),
        "BG-Nummer": jobcenter.get("bg_nummer"),
        "Adresse Straße": adresse.get("strasse"),
        "Adresse Ort": adresse.get("ort"),
        "Jobcenter-Name": jobcenter.get("name"),
    }
    for feld, wert in pflicht.items():
        if not wert or str(wert).strip() in ("", "null", "None"):
            c.fail(f"Pflichtfeld leer: {feld}")
    if not c.findings:
        c.ok("Alle Pflicht-Stammdaten vorhanden")
    return c


def check_frist(report: dict[str, Any]) -> Check:
    c = Check("Frist-Plausibilität")
    frist = report.get("frist")
    if not frist:
        c.warn("Keine Frist im Schreiben erkannt — manuell prüfen")
        return c
    if isinstance(frist, str):
        try:
            frist = datetime.strptime(frist, "%Y-%m-%d").date()
        except ValueError:
            c.warn(f"Frist nicht parsebar: {frist}")
            return c
    heute = date.today()
    if frist < heute:
        delta = (heute - frist).days
        c.warn(
            f"Frist ({frist}) ist seit {delta} Tagen überschritten. "
            f"Falls Antrag noch läuft: Nachreichung trotzdem schicken, ggf. "
            f"um Fristverlängerung / Nachsicht bitten."
        )
    else:
        delta = (frist - heute).days
        c.ok(f"Frist ({frist}) in {delta} Tagen — noch Zeit")
    return c


def check_anschreiben_inhalt(anschreiben_path: Path | None, stammdaten: dict[str, Any]) -> Check:
    c = Check("Anschreiben-Inhalt")
    if anschreiben_path is None or not anschreiben_path.exists():
        c.warn("Kein Anschreiben zum Prüfen übergeben")
        return c

    try:
        from docx import Document
        doc = Document(str(anschreiben_path))
        text = "\n".join(p.text for p in doc.paragraphs)
    except Exception as e:  # noqa: BLE001
        c.fail(f"Anschreiben nicht lesbar: {e}")
        return c

    person = stammdaten.get("person", {})
    jobcenter = stammdaten.get("jobcenter", {})
    name = f"{person.get('vorname', '')} {person.get('nachname', '')}".strip()
    bg_nr = str(jobcenter.get("bg_nummer", ""))

    if name and name in text:
        c.ok(f"Name korrekt im Anschreiben: {name}")
    else:
        c.fail(f"Name '{name}' fehlt im Anschreiben")

    if bg_nr and bg_nr in text:
        c.ok(f"BG-Nummer korrekt: {bg_nr}")
    else:
        c.fail(f"BG-Nummer '{bg_nr}' fehlt im Anschreiben")

    if "Unterschrift" in text or "_____" in text or "Mit freundlichen Grüßen" in text:
        c.ok("Unterschriftsbereich vorhanden")
    else:
        c.warn("Kein Unterschriftsbereich erkennbar")

    return c


def check_konsistenz_offen(report: dict[str, Any]) -> Check:
    c = Check("Konsistenz offene Forderungen")
    offen = [f for f in report["forderungen"] if f["klassifikation"] == "offen"]
    if not offen:
        c.ok("Keine offenen Pflicht-Forderungen — Antwort wäre rein erklärend")
        return c

    c.ok(f"{len(offen)} offene Pflicht-Forderung(en) zum Nachreichen identifiziert")

    # Heuristik: Items die wie 'gibt es nicht' klingen aber als offen markiert sind
    verdaechtig = ["mieterhoehung", "mieterhöhung", "wohngeld", "insolvenz"]
    for f in offen:
        fid = f.get("forderung_id", "").lower()
        if any(v in fid for v in verdaechtig):
            c.warn(
                f"'{f['forderung_titel']}' ist als nachzureichen markiert, könnte "
                f"aber 'nicht vorhanden / N/A' sein. Bitte prüfen ob das wirklich "
                f"eingereicht werden muss oder nur erklärt."
            )
    return c


def check_disclaimer() -> Check:
    c = Check("Rechtlicher Hinweis")
    c.warn(
        "Dies ist ein automatisch generierter Entwurf. Felix muss das Anschreiben "
        "und alle Anlagen vor Versand persönlich prüfen und unterschreiben. "
        "Falschangaben können nach §§ 60, 66 SGB I zu Leistungsversagung führen."
    )
    return c


def run(
    report_path: Path,
    anschreiben_path: Path | None = None,
    dry: bool = False,
) -> dict[str, Any]:
    if not report_path.is_absolute():
        candidate = buergergeld_root() / report_path
        report_path = candidate if candidate.exists() else Path.cwd() / report_path
    if not report_path.exists():
        raise FileNotFoundError(f"Status-Report nicht gefunden: {report_path}")

    with report_path.open("r", encoding="utf-8") as f:
        report = yaml.safe_load(f)

    stammdaten = get_stammdaten()

    # Anschreiben auto-finden falls nicht angegeben (neuestes im ausgang/)
    if anschreiben_path is None:
        ausgang = buergergeld_root() / "ausgang"
        candidates = sorted(ausgang.glob("*_Anschreiben_*.docx"), reverse=True)
        if candidates:
            anschreiben_path = candidates[0]

    checks = [
        check_stammdaten_vollstaendig(stammdaten),
        check_frist(report),
        check_anschreiben_inhalt(anschreiben_path, stammdaten),
        check_konsistenz_offen(report),
        check_disclaimer(),
    ]

    all_findings = []
    for c in checks:
        for f in c.findings:
            all_findings.append({"check": c.name, **f})

    n_fail = sum(1 for f in all_findings if f["severity"] == "FAIL")
    n_warn = sum(1 for f in all_findings if f["severity"] == "WARN")
    n_pass = sum(1 for f in all_findings if f["severity"] == "PASS")

    verdict = "FAIL" if n_fail else ("WARN" if n_warn else "PASS")

    result = {
        "verdict": verdict,
        "summary": {"fail": n_fail, "warn": n_warn, "pass": n_pass},
        "anschreiben": str(anschreiben_path) if anschreiben_path else None,
        "findings": all_findings,
    }

    if dry:
        result["dry_run"] = True
        return result

    # Report schreiben
    ausgang = buergergeld_root() / "ausgang"
    today = date.today().isoformat()
    val_path = ausgang / f"{today}_validation_report.yaml"
    with val_path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(
            {"meta": {"generated_at": datetime.now().isoformat(timespec="seconds")}, **result},
            f, allow_unicode=True, sort_keys=False,
        )
    result["report"] = str(val_path)

    append_event(
        typ="validation_done",
        titel=f"Validierung: {verdict} ({n_fail} FAIL, {n_warn} WARN, {n_pass} PASS)",
        quelle="skill:buergergeld-validate-antwort",
        verdict=verdict,
        summary=result["summary"],
    )

    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("report", help="Pfad zum status_report.yaml")
    parser.add_argument("--anschreiben", help="Pfad zum Anschreiben docx (sonst auto)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    result = run(
        Path(args.report),
        anschreiben_path=Path(args.anschreiben) if args.anschreiben else None,
        dry=args.dry_run,
    )
    print(json.dumps(result, indent=2, default=str, ensure_ascii=False))
