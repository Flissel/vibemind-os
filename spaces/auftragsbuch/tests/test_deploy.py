"""Statische Tests der Ausroll-Dateien des Auftragsbuchs (kein Docker, kein Netz).

Muster: spaces/event_management/tests/system/test_ausrollen.py. Geprueft wird nur der Text der
Dateien: anbinden.sh und db_zugang.sh lesen Geheimnisse nur von stdin bzw. erzeugen sie selbst und
geben nichts aus, Compose bindet nur an Loopback und reicht Geheimnisse nur als Dateien, nichts
Privates steht im Ordner, das OpenFang-Beispiel ist ein stdio-Eintrag ohne Schluessel, das Runbook
haelt seine Regeln ein.
"""
from __future__ import annotations

import re
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
    assert not re.search(r"\$\{?[1-9@*#]", text), "keine Argumente aus der Kommandozeile"
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
                          "AUFTRAGSBUCH_DB_CONTAINER", "AUFTRAGSBUCH_DB_HOST", "AUFTRAGSBUCH_DB_NAME"}
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
    assert "AUFTRAGSBUCH_URL" in args and "AUFTRAGSBUCH_SCHLUESSEL" in args
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


def _punkte(zeilen: list[str], ab: str = "## Stufe 1") -> list[str]:
    """Zerlegt das Runbook ab `ab` in nummerierte und Spiegelstrich-Punkte samt eingerueckter Folgezeilen.

    Jeder Punkt kommt flach zurueck (siehe _flach). Schluss ist der Abschnitt `## Offene Punkte`: Er
    beschreibt keine Handlung.
    """
    start = next(i for i, z in enumerate(zeilen) if z.startswith(ab))
    punkte: list[str] = []
    offen = False
    for zeile in zeilen[start:]:
        if zeile.startswith("## Offene Punkte"):
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
    (probe,) = [p for p in punkte if "migration_probe" in p]
    assert _JA in probe and "ROLLBACK" in probe and "lock_timeout" in probe
    (bleibt,) = [p for p in punkte if _BLEIBT in p]
    assert "Normalfall" in bleibt and "CHECK" in bleibt
    # Jede Aktion des Rueckwegs von Stufe 1 hinter dem Normalfall braucht ihr eigenes Ja.
    stufe1 = punkte[: next(i for i, p in enumerate(punkte) if "Bau-Ordner auf dem" in p)]
    nach = stufe1[stufe1.index(bleibt) + 1:]
    assert len(nach) == 3 and all(_JA in p for p in nach)


def test_runbook_abschluss_ist_eine_nummerierte_liste_mit_marker_an_der_probe() -> None:
    punkte = _punkte(_zeilen(RUNBOOK), ab="## Abschluss")
    assert len(punkte) >= 8 and all(re.match(r"\d+\. ", p) for p in punkte)
    probe = next(p for p in punkte if "auftrag_probe" in p)
    assert _JA in probe
    assert all(_JA in p for p in punkte if "docker compose" in p or "direkt an OpenFang" in p)


def test_runbook_rueckweg_ist_eine_liste_mit_einer_aktion_je_punkt() -> None:
    zeilen = _zeilen(RUNBOOK)
    koepfe = [i for i, z in enumerate(zeilen) if z.startswith("**Rückweg")]
    assert len(koepfe) == 3
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
