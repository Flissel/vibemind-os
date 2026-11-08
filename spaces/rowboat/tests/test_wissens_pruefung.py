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


def _karte(datei: Path, notes: dict, version: int = 1) -> Path:
    datei.parent.mkdir(parents=True, exist_ok=True)
    datei.write_text(json.dumps({"version": version, "notes": notes}), encoding="utf-8")
    return datei


def test_karte_lesen_wie_rowboat(tmp_path):
    gut = _karte(tmp_path / "a.json", {
        "Topics/Allein": {"level": 3, "parent": "Topics/A"},
        "Topics/OhneEbene": {"parent": "Topics/A"},           # ohne ganzzahlige Ebene: Rowboat ignoriert den Eintrag
        "Topics/Wahr": {"level": True, "parent": "Topics/A"},  # bool ist keine Ebene
        "Topics/OhneParent": {"level": 2},
    })
    assert pruefung.karte_lesen(gut) == {"Topics/Allein.md": "Topics/A.md"}
    assert pruefung.karte_lesen(_karte(tmp_path / "b.json", {"X": {"level": 1, "parent": "Y"}}, version=2)) == {}
    kaputt = tmp_path / "c.json"
    kaputt.write_text("{nicht json", encoding="utf-8")
    assert pruefung.karte_lesen(kaputt) == {}
    assert pruefung.karte_lesen(tmp_path / "fehlt.json") == {}


def test_karte_gibt_eltern_nur_ohne_parent_im_kopf(tmp_path):
    w = _wissen(tmp_path)
    _notiz(w, "Areas/Topics.md", "# Bereich\n")
    _notiz(w, "Topics/MitTotemParent.md", "---\nparent: \"[[Topics/Gibtsnicht]]\"\n---\n# x\n")
    karte = {
        "Topics/Allein.md": "Areas/Topics.md",          # ohne parent im Kopf: Linie ueber die Karte
        "Topics/MitTotemParent.md": "Areas/Topics.md",  # parent im Kopf gewinnt, auch wenn das Ziel fehlt
        "Topics/B.md": "Areas/Topics.md",               # hat parent im Kopf: Karte zaehlt nicht
        "Agents/k.md": "Areas/Fehlt.md",                # Eltern-Notiz fehlt: keine Linie
    }
    k = pruefung.kennzahlen(w, karte)
    assert k.karte_kanten == 1
    assert k.lose == 2  # X und MitTotemParent; Allein und Areas/Topics haengen jetzt aneinander
    assert k.karte_fehlt == 3  # ohne parent im Kopf und ohne Eintrag in der Karte: A, X, Areas/Topics


def test_lauf_liest_karte_neben_dem_wissen(tmp_path):
    wissen = _wissen(tmp_path)
    _notiz(wissen, "Areas/Topics.md", "# Bereich\n")
    _karte(tmp_path / "config" / "vault-map.json", {"Topics/Allein": {"level": 3, "parent": "Areas/Topics"}})
    assert pruefung.main(["--wissen", str(wissen), "--stand", str(tmp_path / "s")], jetzt=JETZT) == 0
    verlauf = json.loads((tmp_path / "s" / "verlauf.json").read_text(encoding="utf-8"))
    assert verlauf[-1]["lose"] == 1 and verlauf[-1]["karte_kanten"] == 1
    text = (wissen / pruefung.ZIEL).read_text(encoding="utf-8")
    assert "| Linien über die Ordnungs-Karte | 1 |" in text


def test_karte_erneuern_schreibt_karte_und_eine_zeile(tmp_path, capsys):
    import karte

    wd = tmp_path / "rowboat-data"
    _notiz(wd / "knowledge", "Agents/neu (abc123).md", "# neu\n")
    _notiz(wd / "knowledge", "Irgendwo/ohne Regel.md", "# x\n")
    assert karte.main(["--workdir", str(wd)]) == 0
    gelesen = pruefung.karte_lesen(wd / "config" / "vault-map.json")
    assert "Agents/neu (abc123).md" in gelesen
    ausgabe = capsys.readouterr()
    zeilen = [z for z in (ausgabe.out + ausgabe.err).splitlines() if z.strip()]
    assert len(zeilen) == 1 and zeilen[0].startswith("Karte: ") and "erneuert" in zeilen[0]
    assert karte.main(["--workdir", str(wd)]) == 0
    assert "unveraendert" in capsys.readouterr().out


def test_karte_ohne_wissen_exit_2(tmp_path):
    import karte

    assert karte.main(["--workdir", str(tmp_path / "leer")]) == 2
    (tmp_path / "x" / "knowledge").mkdir(parents=True)
    assert karte.main(["--workdir", str(tmp_path / "x"), "--profil", "gibtsnicht"]) == 2


def test_systemd_einheiten_karte():
    timer = _einheit("rowboat-vault-map.timer")
    assert timer["OnCalendar"] == ["*:0/10"] and timer["Unit"] == ["rowboat-vault-map.service"]
    dienst = _einheit("rowboat-vault-map.service")
    assert dienst["ExecStart"] == ["/usr/bin/python3 /home/debian/marketing-os/spaces/rowboat/wissens_pruefung/karte.py"]
    assert dienst["User"] == ["debian"] and dienst["Type"] == ["oneshot"] and dienst["NoNewPrivileges"] == ["yes"]
    assert "EnvironmentFile" not in dienst


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
