"""Tests fuer die naechtliche Wissens-Pruefung (lose Notizen und tote Verbindungen im Rowboat-Graph)."""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "wissens_pruefung"))

import pruefung  # noqa: E402

JETZT = datetime(2026, 11, 8, 3, 15, tzinfo=timezone.utc)


def _notiz(wissen: Path, rel: str, text: str) -> None:
    p = wissen / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def _wissen(tmp_path: Path) -> Path:
    w = tmp_path / "knowledge"
    _notiz(w, "Topics/A.md", "# A\n[[Topics/B]] und [[Topics/Fehlt]]\n")
    _notiz(w, "Topics/B.md", "---\nparent: \"[[Topics/A]]\"\n---\n# B\n")
    _notiz(w, "Topics/Allein.md", "# allein\n")
    _notiz(w, "companys/VibeMind/Kontakte/bAV/X.md",
           "---\nerzeugt_von: rowboat-geschaeftsdaten\n---\n# X\n[[People/Max Muster]]\n")
    _notiz(w, "Agents/k.md", "[[topics/a]] [[A]] [[Topics/A#Kopf|Alias]]\n")
    _notiz(w, "Meetings/m.md", "[[Topics/Allein]]\n")  # Rowboat zeigt Meetings nicht im Graph
    _notiz(w, ".git/x.md", "[[Topics/Allein]]\n")
    _notiz(w, "Bilder/logo.png", "binaer")
    return w


def test_link_ziel_wie_rowboat():
    assert pruefung.link_ziel("Topics/A") == "Topics/A.md"
    assert pruefung.link_ziel(" Topics/A#Kopf|Alias ") == "Topics/A.md"
    assert pruefung.link_ziel("/knowledge/Topics/A.md") == "Topics/A.md"
    assert pruefung.link_ziel("../x") is None and pruefung.link_ziel("Ordner/") is None
    assert pruefung.link_ziel("Bilder/logo.png") is None  # keine Notiz, zaehlt nicht als tot


def test_kennzahlen_nur_exakte_pfade_sind_kanten(tmp_path):
    k = pruefung.kennzahlen(_wissen(tmp_path))
    assert k.notizen == 5  # ohne Meetings, ohne .git, nur .md
    assert k.kanten == 2  # A-B (Text und parent zaehlen als eine Kante) und k-A (Anker und Alias)
    assert k.lose == 2  # Allein, X; [[topics/a]] und [[A]] ergeben in Rowboat keine Kante
    assert k.tote == 4 and k.tote_ziele["Topics/Fehlt.md"] == 1 and k.tote_ziele["A.md"] == 1
    assert k.inseln == 3 and k.groesste_insel == 3


def test_gruppen_und_quellen(tmp_path):
    k = pruefung.kennzahlen(_wissen(tmp_path))
    assert k.je_gruppe["Topics"] == (1, 3)
    assert k.je_gruppe["companys/VibeMind/Kontakte"] == (1, 1)
    assert k.je_quelle["rowboat-geschaeftsdaten"] == (1, 1)
    assert k.je_quelle["(ohne Angabe)"] == (1, 4)
    assert k.je_gruppe["Agents"] == (0, 1)


def test_notiz_ohne_personendaten_und_ohne_links_in_tabellen(tmp_path):
    k = pruefung.kennzahlen(_wissen(tmp_path))
    text = pruefung.notiz(k, [], JETZT)
    assert "Max Muster" not in text  # tote Ziele unter People/ und Kontakte/ werden nur gezaehlt
    assert "Topics/Fehlt.md" in text
    for zeile in text.splitlines():
        if zeile.startswith("|"):
            assert "[[" not in zeile
    assert text.startswith("---\nerzeugt_von: wissens-pruefung\n")
    assert "live" not in text.split("\n---\n", 1)[0] and "@rowboat" not in text.lower()
    assert "| Lose Notizen | 2 (40 %) |" in text


def test_verlauf_mit_vortag(tmp_path):
    k = pruefung.kennzahlen(_wissen(tmp_path))
    vorher = [{"zeit": "2026-11-07T03:15:00+00:00", "notizen": 4, "lose": 4, "tote": 7, "inseln": 4}]
    text = pruefung.notiz(k, vorher, JETZT)
    assert "| Lose Notizen | 2 (40 %) | 4 |" in text
    assert "| 07.11.2026 | 4 | 4 | 7 |" in text


def test_lauf_schreibt_notiz_und_verlauf(tmp_path):
    wissen = _wissen(tmp_path)
    stand = tmp_path / "stand"
    assert pruefung.main(["--wissen", str(wissen), "--stand", str(stand)], jetzt=JETZT) == 0
    text = (wissen / pruefung.ZIEL).read_text(encoding="utf-8")
    assert "# Wissens-Gesundheit" in text
    verlauf = json.loads((stand / "verlauf.json").read_text(encoding="utf-8"))
    assert verlauf[-1]["lose"] == 2
    # die eigene Notiz zaehlt beim naechsten Lauf mit, die Kennzahlen bleiben stabil genug
    assert pruefung.main(["--wissen", str(wissen), "--stand", str(stand)], jetzt=JETZT) == 0
    assert len(json.loads((stand / "verlauf.json").read_text(encoding="utf-8"))) == 2


def test_verlauf_hoechstens_60_eintraege(tmp_path):
    wissen = _wissen(tmp_path)
    stand = tmp_path / "stand"
    stand.mkdir()
    alt = [{"zeit": f"2026-09-{(i % 28) + 1:02d}T03:15:00+00:00", "notizen": 1, "lose": 1, "tote": 0, "inseln": 1}
           for i in range(70)]
    (stand / "verlauf.json").write_text(json.dumps(alt), encoding="utf-8")
    pruefung.main(["--wissen", str(wissen), "--stand", str(stand)], jetzt=JETZT)
    assert len(json.loads((stand / "verlauf.json").read_text(encoding="utf-8"))) == 60


def test_trocken_schreibt_nichts(tmp_path, capsys):
    wissen = _wissen(tmp_path)
    stand = tmp_path / "stand"
    assert pruefung.main(["--wissen", str(wissen), "--stand", str(stand), "--trocken"], jetzt=JETZT) == 0
    assert not (wissen / pruefung.ZIEL).exists() and not stand.exists()
    assert "Lose Notizen" in capsys.readouterr().out


def test_fehlendes_wissen_exit_2(tmp_path):
    assert pruefung.main(["--wissen", str(tmp_path / "nichts"), "--stand", str(tmp_path / "s")], jetzt=JETZT) == 2


def _einheit(name: str) -> dict[str, list[str]]:
    werte: dict[str, list[str]] = {}
    for zeile in (Path(pruefung.__file__).parent / "deploy" / name).read_text(encoding="utf-8").splitlines():
        zeile = zeile.strip()
        if zeile and not zeile.startswith(("#", ";", "[")) and "=" in zeile:
            k, _, v = zeile.partition("=")
            werte.setdefault(k.strip(), []).append(v.strip())
    return werte


def test_systemd_einheiten():
    timer = _einheit("rowboat-wissens-pruefung.timer")
    assert timer["OnCalendar"] == ["*-*-* 03:15:00 Europe/Berlin"] and timer["Persistent"] == ["true"]
    dienst = _einheit("rowboat-wissens-pruefung.service")
    assert dienst["ExecStart"] == ["/usr/bin/python3 /home/debian/marketing-os/spaces/rowboat/wissens_pruefung/pruefung.py"]
    assert dienst["User"] == ["debian"] and dienst["Type"] == ["oneshot"] and dienst["NoNewPrivileges"] == ["yes"]
    assert "EnvironmentFile" not in dienst
