"""Hintergrund-Vertrag und systemd-Einheiten des Geschaeftsdaten-Laufs (Teil B, Task 10).

Rowboats Hintergrund-Agenten duerfen `companys/<Firma>/{Kontakte,Newsletter,Wochenbild}` nicht bearbeiten.
Das haengt an upstream-Code: aendert er sich, wird dieser Test rot und Teil B muss neu geprueft werden.
"""
from __future__ import annotations

import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

_ROWBOAT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(_ROWBOAT / "geschaeftsdaten"))
sys.path.insert(0, str(_ROWBOAT.parents[1]))

from geschaeftsdaten_fake import ereignis, inhalt, kontakt  # noqa: E402
from newsletter import newsletter_notiz  # noqa: E402
from quelle import Medium, Versand  # noqa: E402
from steckbrief import steckbrief  # noqa: E402
from wochenbild import wochenbild  # noqa: E402

KNOWLEDGE = _ROWBOAT / "rowboat" / "apps" / "x" / "packages" / "core" / "src" / "knowledge"
DEPLOY = _ROWBOAT / "geschaeftsdaten" / "deploy"
UTC = timezone.utc


def _ts(name: str) -> str:
    pfad = KNOWLEDGE / name
    if not pfad.is_file():
        pytest.skip(f"Rowboat-Checkout fehlt ({name})")
    return pfad.read_text(encoding="utf-8")


def test_rowboat_hintergrund_fasst_companys_nicht_an():
    ordner = set(re.findall(r"""^\s*folder:\s*["']([^"']+)["']""", _ts("note_system.ts"), re.MULTILINE))
    assert ordner and ordner <= {"People", "Organizations", "Projects", "Topics", "Meetings"}

    kuration = re.search(r"CURATION_ENTITY_FOLDERS\s*=\s*\[([^\]]*)\]", _ts("build_graph.ts"))
    assert kuration and re.findall(r"'([^']+)'", kuration.group(1)) == ["People", "Organizations", "Projects", "Topics"]

    assert "fields['live_note'] !== 'true'" in _ts("inline_tasks.ts")
    assert "content.startsWith('---')" in _ts("tag_notes.ts")

    fileops = _ts("live-note/fileops.ts")
    assert re.search(r"function getLiveBlock\([^)]*\)[^{]*\{\s*return fm\.live \?\? null;", fileops)
    assert "const raw = getLiveBlock(frontmatter);\n    if (!raw) return null;" in fileops.replace("\r\n", "\n")


def _notizen() -> list[str]:
    sb = steckbrief(kontakt(name="@rowboat Test"), [ereignis()], laden="sales", heute=date(2026, 10, 26),
                    people=None, empfohlen_von="@rowboat")
    nl = newsletter_notiz(inhalt(felder={"betreff": "Hallo @rowboat", "abschnitte": [{"titel": "", "text": "x"}]}),
                          Versand(inhalt().id, datetime(2026, 10, 22, 8, 0, tzinfo=UTC), 2))
    wb = wochenbild("VibeMind", 2026, 43, [kontakt()], [ereignis()], [("companys/VibeMind/Newsletter/x", 1)],
                    [Medium("reel", "vibemind", "@rowboat Reel", 3.0, datetime(2026, 10, 21, tzinfo=UTC))])
    return [sb, nl, wb]


def test_erzeugte_notizen_triggern_keinen_agenten():
    for text in _notizen():
        assert text.startswith("---\n")
        kopf = text[4:text.index("\n---\n", 4)].splitlines()
        schluessel = {z.split(":", 1)[0].strip() for z in kopf if ":" in z and not z.startswith((" ", "\t"))}
        assert not schluessel & {"live", "live_note", "curated_at"}
        assert "erzeugt_von" in schluessel
        assert "@rowboat" not in text.lower()


def _einheit(name: str) -> dict[str, list[str]]:
    werte: dict[str, list[str]] = {}
    for zeile in (DEPLOY / name).read_text(encoding="utf-8").splitlines():
        zeile = zeile.strip()
        if zeile and not zeile.startswith(("#", ";", "[")) and "=" in zeile:
            k, _, v = zeile.partition("=")
            werte.setdefault(k.strip(), []).append(v.strip())
    return werte


def test_systemd_einheiten():
    timer = _einheit("rowboat-geschaeftsdaten.timer")
    assert timer["OnCalendar"] == ["*-*-* 02:30:00 Europe/Berlin"]
    assert timer["Persistent"] == ["true"]

    dienst = _einheit("rowboat-geschaeftsdaten.service")
    assert dienst["Type"] == ["oneshot"]
    assert dienst["User"] == ["debian"]
    assert dienst["ExecStart"] == ["/usr/bin/python3 /home/debian/marketing-os/spaces/rowboat/geschaeftsdaten/lauf.py"]
    assert dienst["Nice"] == ["10"]
    assert dienst["TimeoutStartSec"] == ["30min"]
    assert dienst["NoNewPrivileges"] == ["yes"]
    for einheit in (timer, dienst):
        assert "EnvironmentFile" not in einheit
        for k, werte in einheit.items():
            for v in werte:
                assert not re.search(r"(key|token|secret|passw)", f"{k}={v}", re.IGNORECASE), k
