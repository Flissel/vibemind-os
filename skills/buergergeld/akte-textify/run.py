"""akte-textify: Konvertiert PDFs/docx/xlsx aus der Bürgergeld-Akte zu MD
für Fungus-Indexing. Idempotent über mtime + hash.

Aufruf:
    python -m skills.buergergeld.akte_textify.run
    BUERGERGELD_ROOT=/anderer/pfad python -m skills.buergergeld.akte_textify.run
"""

from __future__ import annotations

import hashlib
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

_SKILL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SKILL_ROOT))
import importlib.util
_spec = importlib.util.spec_from_file_location(
    "buergergeld_timeline_helper",
    _SKILL_ROOT / "_lib" / "timeline_helper.py",
)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
append_event = _mod.append_event
buergergeld_root = _mod.buergergeld_root


SOURCE_DIRS = ("eingang", "ausgang", "nachweise")
SKIP_PATTERNS = (".~lock", "~$")
EXTENSIONS = {".pdf", ".docx", ".xlsx", ".txt", ".md"}


def file_hash(path: Path) -> str:
    h = hashlib.sha1()
    with path.open("rb") as f:
        while chunk := f.read(64 * 1024):
            h.update(chunk)
    return h.hexdigest()


def collect_sources(root: Path) -> list[Path]:
    files: list[Path] = []
    for sub in SOURCE_DIRS:
        d = root / sub
        if not d.exists():
            continue
        for p in d.rglob("*"):
            if not p.is_file():
                continue
            if any(s in p.name for s in SKIP_PATTERNS):
                continue
            if p.suffix.lower() not in EXTENSIONS:
                continue
            files.append(p)
    return files


def convert_pdf(path: Path) -> str:
    from pypdf import PdfReader
    reader = PdfReader(str(path))
    out: list[str] = []
    for i, page in enumerate(reader.pages, start=1):
        out.append(f"\n## Page {i}\n")
        try:
            out.append(page.extract_text() or "")
        except Exception as e:  # noqa: BLE001
            out.append(f"[extract_text failed: {e}]")
    return "\n".join(out)


def convert_docx(path: Path) -> str:
    from docx import Document
    doc = Document(str(path))
    out: list[str] = []
    for para in doc.paragraphs:
        if para.text.strip():
            out.append(para.text)
    for ti, table in enumerate(doc.tables, start=1):
        out.append(f"\n### Tabelle {ti}\n")
        for row in table.rows:
            cells = [c.text.replace("\n", " ").replace("|", "/") for c in row.cells]
            out.append("| " + " | ".join(cells) + " |")
    return "\n".join(out)


def convert_xlsx(path: Path) -> str:
    from openpyxl import load_workbook
    wb = load_workbook(str(path), data_only=True)
    out: list[str] = []
    for sn in wb.sheetnames:
        out.append(f"\n## Sheet: {sn}\n")
        ws = wb[sn]
        for row in ws.iter_rows(values_only=True):
            if any(c is not None and str(c).strip() for c in row):
                cells = [str(c) if c is not None else "" for c in row]
                out.append("| " + " | ".join(cells) + " |")
    return "\n".join(out)


def convert(path: Path) -> str:
    ext = path.suffix.lower()
    if ext == ".pdf":
        return convert_pdf(path)
    if ext == ".docx":
        return convert_docx(path)
    if ext == ".xlsx":
        return convert_xlsx(path)
    if ext in {".txt", ".md"}:
        return path.read_text(encoding="utf-8", errors="replace")
    raise ValueError(f"Unsupported extension: {ext}")


def write_md(target: Path, source: Path, body: str, src_hash: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    frontmatter = {
        "source": str(source).replace("\\", "/"),
        "source_mtime": datetime.fromtimestamp(source.stat().st_mtime).isoformat(),
        "source_hash": src_hash,
        "converted_at": datetime.now().isoformat(),
    }
    content = "---\n" + yaml.safe_dump(frontmatter, allow_unicode=True, sort_keys=False) + "---\n\n" + body
    target.write_text(content, encoding="utf-8")


def load_manifest(index_root: Path) -> dict[str, Any]:
    mf = index_root / "_manifest.yaml"
    if not mf.exists():
        return {"entries": {}}
    with mf.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {"entries": {}}


def save_manifest(index_root: Path, manifest: dict[str, Any]) -> None:
    index_root.mkdir(parents=True, exist_ok=True)
    mf = index_root / "_manifest.yaml"
    manifest["updated"] = datetime.now().isoformat()
    with mf.open("w", encoding="utf-8") as f:
        yaml.safe_dump(manifest, f, allow_unicode=True, sort_keys=False)


def run(dry: bool = False) -> dict[str, Any]:
    root = buergergeld_root()
    index_root = root / ".text-index"
    sources = collect_sources(root)
    manifest = load_manifest(index_root)
    entries = manifest.setdefault("entries", {})

    stats = {"total": len(sources), "converted": 0, "skipped": 0, "failed": []}

    for src in sources:
        rel = src.relative_to(root)
        target = index_root / rel.with_suffix(rel.suffix + ".md")
        key = str(rel).replace("\\", "/")

        try:
            h = file_hash(src)
        except Exception as e:  # noqa: BLE001
            stats["failed"].append({"file": key, "stage": "hash", "error": str(e)})
            continue

        prev = entries.get(key, {})
        if prev.get("source_hash") == h and target.exists():
            stats["skipped"] += 1
            continue

        if dry:
            stats["converted"] += 1
            continue

        try:
            body = convert(src)
        except Exception as e:  # noqa: BLE001
            stats["failed"].append({"file": key, "stage": "convert", "error": str(e)})
            continue

        try:
            write_md(target, src, body, h)
        except Exception as e:  # noqa: BLE001
            stats["failed"].append({"file": key, "stage": "write", "error": str(e)})
            continue

        entries[key] = {
            "source_hash": h,
            "source_mtime": datetime.fromtimestamp(src.stat().st_mtime).isoformat(),
            "target": str(target.relative_to(root)).replace("\\", "/"),
            "converted_at": datetime.now().isoformat(),
        }
        stats["converted"] += 1

    if not dry:
        save_manifest(index_root, manifest)
        append_event(
            typ="akte_textified",
            titel=f"akte-textify run: {stats['converted']} konvertiert, "
                  f"{stats['skipped']} unverändert, {len(stats['failed'])} Fehler",
            quelle="skill:buergergeld-akte-textify",
            stats=stats,
        )

    return stats


if __name__ == "__main__":
    import argparse
    import json

    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    result = run(dry=args.dry_run)
    print(json.dumps(result, indent=2, default=str))
