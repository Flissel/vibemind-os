import json
import subprocess
import sys
from pathlib import Path

from scripts.schritt_waechter_bericht import bericht

SKRIPT = Path(__file__).resolve().parents[1] / "scripts" / "schritt_waechter_bericht.py"


def test_bericht_zaehlt_und_zieht_stichprobe():
    zeilen = ([{"capability": "a", "auffaellig": True, "quelle": "regel", "grund": "x"}] * 60
              + [{"capability": "b", "auffaellig": True, "quelle": "regel", "grund": "y"}] * 5
              + [{"capability": "b", "auffaellig": None, "quelle": "keine", "grund": ""}] * 35)
    b = bericht(zeilen)
    assert (b["schritte"], b["auffaellig"], b["unbeurteilt"]) == (100, 65, 35)
    assert b["je_quelle"] == {"regel": 65, "keine": 35} and b["je_faehigkeit"] == {"a": [60, 60], "b": [40, 5]}
    assert len(b["stichprobe"]) == 40 and sum(z["capability"] == "b" for z in b["stichprobe"]) == 5
    assert bericht([]) == {"schritte": 0, "auffaellig": 0, "unbeurteilt": 0, "je_quelle": {},
                           "je_faehigkeit": {}, "stichprobe": []}


def test_capability_none_wird_unter_fragezeichen_gezaehlt():
    zeilen = [{"capability": None, "auffaellig": True, "quelle": "regel", "grund": "x"},
              {"capability": None, "auffaellig": False, "quelle": "regel", "grund": ""}]
    b = bericht(zeilen)
    assert b["je_faehigkeit"] == {"?": [2, 1]}
    assert len(b["stichprobe"]) == 1


def test_stichprobe_ist_reihum_und_deterministisch():
    zeilen = ([{"capability": "a", "auffaellig": True, "quelle": "regel", "grund": f"a{i}"} for i in range(3)]
              + [{"capability": "b", "auffaellig": True, "quelle": "regel", "grund": f"b{i}"} for i in range(2)])
    b = bericht(zeilen)
    assert [z["grund"] for z in b["stichprobe"]] == ["a0", "b0", "a1", "b1", "a2"]
    assert bericht(zeilen) == b


def test_kommandozeile_zaehlt_defekte_zeilen(tmp_path):
    datei = tmp_path / "schatten.jsonl"
    gut = {"capability": "a", "auffaellig": True, "quelle": "regel", "grund": "x"}
    datei.write_text(json.dumps(gut) + "\n{kaputt\n", encoding="utf-8")
    p = subprocess.run([sys.executable, str(SKRIPT), str(datei)], capture_output=True, text=True,
                       encoding="utf-8")
    assert p.returncode == 0
    assert '"defekt": 1' in p.stdout
    assert json.loads(p.stdout)["schritte"] == 1


def test_kommandozeile_ohne_argument_gibt_hilfe_und_code_2():
    p = subprocess.run([sys.executable, str(SKRIPT)], capture_output=True, text=True, encoding="utf-8")
    assert p.returncode == 2
    assert "Aufruf" in (p.stdout + p.stderr)
