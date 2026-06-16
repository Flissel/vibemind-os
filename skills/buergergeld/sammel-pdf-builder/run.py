"""sammel-pdf-builder: Bündelt Akten-Abschnitte zu Sammel-PDFs.

Konvertiert docx → PDF (via Word COM / docx2pdf), merged mit vorhandenen PDFs
pro Abschnitt (01-05), erstellt ein Deckblatt mit Inhaltsverzeichnis.

PDF-Konvertierung:
    1. docx2pdf (nutzt MS Word COM) — beste Qualität, hier verfügbar
    2. Fallback: reportlab-Rendering aus extrahiertem Text (wenn Word fehlt)

Aufruf:
    python skills/buergergeld/sammel-pdf-builder/run.py --abschnitt 02_Einkommen
    python skills/buergergeld/sammel-pdf-builder/run.py --alle
    python skills/buergergeld/sammel-pdf-builder/run.py --dateien <pfad1> <pfad2>
    python skills/buergergeld/sammel-pdf-builder/run.py --abschnitt 02_Einkommen --dry-run
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import tempfile
from datetime import date
from pathlib import Path
from typing import Any

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

SKIP_PATTERNS = (".~lock", "~$", ".forderungen.yaml")
CONVERTIBLE = {".docx"}
PDF_EXT = {".pdf"}


def docx_to_pdf(docx_path: Path, out_dir: Path) -> Path | None:
    """Konvertiere docx → pdf. Word COM bevorzugt, reportlab als Fallback."""
    out_pdf = out_dir / (docx_path.stem + ".pdf")

    # Versuch 1: docx2pdf (Word COM)
    try:
        from docx2pdf import convert
        convert(str(docx_path), str(out_pdf))
        if out_pdf.exists() and out_pdf.stat().st_size > 0:
            return out_pdf
    except Exception:  # noqa: BLE001
        pass

    # Versuch 2: reportlab-Fallback (Text-Rendering)
    try:
        return _docx_to_pdf_reportlab(docx_path, out_pdf)
    except Exception:  # noqa: BLE001
        return None


def _docx_to_pdf_reportlab(docx_path: Path, out_pdf: Path) -> Path:
    from docx import Document
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer

    doc = Document(str(docx_path))
    styles = getSampleStyleSheet()
    story = []
    for p in doc.paragraphs:
        text = p.text.strip()
        if text:
            # Escape XML-Sonderzeichen für reportlab
            text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            story.append(Paragraph(text, styles["Normal"]))
            story.append(Spacer(1, 4))
    pdf = SimpleDocTemplate(str(out_pdf), pagesize=A4)
    pdf.build(story or [Paragraph("(leer)", styles["Normal"])])
    return out_pdf


def make_deckblatt(titel: str, dateien: list[str], out_pdf: Path) -> Path:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import cm
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, ListFlowable, ListItem

    styles = getSampleStyleSheet()
    story = [
        Paragraph(titel, styles["Title"]),
        Spacer(1, cm),
        Paragraph(f"Erstellt am {date.today().strftime('%d.%m.%Y')}", styles["Normal"]),
        Spacer(1, cm),
        Paragraph("Inhalt:", styles["Heading2"]),
    ]
    items = [
        ListItem(Paragraph(name.replace("&", "&amp;").replace("<", "&lt;"), styles["Normal"]))
        for name in dateien
    ]
    story.append(ListFlowable(items, bulletType="1"))
    SimpleDocTemplate(str(out_pdf), pagesize=A4).build(story)
    return out_pdf


def collect_abschnitt_files(abschnitt: str) -> list[Path]:
    nachweise = buergergeld_root() / "nachweise"
    target = None
    for d in nachweise.iterdir():
        if d.is_dir() and (d.name == abschnitt or d.name.startswith(abschnitt.split("_")[0])):
            target = d
            break
    if target is None:
        raise FileNotFoundError(f"Abschnitt nicht gefunden: {abschnitt} (in {nachweise})")

    files = []
    for p in sorted(target.glob("*")):
        if not p.is_file():
            continue
        if any(s in p.name for s in SKIP_PATTERNS):
            continue
        if p.name.startswith("_root_"):
            continue  # Duplikate aus Drive-Root überspringen
        if p.suffix.lower() in CONVERTIBLE or p.suffix.lower() in PDF_EXT:
            files.append(p)
    return files


def build_sammel_pdf(titel: str, source_files: list[Path], out_path: Path) -> dict[str, Any]:
    from pypdf import PdfWriter, PdfReader

    writer = PdfWriter()
    included = []
    failed = []

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)

        # Deckblatt
        deckblatt = make_deckblatt(titel, [f.name for f in source_files], tmp_dir / "_deckblatt.pdf")
        for page in PdfReader(str(deckblatt)).pages:
            writer.add_page(page)

        for src in source_files:
            ext = src.suffix.lower()
            pdf_path = None
            if ext in PDF_EXT:
                pdf_path = src
            elif ext in CONVERTIBLE:
                pdf_path = docx_to_pdf(src, tmp_dir)

            if pdf_path is None or not Path(pdf_path).exists():
                failed.append(src.name)
                continue

            try:
                for page in PdfReader(str(pdf_path)).pages:
                    writer.add_page(page)
                included.append(src.name)
            except Exception as e:  # noqa: BLE001
                failed.append(f"{src.name} (merge: {e})")

        out_path.parent.mkdir(parents=True, exist_ok=True)
        with out_path.open("wb") as f:
            writer.write(f)

    return {"included": included, "failed": failed, "n_pages": len(writer.pages)}


def run(
    abschnitt: str | None = None,
    alle: bool = False,
    dateien: list[str] | None = None,
    dry: bool = False,
) -> dict[str, Any]:
    today = date.today().isoformat()
    ausgang = buergergeld_root() / "ausgang"

    jobs = []
    if alle:
        nachweise = buergergeld_root() / "nachweise"
        for d in sorted(nachweise.iterdir()):
            if d.is_dir():
                jobs.append((d.name, collect_abschnitt_files(d.name)))
    elif abschnitt:
        jobs.append((abschnitt, collect_abschnitt_files(abschnitt)))
    elif dateien:
        files = [Path(d) for d in dateien]
        jobs.append(("Auswahl", files))
    else:
        raise ValueError("Either --abschnitt, --alle, or --dateien required")

    results = []
    for name, files in jobs:
        if dry:
            results.append({
                "abschnitt": name,
                "n_dateien": len(files),
                "dateien": [f.name for f in files],
                "dry_run": True,
            })
            continue

        out_path = ausgang / f"{today}_Sammel_{name}.pdf"
        info = build_sammel_pdf(f"Sammel-PDF — {name}", files, out_path)
        results.append({
            "abschnitt": name,
            "output": str(out_path),
            "n_eingebunden": len(info["included"]),
            "n_pages": info["n_pages"],
            "failed": info["failed"],
        })

    if not dry:
        total_pdfs = len(results)
        append_event(
            typ="sammel_pdf_gebaut",
            titel=f"Sammel-PDF gebaut: {total_pdfs} Abschnitt(e)",
            quelle="skill:buergergeld-sammel-pdf-builder",
            abschnitte=[r["abschnitt"] for r in results],
        )

    return {"jobs": results}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--abschnitt", help="Abschnittsname z.B. 02_Einkommen")
    parser.add_argument("--alle", action="store_true", help="Alle Abschnitte")
    parser.add_argument("--dateien", nargs="+", help="Explizite Dateiliste")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    result = run(
        abschnitt=args.abschnitt,
        alle=args.alle,
        dateien=args.dateien,
        dry=args.dry_run,
    )
    print(json.dumps(result, indent=2, default=str, ensure_ascii=False))
