"""status-abgleich: Forderungen × todo.yaml → Diff-Report.

Klassifiziert jede Forderung als erfuellt/offen/na/unklar. Nutzt regelbasierten
Match (ID-Slug) + LLM-Fallback für semantic match. Output ist Markdown-Report
zum Lesen + YAML-Report für Folge-Skills.

Aufruf:
    python skills/buergergeld/status-abgleich/run.py <forderungen_yaml>
    python skills/buergergeld/status-abgleich/run.py <forderungen_yaml> --dry-run
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
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
_llm = _load_lib_module("llm", "llm.py")

append_event = _timeline.append_event
buergergeld_root = _timeline.buergergeld_root
get_todo = _timeline.get_todo
llm_client = _llm.llm_client
llm_model = _llm.llm_model


def normalize_id(s: str) -> str:
    """Bring slugs in vergleichbare Form (lowercase, underscores → hyphens)."""
    s = s.lower().replace("_", "-")
    s = re.sub(r"[^a-z0-9-]", "", s)
    s = re.sub(r"-+", "-", s).strip("-")
    return s


def token_overlap(a: str, b: str) -> float:
    """Jaccard-Ähnlichkeit über tokenisierte Strings (für Titel-Match)."""
    ta = set(re.findall(r"\w+", a.lower()))
    tb = set(re.findall(r"\w+", b.lower()))
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def rule_based_match(forderung: dict[str, Any], todo_items: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Versucht ID-Slug-Match + Token-Overlap. Returns matched item or None."""
    fid = normalize_id(forderung.get("id", ""))
    ftitel = forderung.get("titel", "")
    fbeschreibung = forderung.get("beschreibung", "")

    # Exact ID-Match
    for item in todo_items:
        if normalize_id(item.get("id", "")) == fid:
            return item

    # Substring-Match in Slug
    for item in todo_items:
        tid = normalize_id(item.get("id", ""))
        if fid and tid and (fid in tid or tid in fid) and min(len(fid), len(tid)) >= 4:
            return item

    # Titel-Overlap > 0.5
    best_score = 0.0
    best_item = None
    for item in todo_items:
        score = max(
            token_overlap(ftitel, item.get("titel", "")),
            token_overlap(fbeschreibung[:200], item.get("titel", "") + " " + item.get("notiz", "")),
        )
        if score > best_score:
            best_score = score
            best_item = item

    if best_score >= 0.5:
        return best_item
    return None


STATUS_TO_KLASSIFIKATION = {
    "confirmed": "erfuellt",
    "submitted": "erfuellt",
    "na": "na",
    "open": "offen",
    "in_progress": "offen",
}


def classify(forderung: dict[str, Any], matched: dict[str, Any] | None) -> dict[str, Any]:
    if matched is None:
        return {
            "klassifikation": "offen" if not forderung.get("optional") else "offen_optional",
            "match": None,
            "begruendung": "Kein passendes Item in todo.yaml gefunden.",
        }
    status = matched.get("status", "open")
    klass = STATUS_TO_KLASSIFIKATION.get(status, "unklar")
    return {
        "klassifikation": klass,
        "match": {
            "todo_id": matched.get("id"),
            "todo_titel": matched.get("titel"),
            "todo_status": status,
            "todo_notiz": matched.get("notiz"),
        },
        "begruendung": f"todo.yaml: {matched.get('id')} = {status}",
    }


# Schlüsselwort-Heuristik: Forderung → erwartete Datei-Namens-Stichworte in nachweise/
AKTEN_KEYWORDS = {
    "stellungnahme-finanzierung": ["Stellungnahme_Finanzierung", "Stellungnahme.*Finanzierung"],
    "beschreibung-taetigkeit": ["Beschreibung_Taetigkeit", "Beschreibung.*Tätigkeit", "VibeMind"],
    "stellungnahme-einkommensrueckgang": ["Stellungnahme_Finanzierung"],  # Abschnitt 4 darin
    "kontoauszuege-3-monate": ["Kontoauszuege_Giro", "Kontoauszuege_Girokonto"],
    "kontoauszuege": ["Kontoauszuege"],
    "kontenuebersicht": ["Kontenuebersicht"],
    "einnahmen-ausgaben": ["Einkommensaufstellung"],
    "einkommensaufstellung": ["Einkommensaufstellung"],
    "mietvertrag": ["Mietvertrag"],
    "nebenkostenabrechnung": ["Nebenkostenabrechnung"],
    "heizkostenabrechnung": ["Heizkostenabrechnung"],
    "untermietvertrag": ["Untermiet", "Surya"],
    "zustimmung-untervermietung": ["Erlaubnis_Untervermietung", "Untervermietung"],
    "selbstauskunft-bargeld": ["Selbstauskunft_Bargeld", "Selbstauskunft.*Bargeld"],
    "nachweise-vermoegen": ["Selbstauskunft_Bargeld", "Kontenuebersicht"],
    "vermoegen": ["Selbstauskunft", "Kontenuebersicht"],
    "kuendigung": ["Kuendigung", "Kündigung"],
    "gewerbeanmeldung": ["Beschreibung_Taetigkeit"],  # erklärt darin warum keine
    "gewerbeabmeldung": ["Beschreibung_Taetigkeit"],
    "umsatzsteuer": ["Beschreibung_Taetigkeit"],
    "umsatzsteuervoranmeldungen": ["Beschreibung_Taetigkeit"],
    "betriebswirtschaftliche-auswertung": ["Beschreibung_Taetigkeit"],
    "kontoauszuege-geschaeftskonto": ["Beschreibung_Taetigkeit"],
    "checkliste": ["Checkliste"],
    "deckblatt": ["Deckblatt"],
}


def find_akte_matches(forderung_id: str, akten_files: list[Path]) -> list[Path]:
    """Sucht in Akten-Dateinamen nach Keywords die zur Forderung passen."""
    keywords = []
    fid_norm = normalize_id(forderung_id)
    for key, kws in AKTEN_KEYWORDS.items():
        if normalize_id(key) in fid_norm or fid_norm in normalize_id(key):
            keywords.extend(kws)

    if not keywords:
        # Fallback: nutze die Forderungs-ID-Tokens selbst
        tokens = [t for t in fid_norm.split("-") if len(t) >= 4]
        keywords = tokens

    if not keywords:
        return []

    matches = []
    for f in akten_files:
        name = f.name
        for kw in keywords:
            if re.search(kw, name, re.IGNORECASE):
                matches.append(f)
                break
    return matches


def collect_akten_files(root: Path) -> list[Path]:
    """Sammelt alle Akten-Dateien aus nachweise/."""
    nachweise = root / "nachweise"
    if not nachweise.exists():
        return []
    files = []
    for p in nachweise.rglob("*"):
        if p.is_file() and not p.name.startswith(".") and not p.name.startswith("~"):
            files.append(p)
    return files


def llm_resolve_unsicher(
    unsichere: list[tuple[dict, list[dict]]],
) -> dict[str, dict[str, Any]]:
    """Für jede unsichere Forderung: LLM entscheidet ob ein todo-Item passt."""
    if not unsichere:
        return {}

    client = llm_client("status_abgleich")
    model = llm_model("status_abgleich")
    results: dict[str, dict[str, Any]] = {}

    for forderung, kandidaten in unsichere:
        kandidaten_text = "\n".join(
            f"  - id={c.get('id')}, titel={c.get('titel')}, status={c.get('status')}, "
            f"notiz={c.get('notiz', '')[:120]}"
            for c in kandidaten[:10]
        ) or "  (keine Kandidaten)"

        prompt = f"""Entscheide ob die Forderung durch ein todo-Item abgedeckt ist.

FORDERUNG:
  id: {forderung.get('id')}
  titel: {forderung.get('titel')}
  beschreibung: {forderung.get('beschreibung')}
  kategorie: {forderung.get('kategorie')}
  optional: {forderung.get('optional', False)}

TODO-KANDIDATEN:
{kandidaten_text}

Antworte als JSON:
{{
  "match_id": "<todo-id oder null wenn kein match>",
  "konfidenz": "hoch | mittel | niedrig",
  "begruendung": "<1 Satz>"
}}

NUR das JSON, nichts sonst."""

        try:
            response = client.chat.completions.create(
                model=model,
                temperature=0.0,
                max_tokens=256,
                messages=[{"role": "user", "content": prompt}],
            )
            content = (response.choices[0].message.content or "").strip()
            if content.startswith("```"):
                lines = content.splitlines()
                content = "\n".join(lines[1:-1]) if lines[-1].startswith("```") else "\n".join(lines[1:])
            parsed = json.loads(content)
            results[forderung["id"]] = parsed
        except Exception as e:  # noqa: BLE001
            results[forderung["id"]] = {"match_id": None, "konfidenz": "niedrig", "begruendung": f"LLM-Fehler: {e}"}

    return results


def build_report(
    forderungen_data: dict[str, Any],
    todo_data: dict[str, Any],
) -> dict[str, Any]:
    forderungen = forderungen_data.get("forderungen", [])
    todo_items = todo_data.get("items", [])
    akten_files = collect_akten_files(buergergeld_root())

    klassifizierungen = []
    unsichere: list[tuple[dict, list[dict]]] = []

    for f in forderungen:
        matched = rule_based_match(f, todo_items)
        klass = classify(f, matched)

        # Wenn kein todo-Match: Akten-Lookup als zweite Quelle
        if matched is None:
            akten_matches = find_akte_matches(f.get("id", ""), akten_files)
            if akten_matches:
                # Akte enthält Dokument(e) die zur Forderung passen
                rel_paths = [
                    str(p.relative_to(buergergeld_root())).replace("\\", "/")
                    for p in akten_matches[:3]
                ]
                klass = {
                    "klassifikation": "erfuellt",
                    "match": {
                        "akte_files": rel_paths,
                        "begruendung": "Akten-Match",
                    },
                    "begruendung": f"In Akte vorhanden: {', '.join(rel_paths)}",
                }
            elif klass["klassifikation"] == "offen":
                # Kandidaten aus todo.yaml für LLM-Resolution
                kandidaten = [
                    t for t in todo_items
                    if token_overlap(f.get("titel", "") + " " + f.get("beschreibung", "")[:200],
                                     t.get("titel", "") + " " + (t.get("notiz") or "")) > 0.15
                ][:10]
                if kandidaten:
                    unsichere.append((f, kandidaten))

        klassifizierungen.append({
            "forderung_id": f.get("id"),
            "forderung_titel": f.get("titel"),
            "kategorie": f.get("kategorie"),
            "optional": f.get("optional", False),
            **klass,
        })

    # LLM-Auflösung der unsicheren
    if unsichere:
        llm_results = llm_resolve_unsicher(unsichere)
        for k in klassifizierungen:
            if k["forderung_id"] in llm_results:
                r = llm_results[k["forderung_id"]]
                if r.get("match_id") and r.get("konfidenz") in ("hoch", "mittel"):
                    # Re-classify mit gefundenem match
                    matched = next((t for t in todo_items if t.get("id") == r["match_id"]), None)
                    if matched:
                        new_klass = classify({"id": k["forderung_id"], "titel": k["forderung_titel"]}, matched)
                        k["klassifikation"] = new_klass["klassifikation"]
                        k["match"] = new_klass["match"]
                        k["begruendung"] = f"LLM-Match ({r['konfidenz']}): {r.get('begruendung', '')}"
                else:
                    k["begruendung"] = f"LLM: kein eindeutiger Match ({r.get('begruendung', '')})"

    # Aggregate
    summary = {
        "total": len(klassifizierungen),
        "erfuellt": sum(1 for k in klassifizierungen if k["klassifikation"] == "erfuellt"),
        "offen": sum(1 for k in klassifizierungen if k["klassifikation"] == "offen"),
        "offen_optional": sum(1 for k in klassifizierungen if k["klassifikation"] == "offen_optional"),
        "na": sum(1 for k in klassifizierungen if k["klassifikation"] == "na"),
    }

    return {
        "summary": summary,
        "forderungen": klassifizierungen,
        "absender": forderungen_data.get("absender"),
        "betreff": forderungen_data.get("betreff"),
        "datum_schreiben": forderungen_data.get("datum_schreiben"),
        "frist": forderungen_data.get("frist"),
    }


def render_markdown(report: dict[str, Any]) -> str:
    s = report["summary"]
    out = [
        f"# Status-Abgleich Bürgergeld",
        f"",
        f"**Schreiben:** {report.get('betreff', '?')}",
        f"**Absender:** {(report.get('absender') or {}).get('name', '?')}",
        f"**Datum Schreiben:** {report.get('datum_schreiben') or '?'}",
        f"**Frist:** {report.get('frist') or '?'}",
        f"",
        f"## Übersicht",
        f"",
        f"- Gesamt: **{s['total']}** Forderungen",
        f"- ✅ Erfüllt: **{s['erfuellt']}**",
        f"- ❌ Offen: **{s['offen']}**",
        f"- ⏳ Offen (optional): **{s['offen_optional']}**",
        f"- ⊘ N/A: **{s['na']}**",
        f"",
    ]

    for klass, label, icon in [
        ("offen", "Offene Forderungen", "❌"),
        ("offen_optional", "Optional Offene Forderungen", "⏳"),
        ("erfuellt", "Erfüllte Forderungen", "✅"),
        ("na", "Nicht zutreffende Forderungen", "⊘"),
    ]:
        items = [k for k in report["forderungen"] if k["klassifikation"] == klass]
        if not items:
            continue
        out.append(f"## {icon} {label} ({len(items)})")
        out.append("")
        for item in items:
            out.append(f"### {item['forderung_titel']}")
            out.append(f"")
            out.append(f"- **id:** `{item['forderung_id']}`")
            out.append(f"- **kategorie:** {item['kategorie']}")
            out.append(f"- **begründung:** {item['begruendung']}")
            if item.get("match"):
                m = item["match"]
                if "todo_id" in m:
                    out.append(f"- **todo-match:** `{m['todo_id']}` (status: {m['todo_status']})")
                if "akte_files" in m:
                    out.append(f"- **akte-match:**")
                    for af in m["akte_files"]:
                        out.append(f"    - `{af}`")
            out.append("")

    return "\n".join(out)


def run(forderungen_yaml: Path, dry: bool = False) -> dict[str, Any]:
    if not forderungen_yaml.is_absolute():
        candidate = buergergeld_root() / forderungen_yaml
        if candidate.exists():
            forderungen_yaml = candidate
        else:
            forderungen_yaml = Path.cwd() / forderungen_yaml

    if not forderungen_yaml.exists():
        raise FileNotFoundError(f"Forderungen-YAML nicht gefunden: {forderungen_yaml}")

    with forderungen_yaml.open("r", encoding="utf-8") as f:
        forderungen_data = yaml.safe_load(f)

    todo_data = get_todo()
    report = build_report(forderungen_data, todo_data)

    today = date.today().isoformat()
    ausgang = buergergeld_root() / "ausgang"
    ausgang.mkdir(exist_ok=True)
    md_path = ausgang / f"{today}_status_report.md"
    yaml_path = ausgang / f"{today}_status_report.yaml"

    result = {
        "summary": report["summary"],
        "n_offen": report["summary"]["offen"],
        "n_erfuellt": report["summary"]["erfuellt"],
        "frist": report.get("frist"),
        "input": str(forderungen_yaml),
    }

    if dry:
        result["dry_run"] = True
        result["report"] = report
        return result

    md_content = render_markdown(report)
    md_path.write_text(md_content, encoding="utf-8")
    with yaml_path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(
            {
                "meta": {
                    "source_forderungen": str(forderungen_yaml).replace("\\", "/"),
                    "generated_at": datetime.now().isoformat(timespec="seconds"),
                    "schema_version": 1,
                },
                **report,
            },
            f,
            allow_unicode=True,
            sort_keys=False,
        )

    result["report_md"] = str(md_path)
    result["report_yaml"] = str(yaml_path)

    append_event(
        typ="status_abgleich_done",
        titel=f"Status-Abgleich: {report['summary']['offen']} offen, {report['summary']['erfuellt']} erfüllt von {report['summary']['total']}",
        quelle="skill:buergergeld-status-abgleich",
        summary=report["summary"],
        report_md=str(md_path.relative_to(buergergeld_root())).replace("\\", "/")
            if str(md_path).startswith(str(buergergeld_root())) else str(md_path),
    )

    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("forderungen", help="Pfad zur forderungen.yaml")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    result = run(Path(args.forderungen), dry=args.dry_run)
    print(json.dumps(result, indent=2, default=str, ensure_ascii=False))
