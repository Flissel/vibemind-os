"""parse-eingang: PDF aus eingang/ → strukturierte Forderungsliste YAML.

Nutzt LLM (Claude Sonnet 4.5 via OpenRouter) für robustes Parsen von
Behördenschreiben. Output ist deterministisch (temperature 0) und Pydantic-
validiert.

Aufruf:
    python skills/buergergeld/parse-eingang/run.py <pdf_path>
    python skills/buergergeld/parse-eingang/run.py <pdf_path> --dry-run
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

# Helpers laden
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
llm_client = _llm.llm_client
llm_model = _llm.llm_model


SYSTEM_PROMPT = """Du bist ein präziser Parser für deutsche Behörden-Schreiben \
(Jobcenter, Agentur für Arbeit, Krankenkassen, Sozialämter).

Deine Aufgabe: Extrahiere aus dem PDF-Text strukturierte Daten in JSON.

WICHTIG: Jede einzelne Spiegelstrich-Position ist eine SEPARATE Forderung.
Wenn das Schreiben unter "Persönliche Daten" 7 Spiegelstriche listet
(ID-Prüfung, Stellungnahme, Beschreibung Tätigkeit, USt-IdNr, Gewerbeanmeldung,
Gewerbeabmeldung, Stellungnahme Einkommensrückgang), dann sind das 7 separate
Forderungen — NICHT ein Eintrag "Persönliche Daten" mit Kommas.

Ausgabe-Schema (NUR JSON, keine Erklärungen, keine Markdown-Fences):

{
  "absender": {
    "name": "string",
    "art": "jobcenter | agentur_fuer_arbeit | krankenkasse | sozialamt | rentenversicherung | sonstige",
    "adresse": "string"
  },
  "bezug": {
    "bg_nummer": "string oder null",
    "kundennummer": "string oder null",
    "aktenzeichen": "string oder null"
  },
  "datum_schreiben": "YYYY-MM-DD oder null",
  "frist": "YYYY-MM-DD oder null",
  "betreff": "string",
  "forderungen": [
    {
      "id": "kebab-case-slug",
      "titel": "string (kurz, eine Sache)",
      "kategorie": "persoenliche_daten | einkommen | unterkunft | vermoegen | sonstiges",
      "beschreibung": "string (volle Original-Formulierung des Spiegelstrichs)",
      "paragraph": "string oder null",
      "optional": true | false
    }
  ],
  "konsequenzen": ["string", ...],
  "weitere_hinweise": ["string", ...]
}

Regeln:
- EIN Spiegelstrich = EINE Forderung. Niemals mehrere Spiegelstriche in einem Eintrag.
- `id` eindeutig, lowercase-kebab-case, beschreibt die Sache (z.B. "stellungnahme-finanzierung",
  "kontoauszuege-3-monate", "iav-arbeitsvertrag")
- `titel` ist die Kurzform (5-10 Wörter), `beschreibung` der volle Spiegelstrich-Text
- `optional` = true wenn "soweit vorhanden", "gegebenenfalls", "falls bezogen" im Text steht
- `paragraph` nur wenn explizit genannt (z.B. "§ 60 SGB I")
- Halluziniere NICHTS — wenn unklar, schreibe null oder lass weg
- Datums-Format strikt YYYY-MM-DD
"""


def extract_pdf_text(pdf_path: Path) -> str:
    from pypdf import PdfReader
    reader = PdfReader(str(pdf_path))
    pages = []
    for i, page in enumerate(reader.pages, start=1):
        try:
            text = page.extract_text() or ""
        except Exception:  # noqa: BLE001
            text = ""
        pages.append(f"--- Page {i} ---\n{text}")
    return "\n\n".join(pages)


def call_llm(pdf_text: str) -> dict[str, Any]:
    client = llm_client("parse_eingang")
    model = llm_model("parse_eingang")

    response = client.chat.completions.create(
        model=model,
        temperature=0.0,
        max_tokens=4096,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"PDF-TEXT:\n\n{pdf_text}\n\nGib NUR das JSON aus, nichts sonst."},
        ],
    )
    content = response.choices[0].message.content or ""

    # Strip markdown code fence falls LLM trotz Prompt eines macht
    content = content.strip()
    if content.startswith("```"):
        lines = content.splitlines()
        content = "\n".join(lines[1:-1]) if lines[-1].startswith("```") else "\n".join(lines[1:])

    return json.loads(content)


def _date_or_none(value: Any) -> date | None:
    if not value or value == "null":
        return None
    if isinstance(value, date):
        return value
    try:
        return datetime.strptime(str(value), "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def normalize(parsed: dict[str, Any]) -> dict[str, Any]:
    """Konvertiert LLM-Output in finales YAML-Schema mit echten dates."""
    return {
        "absender": parsed.get("absender") or {},
        "bezug": parsed.get("bezug") or {},
        "datum_schreiben": _date_or_none(parsed.get("datum_schreiben")),
        "frist": _date_or_none(parsed.get("frist")),
        "betreff": parsed.get("betreff", ""),
        "forderungen": parsed.get("forderungen") or [],
        "konsequenzen": parsed.get("konsequenzen") or [],
        "weitere_hinweise": parsed.get("weitere_hinweise") or [],
    }


def write_yaml(
    pdf_path: Path,
    data: dict[str, Any],
    model: str,
) -> Path:
    output = pdf_path.with_suffix(pdf_path.suffix + ".forderungen.yaml")
    frontmatter = {
        "meta": {
            "source_pdf": str(pdf_path).replace("\\", "/"),
            "parsed_at": datetime.now().isoformat(timespec="seconds"),
            "llm_model": model,
            "schema_version": 1,
        }
    }
    full = {**frontmatter, **data}
    with output.open("w", encoding="utf-8") as f:
        yaml.safe_dump(full, f, allow_unicode=True, sort_keys=False, default_flow_style=False)
    return output


def run(pdf_path: Path, dry: bool = False) -> dict[str, Any]:
    if not pdf_path.is_absolute():
        candidate = buergergeld_root() / pdf_path
        if candidate.exists():
            pdf_path = candidate
        else:
            pdf_path = Path.cwd() / pdf_path

    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF nicht gefunden: {pdf_path}")

    text = extract_pdf_text(pdf_path)
    if len(text.strip()) < 50:
        raise ValueError(f"PDF hat keinen extrahierbaren Text-Layer (Länge {len(text)}). OCR wäre nötig.")

    parsed = call_llm(text)
    data = normalize(parsed)

    model = llm_model("parse_eingang")

    result = {
        "pdf": str(pdf_path),
        "absender": data["absender"].get("name", "unbekannt"),
        "datum_schreiben": data["datum_schreiben"],
        "frist": data["frist"],
        "n_forderungen": len(data["forderungen"]),
        "kategorien": sorted(set(f.get("kategorie", "") for f in data["forderungen"]) - {""}),
    }

    if dry:
        result["dry_run"] = True
        result["data"] = data
        return result

    out_path = write_yaml(pdf_path, data, model)
    result["output"] = str(out_path)

    append_event(
        typ="schreiben_eingang_parsed",
        titel=f"Schreiben geparsed: {data['betreff'] or pdf_path.name}",
        quelle="skill:buergergeld-parse-eingang",
        absender=data["absender"].get("name"),
        n_forderungen=len(data["forderungen"]),
        frist=data["frist"],
        output_yaml=str(out_path.relative_to(buergergeld_root())).replace("\\", "/")
            if str(out_path).startswith(str(buergergeld_root())) else str(out_path),
    )

    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("pdf", help="Pfad zur PDF (relativ zu BUERGERGELD_ROOT oder absolut)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    result = run(Path(args.pdf), dry=args.dry_run)
    print(json.dumps(result, indent=2, default=str, ensure_ascii=False))
