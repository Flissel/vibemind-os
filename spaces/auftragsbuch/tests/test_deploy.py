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
