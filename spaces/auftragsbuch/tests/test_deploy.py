"""Statische Tests der Ausroll-Dateien des Auftragsbuchs (kein Docker, kein Netz).

Muster: spaces/event_management/tests/system/test_ausrollen.py. Geprueft wird nur der Text der
Dateien: anbinden.sh liest nur von stdin und gibt nichts aus, Compose bindet nur an Loopback und
reicht Geheimnisse nur als Dateien, nichts Privates steht im Ordner, das OpenFang-Beispiel ist
ein stdio-Eintrag ohne Schluessel.
"""
from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest
import yaml

DIENST = Path(__file__).resolve().parents[1]
ANBINDEN = DIENST / "deploy" / "anbinden.sh"
COMPOSE = DIENST / "docker-compose.yml"
RUNBOOK = DIENST / "deploy" / "ausrollen.md"
EINTRAG = DIENST / "deploy" / "openfang-eintrag.beispiel.toml"

_VARIABLE = re.compile(r"\$\{?(?:dsn|schluessel)\b")
# Erlaubt: printf '%s' "$var" in eine Datei unter laufzeit/ oder per Pipe in sha256sum.
_ERLAUBT = re.compile(
    r"""\bprintf '%s(?:\\n)?' "\$\{?(?:dsn|schluessel)\}?" *(?:\| *sha256sum\b|> *laufzeit/)"""
)


def _gibt_wert_aus(zeile: str) -> bool:
    """Wahr, wenn eine Zeile DSN oder Schluessel ausgeben koennte.

    Kommentare zaehlen nicht. `set -x` ist immer verdaechtig. Eine Zeile, die eine der beiden
    Variablen nennt, darf kein `echo`, `tee`, `logger`, `>&` und kein Here-Doc/-String enthalten;
    `printf` ist nur in der erlaubten Form (siehe _ERLAUBT) in Ordnung.
    """
    code = "" if zeile.lstrip().startswith("#") else zeile
    if re.search(r"\bset\s+-[a-z]*x", code):
        return True
    if not _VARIABLE.search(code):
        return False
    if re.search(r"\becho\b|>&|\btee\b|\blogger\b|<<", code):
        return True
    if re.search(r"\bprintf\b", code):
        return _ERLAUBT.search(code) is None
    return False


def _zeilen(pfad: Path) -> list[str]:
    return pfad.read_text(encoding="utf-8").splitlines()


def test_pruefer_unterscheidet_ausgabe_von_pipe_und_datei() -> None:
    gut = [
        """printf '%s\\n' "$dsn" > laufzeit/dsn""",
        """printf '%s' "$schluessel" | sha256sum | cut -d' ' -f1 > laufzeit/schluessel-hash""",
        """[ -n "$dsn" ] && [ -n "$schluessel" ] || exit 2""",
        """# echo "$dsn" steht nur im Kommentar""",
    ]
    schlecht = [
        'echo "$dsn"',
        """printf '%s\\n' "$schluessel\"""",
        """printf '%s\\n' "$dsn" >&2""",
        """printf '%s' "$schluessel" | tee x""",
        'sha256sum <<< "$schluessel"',
        "set -x",
    ]
    assert [z for z in gut if _gibt_wert_aus(z)] == []
    assert [z for z in schlecht if _gibt_wert_aus(z)] == schlecht


def test_anbinden_nur_stdin_und_ohne_ausgabe() -> None:
    text = ANBINDEN.read_text(encoding="utf-8")
    assert text.startswith("#!")
    assert "umask 077" in text
    assert "read -r" in text
    assert "mkdir" in text and "chmod 700" in text
    assert "sha256sum" in text
    assert "docker cp" not in text
    assert not re.search(r"\$\{?[1-9@*#]", text), "keine Argumente aus der Kommandozeile"
    assert [z for z in _zeilen(ANBINDEN) if _gibt_wert_aus(z)] == []


def test_anbinden_schreibt_beide_dateien() -> None:
    text = ANBINDEN.read_text(encoding="utf-8")
    assert re.search(r"> *laufzeit/dsn\b", text)
    assert re.search(r"> *laufzeit/schluessel-hash\b", text)


@pytest.fixture(scope="module")
def compose() -> dict:
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))


def test_compose_nur_loopback_und_fester_name(compose: dict) -> None:
    assert compose["name"] == "auftragsbuch"
    assert list(compose["services"]) == ["auftragsbuch"]
    dienst = compose["services"]["auftragsbuch"]
    ports = dienst["ports"]
    assert ports and all(str(p).startswith("127.0.0.1:") for p in ports)
    assert "${AUFTRAGSBUCH_TAG:?" in dienst["image"]
    assert dienst["restart"] == "unless-stopped"
    assert dienst["mem_limit"] == "256m"


def test_compose_geheimnisse_nur_als_datei(compose: dict) -> None:
    dienst = compose["services"]["auftragsbuch"]
    env = dienst.get("environment", {})
    for name in env:
        if re.search(r"DSN|SCHLUESSEL|PASSWORT|TOKEN", name):
            assert name.endswith("_FILE"), name
    assert env["AUFTRAGSBUCH_DSN_FILE"].startswith("/run/secrets/")
    assert env["AUFTRAGSBUCH_SCHLUESSEL_HASH_FILE"].startswith("/run/secrets/")
    # Beide Dateien kommen schreibgeschuetzt aus ./laufzeit/.
    mounts = [v for v in dienst["volumes"] if str(v).startswith("./laufzeit/")]
    assert len(mounts) == 2 and all(str(v).endswith(":ro") for v in mounts)
    assert dienst["env_file"] == ["./laufzeit/werte.env"]


def test_compose_haengt_im_externen_datenbanknetz(compose: dict) -> None:
    netz = compose["networks"]["datenbank"]
    assert netz["external"] is True and netz["name"] == "debian_default"
    assert compose["services"]["auftragsbuch"]["networks"] == ["datenbank"]


def test_gitignore_haelt_laufzeit_fern() -> None:
    assert "laufzeit/" in _zeilen(DIENST / ".gitignore")


def test_keine_privaten_werte() -> None:
    ipv4 = re.compile(r"(?<![\w.])\d{1,3}(?:\.\d{1,3}){3}(?![\w.])")
    erlaubt = {"127.0.0.1", "0.0.0.0"}
    gelesen = 0
    for pfad in sorted(DIENST.rglob("*")):
        teile = pfad.relative_to(DIENST).parts
        if not pfad.is_file() or {"tests", "__pycache__", "laufzeit"} & set(teile):
            continue
        try:
            text = pfad.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        gelesen += 1
        assert "ts.net" not in text, pfad.name
        fremd = [a for a in ipv4.findall(text) if a not in erlaubt]
        assert fremd == [], f"{pfad.name}: {fremd}"
    assert gelesen >= 8


def test_beispiel_eintrag_ist_stdio_ohne_schluessel() -> None:
    text = EINTRAG.read_text(encoding="utf-8")
    daten = tomllib.loads(text)
    (eintrag,) = daten["mcp_servers"]
    assert eintrag["name"] == "auftragsbuch"
    transport = eintrag["transport"]
    assert transport["type"] == "stdio"
    args = transport["args"]
    assert "--env" in args
    assert "AUFTRAGSBUCH_URL" in args and "AUFTRAGSBUCH_SCHLUESSEL" in args
    assert args[-1].endswith("bruecke.py")
    assert "headers" not in eintrag and "auth_headers" not in eintrag
    assert "headers" not in transport
    assert "http://" not in text and "https://" not in text


_JA = "**Eigenes Ja des Betreibers:**"
def test_runbook_jeder_compose_befehl_setzt_den_tag() -> None:
    text = RUNBOOK.read_text(encoding="utf-8")
    befehle = re.findall(r"`([^`]*docker compose[^`]*)`", text)
    assert len(befehle) >= 4
    for befehl in befehle:
        assert befehl.startswith("AUFTRAGSBUCH_TAG="), befehl


# Regel fuer die ganze Datei: Was auf dem Mini-PC, an der Datenbank, an OpenFang, am Betriebs-
# Checkout, am Gehirn oder an `master` etwas aendert, startet oder stoppt, braucht im selben
# Punkt den Marker. Lesende Schritte (Probe in ROLLBACK, `\ddp`, Rauchproben) sind frei.
_EINGRIFF = re.compile(
    r"docker compose[^`]*\b(?:up|down|stop|start)\b|einspielen|\bDROP\b|ALTER ROLE|Passwort"
    r"|auftrag_probe|tools/call|merge|checkout|neu starten|Neustart|brain-core|openfang\.vibemind\.toml|OpenFang-Datei"
    r"|~/\.openfang/agents|tailscale serve|anbinden\.sh|werte\.env|wiederherstellen|entfern",
    re.IGNORECASE,
)


def _punkte(zeilen: list[str]) -> list[str]:
    """Zerlegt die Stufen des Runbooks in nummerierte und Spiegelstrich-Punkte samt eingerueckter Folgezeilen."""
    start = next(i for i, z in enumerate(zeilen) if z.startswith("## Stufe 1"))
    punkte: list[str] = []
    offen = False
    for zeile in zeilen[start:]:
        if re.match(r"\d+\. |- ", zeile):
            punkte.append(zeile)
            offen = True
        elif zeile.startswith(" ") and offen:
            punkte[-1] += "\n" + zeile
        else:
            offen = False  # Leerzeile, Ueberschrift oder Absatz beendet den Punkt
    return punkte


_GEDECKT = "ist vom Ja zum Einspielen mit abgedeckt"  # Pflichtsatz der Sicherung und der Probe in ROLLBACK
_NICHTS = "legt nichts an"  # Pflichtsatz der Rauchprobe (nur lesend, erwarteter Fehlschlag)


def test_runbook_regel_jeder_eingreifende_punkt_traegt_das_ja() -> None:
    punkte = _punkte(_zeilen(RUNBOOK))
    gedeckt = [p for p in punkte if _GEDECKT in p]
    rauch = [p for p in punkte if _NICHTS in p]
    # Genau die Sicherung und die Probe in ROLLBACK sind ueber ihren Pflichtsatz ausgenommen,
    # genau die Rauchprobe darf Aufrufe ohne Marker nennen, und sie fuehrt nichts aus, was anlegt.
    assert len(gedeckt) == 2 and all("ändert nichts an der Datenbank" in p for p in gedeckt)
    assert any("ROLLBACK" in p for p in gedeckt) and any("Sicherung" in p for p in gedeckt)
    assert len(rauch) == 1 and "tools/call `auftrag_probe`" not in rauch[0] and _JA not in rauch[0]
    frei = gedeckt + rauch
    fehlend = [p.splitlines()[0][:70] for p in punkte
               if _EINGRIFF.search(p) and _JA not in p and not any(p is f for f in frei)]
    assert fehlend == []
    # Mindestzahlen: ein leerer oder zerlegter Text darf den Test nicht stillschweigend bestehen lassen.
    assert len(punkte) >= 32
    assert len([p for p in punkte if _JA in p]) >= 24


def test_runbook_abschluss_ist_eine_nummerierte_liste_mit_marker_an_der_probe() -> None:
    zeilen = _zeilen(RUNBOOK)
    start = next(i for i, z in enumerate(zeilen) if z.startswith("## Abschluss"))
    punkte = [z for z in zeilen[start:] if re.match(r"\d+\. ", z)]
    assert len(punkte) >= 4
    probe = next(z for z in zeilen[start:] if "auftrag_probe" in z)
    assert re.match(r"\d+\. ", probe) and _JA in probe


def test_runbook_rueckweg_ist_eine_liste_mit_einer_aktion_je_punkt() -> None:
    zeilen = _zeilen(RUNBOOK)
    koepfe = [i for i, z in enumerate(zeilen) if z.startswith("**Rückweg")]
    assert len(koepfe) == 3
    for i in koepfe:
        assert zeilen[i] == "**Rückweg:**"
        assert zeilen[i + 1] == "" and zeilen[i + 2].startswith("- ")
