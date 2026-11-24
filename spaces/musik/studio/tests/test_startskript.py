"""Startskript der drei PC-Dienste: liest das Skript nur als Text (startet nichts)."""
import re
from pathlib import Path

SKRIPT = Path(__file__).resolve().parents[1] / "scripts" / "musik-dienste-starten.ps1"


def _text() -> str:
    return SKRIPT.read_text(encoding="utf-8")


def test_startskript_nennt_drei_dienste_und_keine_adressen():
    t = _text()
    for name, port in (("musik_dienst", "8148"), ("musik_shim", "8152"), ("musik_telegram", "8142")):
        assert name in t
        assert port in t
    # 8139 und 8141 gehoeren dem Learning-Claw.
    assert "8139" not in t
    assert "8141" not in t
    assert "192.168." not in t
    assert not re.search(r"(?<!\d)100\.\d", t)
    assert "localhost" not in t.lower()
    assert not re.search(r"[A-Za-z]:\\Users\\", t), "kein fester Benutzerpfad im oeffentlichen Repo"
    assert "Start-Process" in t
    assert "2>&1 |" not in t


def test_startskript_umgebung_und_logs():
    t = _text()
    assert "-WindowStyle" in t or "WindowStyle" in t
    assert "Hidden" in t
    assert "RedirectStandardOutput" in t and "RedirectStandardError" in t
    for name in ("VIBEMIND_AGENT", "SHIM_EXTRA_MCP_CONFIG", "MCP_TOOL_TIMEOUT", "MUSIK_STUDIO_PORT",
                 "MUSIK_DATEN", "MUSIK_TELEGRAM_LLM_URL", "MUSIK_STUDIO_URL", "MUSIK_TELEGRAM_PORT"):
        assert name in t
    assert "musik-claw" in t and "musik-kette" in t
    assert "-Liste" in t or "$Liste" in t
    assert "$PSScriptRoot" in t


def test_startskript_liest_keine_geheimnisse():
    t = _text()
    for geheim in ("TOKEN", "SCHLUESSEL", "TELEGRAM_CHAT_ID"):
        assert not re.search(rf"\$env:\w*{geheim}", t)
        assert f"'{geheim}" not in t


def test_startskript_erkennt_laufende_dienste_am_programmpfad():
    t = _text()
    assert "ExecutablePath" in t
    assert "OrdinalIgnoreCase" in t and "GetFullPath" in t
    assert re.search(r"--port\\s\+8152", t)
    assert "powershell|pwsh" in t
    assert "$prozessId -eq $PID" in t
    # kein Teilstring-Abgleich mehr ueber die Kommandozeile allein
    assert "-notlike" not in t
    assert "Marke" not in t


def test_startskript_setzt_umgebung_erst_im_try_und_rollt_in_finally_zurueck():
    t = _text()
    start = t.index("Start-Process @start")
    versuch = t.rindex("try {", 0, start)
    setzen = t.index("SetEnvironmentVariable($k, [string]$d.Env[$k], 'Process')")
    assert versuch < setzen < start
    assert "} finally {" in t
    ende = t.index("} finally {", start)
    zurueck = t.index("SetEnvironmentVariable($k, $vorher[$k], 'Process')", ende)
    assert zurueck > ende
