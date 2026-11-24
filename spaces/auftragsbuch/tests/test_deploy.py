"""Statische Tests der Ausroll-Dateien des Auftragsbuchs (kein Docker, kein Netz).

Muster: spaces/event_management/tests/system/test_ausrollen.py. Geprueft wird nur der Text der
Dateien: anbinden.sh und db_zugang.sh lesen Geheimnisse nur von stdin bzw. erzeugen sie selbst und
geben nichts aus, Compose bindet nur an Loopback und reicht Geheimnisse nur als Dateien, nichts
Privates steht im Ordner, das OpenFang-Beispiel ist ein stdio-Eintrag ohne Schluessel, das Runbook
haelt seine Regeln ein.
"""
from __future__ import annotations

import re
import shlex
import tomllib
from pathlib import Path

import pytest
import yaml

DIENST = Path(__file__).resolve().parents[1]
ANBINDEN = DIENST / "deploy" / "anbinden.sh"
DB_ZUGANG = DIENST / "deploy" / "db_zugang.sh"
COMPOSE = DIENST / "docker-compose.yml"
RUNBOOK = DIENST / "deploy" / "ausrollen.md"
EINTRAG = DIENST / "deploy" / "openfang-eintrag.beispiel.toml"

_VARIABLE = re.compile(r"\$\{?(?:dsn|schluessel|passwort)\b")
# Erlaubt: printf '%s' "$var" in eine Datei unter laufzeit/, per Pipe in sha256sum oder per Pipe in
# die Hilfe deploy/db_zugang.py (die selbst keinen Wert ausgibt).
_ERLAUBT = re.compile(
    r"""\bprintf '%s(?:\\n)?' "\$\{?(?:dsn|schluessel|passwort)\}?" *"""
    r"""(?:\| *sha256sum\b|> *laufzeit/|\| *python3 deploy/db_zugang\.py (?:sql|dsn-schreiben)\b)"""
)


_LEERTEST = re.compile(r"""\[ +-[zn] +"\$\{?(?:dsn|schluessel|passwort)\}?" +\]""")


def _gibt_wert_aus(zeile: str) -> bool:
    """Wahr, wenn eine Zeile DSN, Schluessel oder Passwort ausgeben oder als Argument weitergeben koennte.

    Kommentare zaehlen nicht. `set -x` ist immer verdaechtig. Eine Zeile, die eine der Variablen
    nennt, darf kein `echo`, `tee`, `logger`, `>&` und kein Here-Doc/-String enthalten; `printf`
    ist nur in der erlaubten Form (siehe _ERLAUBT) in Ordnung. Sonst darf die Variable nur in
    einem Leer-Test `[ -z "$var" ]`/`[ -n "$var" ]` stehen (kein Argument eines Programms).
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
    return _VARIABLE.search(_LEERTEST.sub("", code)) is not None


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
        'printf \'%s\\n\' "$passwort" | python3 andere.py',
        'psql -c "ALTER ROLE x PASSWORD \'$passwort\'"',
    ]
    assert [z for z in gut if _gibt_wert_aus(z)] == []
    assert [z for z in schlecht if _gibt_wert_aus(z)] == schlecht


def _code(pfad: Path) -> str:
    """Skripttext ohne Kommentarzeilen."""
    return "\n".join(z for z in _zeilen(pfad) if not z.lstrip().startswith("#"))


@pytest.mark.parametrize("pfad", sorted((DIENST / "deploy").glob("*.sh")), ids=lambda p: p.name)
def test_shell_skripte_ohne_crlf(pfad: Path) -> None:
    roh = pfad.read_bytes()
    assert roh.startswith(b"#!/usr/bin/env bash\n")
    assert b"\r" not in roh


def test_anbinden_nur_eine_zeile_von_stdin_und_ohne_ausgabe() -> None:
    text = ANBINDEN.read_text(encoding="utf-8")
    code = _code(ANBINDEN)
    assert text.startswith("#!")
    assert "umask 077" in code
    assert code.count("read -r") == 1, "genau eine Zeile: der Schluessel"
    assert re.search(r"if \[ -t 0 \]", code), "bricht ab, wenn stdin ein Terminal ist"
    assert re.search(r"if \[ ! -s laufzeit/dsn \]", code), "bricht ab, wenn laufzeit/dsn fehlt"
    assert code.index("[ -t 0 ]") < code.index("read -r")
    assert "chmod 700" in code and "sha256sum" in code
    assert "docker cp" not in text
    assert not re.search(r"\$\{?[1-9@*#]", text), "keine Argumente aus der Kommandozeile"
    assert [z for z in _zeilen(ANBINDEN) if _gibt_wert_aus(z)] == []


def test_anbinden_schreibt_nur_den_hash() -> None:
    code = _code(ANBINDEN)
    assert re.search(r"> *laufzeit/schluessel-hash\b", code)
    assert not re.search(r"> *laufzeit/dsn\b", code), "die DSN schreibt db_zugang.sh"
    assert "$dsn" not in code


def test_db_zugang_erzeugt_passwort_und_gibt_es_nie_aus() -> None:
    text = DB_ZUGANG.read_text(encoding="utf-8")
    code = _code(DB_ZUGANG)
    assert "umask 077" in code
    assert "passwort=$(openssl rand -hex 24)" in code
    assert "read " not in code, "das Passwort entsteht auf dem Mini-PC, nichts kommt von aussen"
    # Einziges Argument: der Name des Zugangs (dienst oder abholer), geprueft von db_zugang.py.
    argumente = set(re.findall(r"\$\{?[1-9@*#][^\s\"]*", text))
    assert argumente == {"${1:-dienst}", "$#"}, argumente
    assert 'python3 deploy/db_zugang.py zugang "$zugang"' in code
    assert 'python3 deploy/db_zugang.py sql "$zugang"' in code
    assert 'python3 deploy/db_zugang.py dsn-schreiben "$zugang"' in code
    assert code.index("db_zugang.py zugang") < code.index("openssl rand"), "erst pruefen, dann aendern"
    assert [z for z in _zeilen(DB_ZUGANG) if _gibt_wert_aus(z)] == []
    # Kein SQL mit Passwort auf einer Kommandozeile: das SQL kommt nur ueber stdin aus der Hilfe.
    assert "password" not in code.lower()
    assert "psql -c" not in code and " -c " not in code
    assert re.search(r"\| *docker exec -i \"\$container\" psql\b", code)
    assert "-v ON_ERROR_STOP=1" in code
    assert "python3 deploy/db_zugang.py sql" in code and "python3 deploy/db_zugang.py dsn-schreiben" in code
    assert code.index("db_zugang.py sql") < code.index("dsn-schreiben"), "erst die Rolle, dann die DSN"
    assert "unset passwort" in code
    assert "chmod 700 laufzeit" in code


@pytest.fixture(scope="module")
def compose() -> dict:
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))


def test_compose_nur_loopback_und_fester_name(compose: dict) -> None:
    assert compose["name"] == "auftragsbuch"
    assert list(compose["services"]) == ["auftragsbuch", "rowboat-abholer"]
    dienst = compose["services"]["auftragsbuch"]
    # Host-Netz (Rowboat lauscht nur auf 127.0.0.1 des Mini-PC): der Dienst bindet selbst nur Loopback.
    assert dienst["environment"]["AUFTRAGSBUCH_HOST"] == "127.0.0.1"
    assert dienst["environment"]["AUFTRAGSBUCH_PORT"] == "8172"
    assert "${AUFTRAGSBUCH_TAG:?" in dienst["image"]
    assert dienst["restart"] == "unless-stopped"
    assert dienst["mem_limit"] == "256m"


def test_compose_geheimnisse_nur_als_datei(compose: dict) -> None:
    for name, dienst in compose["services"].items():
        env = dienst.get("environment", {})
        for var in env:
            if re.search(r"DSN|SCHLUESSEL|PASSWORT|TOKEN|KEY", var):
                assert var.endswith(("_FILE", "_DATEI")), (name, var)
                assert env[var].startswith("/run/secrets/"), (name, var)
        mounts = [str(v) for v in dienst["volumes"]]
        assert mounts and all(v.startswith("./laufzeit/") and v.endswith(":ro") for v in mounts), name
        assert dienst["env_file"] == ["./laufzeit/werte.env"], name
    env = compose["services"]["auftragsbuch"]["environment"]
    assert env["AUFTRAGSBUCH_DSN_FILE"] == "/run/secrets/auftragsbuch-dsn"
    assert env["AUFTRAGSBUCH_SCHLUESSEL_HASH_FILE"] == "/run/secrets/auftragsbuch-schluessel-hash"
    assert env["AUFTRAGSBUCH_ROWBOAT_SCHLUESSEL_DATEI"] == "/run/secrets/auftragsbuch-rowboat-schluessel"
    assert len(compose["services"]["auftragsbuch"]["volumes"]) == 3
    abholer = compose["services"]["rowboat-abholer"]["environment"]
    assert abholer["ABHOLER_DSN_FILE"] == "/run/secrets/abholer-dsn"
    assert abholer["ROWBOAT_SCHLUESSEL_DATEI"] == "/run/secrets/abholer-rowboat-schluessel"
    assert sorted(str(v) for v in compose["services"]["rowboat-abholer"]["volumes"]) == [
        "./laufzeit/abholer-dsn:/run/secrets/abholer-dsn:ro",
        "./laufzeit/rowboat-abholer-schluessel:/run/secrets/abholer-rowboat-schluessel:ro",
    ]


def test_compose_beide_im_host_netz_ohne_ports(compose: dict) -> None:
    assert "networks" not in compose
    for name, dienst in compose["services"].items():
        assert dienst["network_mode"] == "host", name
        assert "ports" not in dienst and "networks" not in dienst, name
    assert compose["services"]["auftragsbuch"]["environment"]["AUFTRAGSBUCH_ROWBOAT_URL"] == "http://127.0.0.1:3220"
    assert compose["services"]["rowboat-abholer"]["environment"]["ROWBOAT_RPC_URL"] == "http://127.0.0.1:3220"


def test_compose_abholer_aus_demselben_abbild(compose: dict) -> None:
    dienst, abholer = compose["services"]["auftragsbuch"], compose["services"]["rowboat-abholer"]
    assert abholer["image"] == dienst["image"] and "build" not in abholer
    assert abholer["pull_policy"] == "never"
    assert abholer["command"] == ["python", "-m", "spaces.auftragsbuch.rowboat.abholer"]
    assert abholer["container_name"] == "rowboat-abholer"
    assert abholer["restart"] == "unless-stopped" and abholer["init"] is True
    assert abholer["user"] == dienst["user"]
    assert abholer["healthcheck"]["test"] == ["CMD", "python", "-m", "spaces.auftragsbuch.rowboat.abholer",
                                              "--gesund"]
    assert "ABHOLER_ARTEN_FREI" not in abholer["environment"], "Vorgabe vorschlaege_anlegen aus dem Code"


def test_abbild_enthaelt_rowboat_und_bindet_nur_loopback() -> None:
    dockerfile = (DIENST / "Dockerfile").read_text(encoding="utf-8")
    assert "COPY spaces/auftragsbuch/rowboat/*.py /app/spaces/auftragsbuch/rowboat/" in dockerfile
    assert "0.0.0.0" not in dockerfile
    assert "AUFTRAGSBUCH_HOST=127.0.0.1" in dockerfile
    ignore = _zeilen(DIENST / "Dockerfile.dockerignore")
    assert "!spaces/auftragsbuch/rowboat/*.py" in ignore


def test_gitignore_haelt_laufzeit_und_compose_env_fern() -> None:
    zeilen = _zeilen(DIENST / ".gitignore")
    assert "laufzeit/" in zeilen
    assert ".env" in zeilen  # AUFTRAGSBUCH_UID/GID fuer Compose, nur auf dem Mini-PC


def test_werte_beispiel_nennt_alle_werte_mit_platzhaltern() -> None:
    werte = {}
    for zeile in _zeilen(DIENST / "deploy" / "werte.beispiel.env"):
        if zeile and not zeile.startswith("#"):
            name, wert = zeile.split("=", 1)
            werte[name] = wert
    assert set(werte) == {"AUFTRAGSBUCH_LADEN", "AUFTRAGSBUCH_MANDANT", "AUFTRAGSBUCH_ZUSATZ_HOSTS",
                          "AUFTRAGSBUCH_DB_CONTAINER", "AUFTRAGSBUCH_DB_HOST", "AUFTRAGSBUCH_DB_PORT",
                          "AUFTRAGSBUCH_DB_NAME", "ABHOLER_LADEN"}
    assert all(re.fullmatch(r"<[A-Z-]+>", w) for w in werte.values()), werte


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
    for name in ("AUFTRAGSBUCH_URL", "AUFTRAGSBUCH_SCHLUESSEL", "AUFTRAGSBUCH_CA_DATEI"):
        assert args[args.index(name) - 1] == "--env", name
    assert args[-1].endswith("bruecke.py")
    assert "headers" not in eintrag and "auth_headers" not in eintrag
    assert "headers" not in transport
    assert "http://" not in text and "https://" not in text


_JA = "**Eigenes Ja des Betreibers:**"


def test_runbook_jeder_compose_befehl_setzt_den_tag() -> None:
    text = _text()
    befehle = re.findall(r"`([^`]*docker compose[^`]*)`", text)
    assert len(befehle) >= 6
    for befehl in befehle:
        assert befehl.startswith("AUFTRAGSBUCH_TAG="), befehl


def _flach(text: str) -> str:
    """Fasst Zeilenumbrueche und Einrueckung zu je einem Leerzeichen zusammen (Saetze laufen ueber Zeilen)."""
    return " ".join(text.split())


def _text() -> str:
    return _flach(RUNBOOK.read_text(encoding="utf-8"))


def _punkte(zeilen: list[str], ab: str = "## Stufe 1", bis: str = "## Offene Punkte") -> list[str]:
    """Zerlegt das Runbook ab `ab` in nummerierte und Spiegelstrich-Punkte samt eingerueckter Folgezeilen.

    Jeder Punkt kommt flach zurueck (siehe _flach). Schluss ist `bis`, als Vorgabe der Abschnitt
    `## Offene Punkte`: Er beschreibt keine Handlung.
    """
    start = next(i for i, z in enumerate(zeilen) if z.startswith(ab))
    punkte: list[str] = []
    offen = False
    for zeile in zeilen[start + 1:]:
        if zeile.startswith(bis):
            break
        if re.match(r"\d+\. |- ", zeile):
            punkte.append(zeile)
            offen = True
        elif zeile.startswith(" ") and offen:
            punkte[-1] += "\n" + zeile
        else:
            offen = False  # Leerzeile, Ueberschrift oder Absatz beendet den Punkt
    return [_flach(p) for p in punkte]


# Regel fuer die ganze Datei: Jeder Punkt (Schritt oder Aktion eines Rueckwegs) traegt entweder den
# Marker oder genau einen der Pflichtsaetze fuer Ausnahmen. Ein Wort im Text macht keine Ausnahme.
_LESEND = "Dieser Schritt liest nur und ändert nichts."
_RAUCH = "Sie legt nichts an:"  # Rauchprobe vom PC: ruft ein Werkzeug auf, das ablehnen muss
_GEDECKT = "ist vom Ja zur Probe mit abgedeckt"  # Sicherung: schreibt nur eine Datei unter ~/backups/
_BLEIBT = "Dieser Punkt ändert nichts."  # Normalfall im Rueckweg von Stufe 1
_AUSNAHMEN = (_LESEND, _RAUCH, _GEDECKT, _BLEIBT)
# Befehle, die etwas aendern, starten oder stoppen; in einem ausgenommenen Punkt duerfen sie nicht stehen.
_BEFEHL = re.compile(
    r"docker compose[^`]*\b(?:up|down|stop|start)\b|tailscale serve --(?:bg|https=\d+ off)|bash [^`]*deploy/"
    r"|anbinden\.sh|db_zugang|pc_env_setzen|\b(?:DROP|ALTER|GRANT|REVOKE|INSERT|UPDATE)\b|DELETE FROM"
    r"|git (?:-C \S+ )?(?:merge|push|pull|checkout|submodule update)|\b(?:cp|rm|mkdir)\b|tar -x"
    r"|POST /api|DELETE /api|> \.env|einspielen|migration_probe|neu starten"
)


def _ausnahmen(punkt: str) -> list[str]:
    return [s for s in _AUSNAHMEN if s in punkt]


def _kopf(punkt: str) -> str:
    return punkt[:70]


def test_runbook_regel_jeder_punkt_traegt_das_ja_oder_genau_eine_ausnahme() -> None:
    punkte = _punkte(_zeilen(RUNBOOK))
    ohne = [_kopf(p) for p in punkte if _JA not in p and len(_ausnahmen(p)) != 1]
    assert ohne == [], "weder Marker noch genau ein Pflichtsatz"
    doppelt = [_kopf(p) for p in punkte if _JA in p and _ausnahmen(p)]
    assert doppelt == [], "Marker und Pflichtsatz zugleich"
    frei_mit_befehl = [_kopf(p) for p in punkte if _JA not in p and _BEFEHL.search(p)]
    assert frei_mit_befehl == [], "ausgenommener Punkt nennt einen eingreifenden Befehl"
    # Mindestzahlen: ein leerer oder zerlegter Text darf den Test nicht stillschweigend bestehen lassen.
    assert len(punkte) >= 50
    assert len([p for p in punkte if _JA in p]) >= 38
    assert len([p for p in punkte if _LESEND in p]) >= 8


def test_runbook_ausnahmen_stehen_nur_wo_sie_hingehoeren() -> None:
    punkte = _punkte(_zeilen(RUNBOOK))
    (rauch,) = [p for p in punkte if _RAUCH in p]
    assert "tools/call" in rauch and "rauchprobe.py" in rauch and "auftrag_probe" not in rauch
    (gedeckt,) = [p for p in punkte if _GEDECKT in p]
    assert "Sicherung" in gedeckt and "ändert nichts an der Datenbank" in gedeckt and "~/backups/" in gedeckt
    proben = [p for p in punkte if "migration_probe" in p]
    assert len(proben) == 2  # 100 in Stufe 1, 101 in Stufe 4
    assert all(_JA in p and "ROLLBACK" in p and "lock_timeout" in p for p in proben)
    (bleibt,) = [p for p in punkte if _BLEIBT in p]
    assert "Normalfall" in bleibt and "CHECK" in bleibt
    # Jede Aktion des Rueckwegs von Stufe 1 hinter dem Normalfall braucht ihr eigenes Ja.
    stufe1 = punkte[: next(i for i, p in enumerate(punkte) if "Bau-Ordner auf dem" in p)]
    nach = stufe1[stufe1.index(bleibt) + 1:]
    assert len(nach) == 3 and all(_JA in p for p in nach)


def test_runbook_abschluss_ist_eine_nummerierte_liste_mit_marker_an_der_probe() -> None:
    punkte = _punkte(_zeilen(RUNBOOK), ab="## Abschluss", bis="## ")
    assert len(punkte) >= 8 and all(re.match(r"\d+\. ", p) for p in punkte)
    probe = next(p for p in punkte if "auftrag_probe" in p)
    assert _JA in probe
    assert all(_JA in p for p in punkte if "docker compose" in p or "direkt an OpenFang" in p)


def test_runbook_rueckweg_ist_eine_liste_mit_einer_aktion_je_punkt() -> None:
    zeilen = _zeilen(RUNBOOK)
    koepfe = [i for i, z in enumerate(zeilen) if z.startswith("**Rückweg")]
    assert len(koepfe) == 4
    for i in koepfe:
        assert zeilen[i] == "**Rückweg:**"
        assert zeilen[i + 1] == "" and zeilen[i + 2].startswith("- ")


_VERBOTEN = ("docker service update", "stack deploy", "system prune", "volume prune", "--remove-orphans")


def test_runbook_nennt_keine_verbotenen_befehle() -> None:
    text = _text()
    for befehl in _VERBOTEN:
        assert befehl not in text, befehl
    # `tailscale serve reset` nur als Warnung "Nie `tailscale serve reset`", nie als Anweisung.
    stellen = [m.start() for m in re.finditer(r"tailscale serve reset", text)]
    assert len(stellen) == 1
    assert all(text[s - 5:s] == "Nie `" for s in stellen)
    assert "`tailscale serve --bg --https=8172 http://127.0.0.1:8172`" in text
    assert "`tailscale serve --https=8172 off`" in text


def test_runbook_geheimnisse_nur_ueber_variable_und_printf() -> None:
    text = _text()
    assert "Authorization" not in text and "Bearer" not in text
    befehle = re.findall(r"`([^`]*)`", text)
    mit_schluessel = [b for b in befehle if "$SCHLUESSEL" in b]
    assert len(mit_schluessel) == 2
    assert all(b.startswith("printf ") and " | " in b for b in mit_schluessel), mit_schluessel
    assert "`SCHLUESSEL=$(python -c 'import secrets; print(secrets.token_urlsafe(32))')`" in text
    assert "`unset SCHLUESSEL`" in text
    assert "`bash deploy/db_zugang.sh`" in text
    assert "'https://<name-des-mini-pc-im-tailnet>:8172/mcp'" in text
    assert "Jeder Prozess, der diese Datei lesen kann, hält Adresse und Schlüssel des Dienstes." in text


def test_runbook_baut_aus_dem_commit_und_laesst_den_fremden_checkout_in_ruhe() -> None:
    text = _text()
    archiv = "`git -c core.autocrlf=false archive <commit> spaces/__init__.py spaces/auftragsbuch spaces/_contract"
    assert archiv in text
    assert "~/auftragsbuch-build" in text
    # Der Checkout ~/vibemind-os auf dem Mini-PC kommt in keinem Befehl vor.
    assert [b for b in re.findall(r"`([^`]*)`", text) if "~/vibemind-os" in b and b != "~/vibemind-os"] == []
    assert "dfc67d54" not in text


def test_runbook_stufe_3_merget_in_der_richtigen_reihenfolge_und_kopiert_unveraendert() -> None:
    text = _text()
    reihenfolge = [text.index(s) for s in ("openfang mergen und pushen", "vibemind-os mergen und pushen",
                                            "im äußeren Repo den Verweis", "Betriebs-Checkout heben")]
    assert reihenfolge == sorted(reihenfolge)
    assert "**unverändert**" in text and "Den Anbieter nicht umstellen" in text
    assert "<AUSGANG-CHECKOUT>" in text and "<AUSGANG-ABBILD>" in text
    assert "`DELETE /api/agents/<kennung>`" in text
    assert "Belegt ist das nicht" in text
    assert "tests/test_openfang_offene_mcp_zugaenge.py" in text


def test_runbook_endet_mit_den_offenen_punkten_fuer_stufe_2() -> None:
    zeilen = _zeilen(RUNBOOK)
    start = zeilen.index("## Offene Punkte für Stufe 2")
    assert all(not z.startswith("## ") for z in zeilen[start + 1:])
    offen = "\n".join(zeilen[start:])
    for stichwort in ("Herkunft erzwingen", "auftrag.naechster(p_an)", "brain-rowboat", "mcp_discovery",
                      "Rechteträger ohne startbares Modell", "harte Sperre"):
        assert stichwort in offen, stichwort


_OPENFANG_START = ('"<PFAD-ZUR-VENV>/Scripts/python.exe" "<PFAD-ZUM-AEUSSEREN-REPO>/scripts/mcp_mit_env.py" '
                   '--env OPENFANG_API_KEY --set OPENFANG_URL=http://127.0.0.1:4200 -- '
                   '"<ZWEIG-CHECKOUT>/spaces/auftragsbuch/deploy/openfang_aufruf.py"')


def test_runbook_openfang_aufrufe_laufen_ueber_das_hilfsprogramm_mit_der_markerregel_wie_bisher() -> None:
    punkte = _punkte(_zeilen(RUNBOOK))
    (pruefen,) = [p for p in punkte if "werkzeuge-je-agent" in p]
    assert f"`{_OPENFANG_START} werkzeuge-je-agent auftragsbuch`" in pruefen
    assert _LESEND in pruefen and _JA not in pruefen
    assert "`brain-sales: mcp_auftragsbuch_auftrag_lesen, mcp_auftragsbuch_auftrag_probe`" in pruefen
    assert "`offener Zugang (sieht alle Werkzeuge des Servers):`" in pruefen and "ändert den Exit-Code nicht" in pruefen
    assert "`unerwartet (<Status>)`" in pruefen and "`capabilities.tools`" in pruefen
    (gegen,) = [p for p in punkte if "gegenprobe brain-sales" in p]
    assert f"`{_OPENFANG_START} gegenprobe brain-sales sales_post_pruefen`" in gegen
    assert _JA in gegen and "direkt an OpenFang" in gegen
    assert "GET /api/mcp/servers" in gegen and "`-32602`" in gegen and "Tool not permitted for caller agent" in gegen
    assert "`DURCHGELASSEN: Recht vorhanden, nur die Freigabe fehlte - anhalten`" in gegen
    assert "`unerwartet (Server auftragsbuch nicht verbunden)`" in gegen
    assert "`abgelehnt von OpenFang (erwartet)`" in gegen and "`DURCHGELASSEN: Unbekannte Auftragsart - anhalten`" in gegen
    (entfernen,) = [p for p in punkte if "agent-entfernen brain-sales" in p]
    assert f"`{_OPENFANG_START} agent-entfernen brain-sales`" in entfernen
    assert _JA in entfernen and "DELETE /api/agents/<kennung>" in entfernen
    assert "`entfernt`" in entfernen and "`nicht vorhanden`" in entfernen
    # Der OpenFang-Schluessel steht nur in Befehlen ueber mcp_mit_env.py und das Hilfsprogramm, nie in curl.
    for befehl in re.findall(r"`([^`]*)`", _text()):
        if "OPENFANG_API_KEY" in befehl:
            assert befehl.startswith(_OPENFANG_START + " "), befehl
    assert "curl" not in " ".join(p for p in punkte if "OpenFang" in p and "/api/" in p)


_BRUECKEN_START = re.compile(r"mcp_mit_env\.py[^`]*?(?:bruecke|rauchprobe)\.py")


def test_runbook_jeder_bruecken_start_reicht_die_ca_datei_durch() -> None:
    befehle = [b for b in re.findall(r"`([^`]*)`", _text()) if _BRUECKEN_START.search(b)]
    assert len(befehle) >= 1
    for befehl in befehle:
        assert "--env AUFTRAGSBUCH_URL --env AUFTRAGSBUCH_SCHLUESSEL --env AUFTRAGSBUCH_CA_DATEI --" in befehl, befehl


def test_runbook_stufe_2_schreibt_die_ca_datei_in_die_root_env_und_nimmt_sie_zurueck() -> None:
    punkte = _punkte(_zeilen(RUNBOOK))
    (schreiben,) = [p for p in punkte if "pc_env_setzen.py" in p and "--entfernen" not in p]
    assert _JA in schreiben
    assert "AUFTRAGSBUCH_CA_DATEI=<PFAD-ZUR-VENV>/Lib/site-packages/certifi/cacert.pem" in schreiben
    assert "kein Geheimnis" in schreiben and "22.11." in schreiben and "Zertifikat nicht pruefbar" in schreiben
    (zurueck,) = [p for p in punkte if "--entfernen" in p]
    assert _JA in zurueck
    assert "--entfernen AUFTRAGSBUCH_URL AUFTRAGSBUCH_SCHLUESSEL AUFTRAGSBUCH_CA_DATEI" in zurueck
    (ausgang,) = [p for p in punkte if "Ausgangsstand festhalten" in p]
    assert "AUFTRAGSBUCH_\\(URL\\|SCHLUESSEL\\|CA_DATEI\\)=" in ausgang and "`3`" in ausgang
    (heben,) = [p for p in punkte if "Betriebs-Checkout heben" in p]
    assert "AUFTRAGSBUCH_CA_DATEI" in heben


# --- Stufe 4: Rowboat als Empfaenger ---------------------------------------------------------

_STUFE_4 = "## Stufe 4: Rowboat als Empfänger"


def _stufe_4() -> list[str]:
    return _punkte(_zeilen(RUNBOOK), ab=_STUFE_4, bis="## ")


def _befehle(punkt: str) -> list[str]:
    return re.findall(r"`([^`]*)`", punkt)


def _einer(punkte: list[str], stichwort: str) -> str:
    treffer = [p for p in punkte if stichwort in p]
    assert len(treffer) == 1, (stichwort, [_kopf(p) for p in treffer])
    return treffer[0]


def test_runbook_stufe_4_steht_vor_den_offenen_punkten() -> None:
    zeilen = _zeilen(RUNBOOK)
    assert zeilen.index(_STUFE_4) < zeilen.index("## Offene Punkte für Stufe 2")
    assert zeilen.index("## Abschluss (Plan-Task 11)") < zeilen.index(_STUFE_4)
    punkte = _stufe_4()
    assert len(punkte) >= 20
    assert len([p for p in punkte if _JA in p]) >= 14


def test_runbook_stufe_4_reihenfolge() -> None:
    punkte = _stufe_4()
    reihe = ["Sicherung", "migration_probe", "<psql> < ../marketing/db/101_auftragsbuch_empfang.sql",
             "bash deploy/db_zugang.sh abholer", "CLI nachsehen", "scoped-key add auftragsbuch-beleg",
             "scoped-key add gehirn-abholer", "docker compose build auftragsbuch",
             "docker compose up -d --no-deps auftragsbuch", "docker compose up -d rowboat-abholer",
             "abholer=start", "INSERT INTO auftrag.brain_arten"]
    stellen = [next(i for i, p in enumerate(punkte) if s in p) for s in reihe]
    assert stellen == sorted(stellen), list(zip(reihe, stellen))


def test_runbook_stufe_4_migration_101_erst_probe_dann_echt() -> None:
    punkte = _stufe_4()
    zaehlen = _einer(punkte, "falsches Rot")
    assert _LESEND in zaehlen and "verify_101" in zaehlen and "video" in zaehlen and "count(*)" in zaehlen
    probe = _einer(punkte, "migration_probe")
    assert "101_auftragsbuch_empfang.sql spaces/marketing/db/verify_101.sql" in probe and _JA in probe
    echt = _einer(punkte, "<psql> < ../marketing/db/101_auftragsbuch_empfang.sql")
    assert _JA in echt and "grep -c $'\\r'" in echt
    text = "\n".join(punkte)
    assert [b for p in punkte for b in _befehle(p) if "098" in b] == []
    assert "098_auftragsbuch_video.sql" in text and "nicht erneut" in text
    sicherung = _einer(punkte, "Sicherung")
    assert _JA in sicherung and "pg_dump" in sicherung and "auftrag-vor-101-" in sicherung


def test_runbook_stufe_4_schluessel_nie_sichtbar_und_eng() -> None:
    punkte = _stufe_4()
    nachsehen = _einer(punkte, "CLI nachsehen")
    assert _LESEND in nachsehen and "scoped-key add" not in nachsehen
    assert f"`{_WAECHTER}`" in nachsehen
    assert "`docker exec rowboat-headless-rowboat-server-1 node rowboat-server.cjs scoped-key list`" in nachsehen
    # Der Waechter steht vor dem ersten Aufruf des Unterbefehls (ein altes Buendel startete sonst einen Server).
    assert nachsehen.index(_WAECHTER) < nachsehen.index("scoped-key list")
    for name, kanaele, datei in [
        ("auftragsbuch-beleg", "todo:get,todo:plannerSignals", "laufzeit/rowboat-beleg-schluessel"),
        ("gehirn-abholer", "todo:get,todo:plannerSignals,todo:addSuggestions", "laufzeit/rowboat-abholer-schluessel"),
    ]:
        punkt = _einer(punkte, f"scoped-key add {name}")
        assert _JA in punkt
        (befehl,) = [b for b in _befehle(punkt) if "scoped-key add" in b]
        assert befehl == (f"(umask 077 && docker exec rowboat-headless-rowboat-server-1 node rowboat-server.cjs "
                          f"scoped-key add {name} {kanaele} > {datei})")
        assert f"`chmod 600 {datei}`" in punkt
    alle = [b for p in punkte for b in _befehle(p)]
    # Der Schluessel geht nur in eine Datei, nie in eine Ausgabe, Variable oder Kommandozeile.
    assert [b for b in alle if "scoped-key add" in b and not re.search(r"> laufzeit/rowboat-\w+-schluessel\)$", b)] == []
    assert [b for b in alle if re.search(r"\bcat\b|\bhead\b|\btail\b|\bless\b", b) and "schluessel" in b] == []
    pruefen = _einer(punkte, "wc -l < laufzeit/rowboat-abholer-schluessel")
    assert _LESEND in pruefen and "stat -c %a" in pruefen and "grep -c $'\\r'" in pruefen
    entziehen = [p for p in punkte if "scoped-key remove" in p]
    assert len(entziehen) == 2 and all(_JA in p for p in entziehen)


_WAECHTER = "docker exec rowboat-headless-rowboat-server-1 grep -cF '[scoped-key] fatal:' rowboat-server.cjs"
_ROWBOAT_SRC = Path(__file__).resolve().parents[2] / "rowboat" / "rowboat" / "apps" / "x"


def _waechter_zaehlt(buendel: str) -> int:
    """Was `grep -cF <muster> rowboat-server.cjs` im Container zaehlte: Zeilen mit dem festen Muster."""
    argumente = shlex.split(_WAECHTER)
    assert argumente[:4] == ["docker", "exec", "rowboat-headless-rowboat-server-1", "grep"]
    assert argumente[4] == "-cF" and argumente[6] == "rowboat-server.cjs" and len(argumente) == 7
    return sum(1 for zeile in buendel.splitlines() if argumente[5] in zeile)


def test_runbook_stufe_4_waechter_laesst_altes_buendel_nicht_durch() -> None:
    # Altes Buendel: kennt scoped-keys.json (auth.ts, protected-paths.ts), aber nicht den Unterbefehl.
    alt = ('const SCOPED_KEYS_FILE = "scoped-keys.json";\n'
           'const PROTECTED = ["server-key", "cipher-key", "config/mcp.json", "scoped-keys.json"];\n'
           'main().catch((err) => { console.error("[server] fatal:", err); process.exit(1); });\n')
    assert "scoped-key" in alt  # das alte `grep -c scoped-key` haette hier 2 gezaehlt
    assert _waechter_zaehlt(alt) == 0
    # Neues Buendel (esbuild behaelt Zeichenketten, Kommentare nicht): der Zweig aus standalone.ts.
    neu = alt + ('if (process.argv[2] === "scoped-key") { runScopedKeyCli(process.argv.slice(3), WorkDir, io)'
                 '.then((code) => process.exit(code), (err) => { console.error("[scoped-key] fatal:", err); });\n')
    assert _waechter_zaehlt(neu) == 1


@pytest.mark.skipif(not (_ROWBOAT_SRC / "apps" / "server" / "src" / "standalone.ts").exists(),
                    reason="Rowboat-Quelltext nicht ausgecheckt")
def test_runbook_stufe_4_waechter_muster_steht_nur_im_cli_zweig() -> None:
    muster = shlex.split(_WAECHTER)[5]
    treffer = sorted(p.relative_to(_ROWBOAT_SRC).as_posix() for p in _ROWBOAT_SRC.rglob("*.ts")
                     if "node_modules" not in p.parts and muster in p.read_text(encoding="utf-8", errors="replace"))
    assert treffer == ["apps/server/src/standalone.ts"]
    zweig = (_ROWBOAT_SRC / "apps" / "server" / "src" / "standalone.ts").read_text(encoding="utf-8")
    start = zweig.index("if (process.argv[2] === 'scoped-key')")
    assert start < zweig.index(muster) < zweig.index("} else {", start)


def test_runbook_stufe_4_dsn_nur_host_und_port_ohne_kennwort() -> None:
    punkte = _stufe_4()
    sicherung = _einer(punkte, "Sicherung")
    assert "`cp -p laufzeit/dsn laufzeit/dsn.vor-stufe-4`" in sicherung
    umziehen = _einer(punkte, "dsn-ort")
    assert _JA in umziehen
    assert _befehle(umziehen)[0] == "python3 deploy/db_zugang.py dsn-ort"
    for verboten in ("ALTER ROLE", "db_zugang.sh", "neues Passwort", "neues Kennwort", "<psql>"):
        assert verboten not in umziehen, verboten
    text = "\n".join(punkte)
    # In Stufe 4 bekommt nur der Abholer ein Passwort; die Rolle des Dienstes wird nirgends angefasst.
    assert "`bash deploy/db_zugang.sh`" not in text
    assert [b for p in punkte for b in _befehle(p) if "bash deploy/db_zugang.sh" in b] == [
        "bash deploy/db_zugang.sh abholer"]
    assert "auftragsbuch_dienst" not in " ".join(b for p in punkte for b in _befehle(p))
    (zurueck,) = [p for p in punkte if "`cp -p laufzeit/dsn.vor-stufe-4 laufzeit/dsn`" in p]
    assert _JA in zurueck


def test_runbook_stufe_4_kurzer_rueckweg_wenn_der_neustart_scheitert() -> None:
    zeilen = _zeilen(RUNBOOK)
    kopf = "**Kurzer Rückweg, falls Schritt 16 scheitert:**"
    assert zeilen.index(_STUFE_4) < zeilen.index(kopf) < zeilen.index("**Rückweg:**", zeilen.index(_STUFE_4))
    kurz = _punkte(zeilen, ab=kopf, bis="**Rückweg:**")
    assert len(kurz) == 4 and all(_JA in p for p in kurz)
    reihe = ["git -c core.autocrlf=false archive <AUSGANG-TAG>", "cp -p laufzeit/werte.env.vor-stufe-4 laufzeit/werte.env",
             "cp -p laufzeit/dsn.vor-stufe-4 laufzeit/dsn",
             "AUFTRAGSBUCH_TAG=<AUSGANG-TAG> docker compose up -d --no-deps auftragsbuch"]
    assert [next(i for i, p in enumerate(kurz) if s in p) for s in reihe] == [0, 1, 2, 3]
    befehle = [b for p in kurz for b in _befehle(p)]
    compose = [b for b in befehle if "docker compose" in b]
    assert compose == ["AUFTRAGSBUCH_TAG=<AUSGANG-TAG> docker compose up -d --no-deps auftragsbuch"]
    assert [b for b in befehle if re.search(r"prune|--remove-orphans|db_zugang|ALTER|DROP", b)] == []
    neustart = _einer(_stufe_4(), "docker compose up -d --no-deps auftragsbuch`. Voraussetzung")
    assert "kurzen Rückweg" in neustart


def test_runbook_stufe_4_dienste_nur_namentlich_und_host_netz() -> None:
    punkte = _stufe_4()
    compose = [b for p in punkte for b in _befehle(p) if "docker compose" in b]
    for befehl in compose:
        if re.search(r"docker compose (?:up|start|stop|rm|build)\b", befehl):
            assert re.search(r"(?:auftragsbuch|rowboat-abholer)$", befehl), befehl
    assert [b for p in punkte for b in _befehle(p) if "~/sales-claw" in b or "~/vibemind-os" in b] == []
    text = "\n".join(punkte)
    assert "AUFTRAGSBUCH_DB_HOST=127.0.0.1" in text and "AUFTRAGSBUCH_DB_PORT" in text
    assert "`python3 deploy/db_zugang.py dsn-ort`" in text  # DSN des Dienstes: nur Host und Port neu
    assert "tailscale serve status" in text and "127.0.0.1:8172" in text
    rauch = _einer(punkte, "abholer=start")
    assert _LESEND in rauch and "healthy" in rauch and "an = 'rowboat'" in rauch


def test_runbook_stufe_4_freischaltung_erst_nach_der_schwelle() -> None:
    punkte = _stufe_4()
    text = _text()
    assert text.count("INSERT INTO auftrag.brain_arten") == 1
    frei = _einer(punkte, "INSERT INTO auftrag.brain_arten")
    assert _JA in frei
    assert "INSERT INTO auftrag.brain_arten (an, art, version) VALUES ('rowboat', 'vorschlaege_anlegen', 2);" in frei
    for stichwort in ("Sperre", "Herkunft", "Ergebnistext", "gemessen"):
        assert stichwort in frei, stichwort
    zurueck = _einer(punkte, "DELETE FROM auftrag.brain_arten")
    assert _JA in zurueck
    assert ("DELETE FROM auftrag.brain_arten WHERE an = 'rowboat' AND art = 'vorschlaege_anlegen' AND version = 2;"
            in zurueck)
    assert punkte.index(frei) < punkte.index(zurueck)


def test_runbook_stufe_4_laden_vergleich_liest_nur_und_gibt_keine_werte_aus() -> None:
    punkte = _stufe_4()
    vergleich = _einer(punkte, "verschieden")
    assert _LESEND in vergleich and "`gleich`" in vergleich and "`verschieden`" in vergleich
    # Nichts, was Werte zeigen koennte: kein echo, cat, printenv, diff, grep, kein set -x.
    verboten = re.compile(r"\b(?:echo|cat|printenv|diff|grep)\b|set -x")
    assert not verboten.search(vergleich)
    # Folge: Der Rowboat-Abholer startet bei Abweichung nicht (Schritt 17 faengt das ab); Schritt 19 bestaetigt nur frueh.
    assert "startet" in vergleich and "Schritt 17" in vergleich and "frühe Bestätigung" in vergleich
    assert "LADEN_PRAEFIX" in vergleich and "Sales" in vergleich
    assert "AUFTRAGSBUCH_LADEN" in vergleich and "ABHOLER_LADEN" in vergleich
    # Sales ab 7403df3: AUFTRAGSBUCH_LADEN ist Pflicht in der Umgebung von Sales; fehlt er oder weicht er ab,
    # bleibt der Sales-Abholer stehen. Die Startzeile sagt bei Gleichheit auftragsbuch_laden=gleich.
    sales = vergleich[vergleich.index("Sales"):]
    assert "Umgebung von Sales" in sales and "AUFTRAGSBUCH_LADEN" in sales and "bleibt stehen" in sales
    assert "`auftragsbuch_laden=gleich`" in sales
    assert "7403df3" in sales and "still ab" in sales   # ältere Sales-Stände: Aufträge laufen still ab
    assert _ausnahmen(vergleich) == [_LESEND] and not _BEFEHL.search("\n".join(_befehle(vergleich)))
    # Vor der Freischaltung (INSERT) und nach der Rauchprobe des Abholers.
    stellen = [next(i for i, p in enumerate(punkte) if s in p) for s in ("abholer=start", "verschieden",
                                                                          "INSERT INTO auftrag.brain_arten")]
    assert stellen == sorted(stellen) and len(set(stellen)) == 3


def test_runbook_post_pruefen_bleibt_gesperrt_bis_die_herkunft_im_auftrag_steht() -> None:
    text = _flach(RUNBOOK.read_text(encoding="utf-8"))
    regel = "**post_pruefen bleibt gesperrt (keine Zeile in brain_arten), bis die Herkunft im Auftrag steht.**"
    assert text.count(regel) == 1
    assert "100_auftragsbuch_brain.sql" in text and "von='brain'" in text and "51 bis 53" in text
    freischalten = next(p for p in _stufe_4() if "INSERT INTO auftrag.brain_arten" in p)
    assert "post_pruefen" in freischalten and "gesperrt" in freischalten
    zeilen = _zeilen(RUNBOOK)
    stelle = next(i for i, z in enumerate(zeilen) if regel in z)
    assert zeilen.index(_STUFE_4) < stelle < zeilen.index("## Offene Punkte für Stufe 2")
