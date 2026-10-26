"""PC-Suchdienst (spaces/rowboat/pc_suche/pc_suche.py): nur lesen, Geheimnisse nie als Treffer."""
from __future__ import annotations

import json
import os
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pc_suche"))

import pc_suche  # noqa: E402
from pc_suche import (  # noqa: E402
    DATEI_AUS,
    ORDNER_AUS,
    WURZELN_VORGABE,
    Einstellungen,
    auszug_schwaerzen,
    handler_bauen,
    ist_ausgeschlossen,
    ordner_aufloesen,
    suchen,
)


@pytest.fixture
def baum(tmp_path: Path) -> Path:
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "Bericht.md").write_text("Quartalszahlen\nUmsatz steigt\n", encoding="utf-8")
    (tmp_path / "b").mkdir()
    (tmp_path / "b" / "notiz.txt").write_text("nichts\nAPI_KEY=abc123 Umsatz\n", encoding="utf-8")
    return tmp_path


def E(wurzel: Path, **kw) -> Einstellungen:
    return Einstellungen(wurzeln=(wurzel,), **kw)


def _pfade(r: dict) -> list[str]:
    return [t["pfad"] for t in r["treffer"]]


def test_vorgaben():
    assert WURZELN_VORGABE == (Path("C:/Users/User/Desktop"), Path("C:/Users/User/Documents"))
    e = Einstellungen(wurzeln=WURZELN_VORGABE)
    assert (e.host, e.port, e.vm_ip, e.zeitlimit_s, e.max_treffer) == ("100.84.127.84", 8149, "100.67.177.45", 10.0, 50)
    assert {".git", "node_modules", "AppData", ".rowboat", ".ssh", ".worktrees"} <= ORDNER_AUS
    assert ".env*" in DATEI_AUS and "werkzeuge.env" in DATEI_AUS


def test_wurzeln_aus_umgebung():
    assert pc_suche.wurzeln_aus_umgebung({}) == WURZELN_VORGABE
    assert pc_suche.wurzeln_aus_umgebung({"PC_SUCHE_WURZELN": " C:/a ; C:/b;\r\n"}) == (Path("C:/a"), Path("C:/b"))


def test_namenssuche_findet(baum):
    r = suchen("bericht", "name", "", 20, E(baum))
    assert r["ok"] is True and r["abgeschnitten"] is False
    assert _pfade(r) == [str(baum / "a" / "Bericht.md")]
    t = r["treffer"][0]
    assert t["groesse"] == (baum / "a" / "Bericht.md").stat().st_size and t["geaendert"]
    assert isinstance(r["dauer_ms"], int)


def test_inhaltssuche_mit_zeile_und_auszug(baum):
    r = suchen("umsatz", "inhalt", "", 20, E(baum))
    treffer = {t["pfad"]: t for t in r["treffer"]}
    assert treffer[str(baum / "a" / "Bericht.md")]["zeile"] == 2
    assert treffer[str(baum / "a" / "Bericht.md")]["auszug"] == "Umsatz steigt"
    assert treffer[str(baum / "b" / "notiz.txt")]["auszug"] == "[ausgeblendet]"


@pytest.mark.parametrize("name", [".env", ".env.local", "id_rsa", "x.pem", "client_secret_123.json", "my_token.txt",
                                  "server-key", "Passwoerter.txt", "werkzeuge.env", "tresor.KDBX"])
def test_geheimnisdateien_nie_treffer(tmp_path, name):
    (tmp_path / name).write_text("geheimwort\n", encoding="utf-8")
    assert ist_ausgeschlossen(tmp_path / name)
    assert suchen(name, "name", "", 50, E(tmp_path))["treffer"] == []
    assert suchen("geheimwort", "inhalt", "", 50, E(tmp_path))["treffer"] == []


@pytest.mark.parametrize("ordner", ["node_modules", ".git", "AppData", ".rowboat", ".ssh", ".venv", ".worktrees"])
def test_ausgeschlossene_ordner_nicht_betreten(tmp_path, ordner):
    (tmp_path / ordner / "tief").mkdir(parents=True)
    (tmp_path / ordner / "tief" / "fund.md").write_text("fund\n", encoding="utf-8")
    assert suchen("fund", "name", "", 20, E(tmp_path))["treffer"] == []
    assert suchen("fund", "inhalt", "", 20, E(tmp_path))["treffer"] == []
    assert suchen("fund", "name", str(tmp_path / ordner), 20, E(tmp_path))["ok"] is False


def test_ordner_innerhalb_und_relativ(baum):
    assert _pfade(suchen("bericht", "name", str(baum / "a"), 20, E(baum))) == [str(baum / "a" / "Bericht.md")]
    assert suchen("bericht", "name", str(baum / "b"), 20, E(baum))["treffer"] == []
    assert _pfade(suchen("bericht", "name", "a", 20, E(baum))) == [str(baum / "a" / "Bericht.md")]
    assert ordner_aufloesen("", (baum,)) == [baum]


def test_ordner_ausserhalb_der_wurzeln_verboten(baum):
    r = suchen("x", "name", "C:/Windows", 20, E(baum))
    assert r["ok"] is False and r["meldung"]
    assert ordner_aufloesen("C:/Windows", (baum,)) is None


def test_punktpunkt_verboten(baum):
    assert suchen("x", "name", str(baum / ".."), 20, E(baum))["ok"] is False
    assert suchen("x", "name", "a/../..", 20, E(baum))["ok"] is False


def test_symlink_nicht_gefolgt(tmp_path):
    draussen = tmp_path / "draussen"
    draussen.mkdir()
    (draussen / "fund.md").write_text("x", encoding="utf-8")
    wurzel = tmp_path / "wurzel"
    wurzel.mkdir()
    try:
        os.symlink(draussen, wurzel / "link", target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("Symlinks hier nicht erlaubt")
    assert suchen("fund", "name", "", 20, E(wurzel))["treffer"] == []


def test_junction_nicht_gefolgt(tmp_path):
    if os.name != "nt":
        pytest.skip("Junctions gibt es nur unter Windows")
    import subprocess
    draussen = tmp_path / "draussen"
    draussen.mkdir()
    (draussen / "fund.md").write_text("x", encoding="utf-8")
    wurzel = tmp_path / "wurzel"
    wurzel.mkdir()
    r = subprocess.run(["cmd", "/c", "mklink", "/J", str(wurzel / "kreuzung"), str(draussen)], capture_output=True)
    if r.returncode != 0:
        pytest.skip("mklink /J nicht moeglich")
    assert suchen("fund", "name", "", 20, E(wurzel))["treffer"] == []


def test_inhalt_nur_erlaubte_endungen(tmp_path):
    (tmp_path / "a.md").write_text("suchwort\n", encoding="utf-8")
    (tmp_path / "b.json").write_text('{"x": "suchwort"}\n', encoding="utf-8")
    (tmp_path / "c.yaml").write_text("x: suchwort\n", encoding="utf-8")
    assert [Path(p).name for p in _pfade(suchen("suchwort", "inhalt", "", 20, E(tmp_path)))] == ["a.md"]


def test_inhalt_grosse_dateien_uebersprungen(tmp_path):
    (tmp_path / "gross.txt").write_bytes(b"suchwort\n" + b"x" * (2 * 1024 * 1024 + 1))
    assert suchen("suchwort", "inhalt", "", 20, E(tmp_path))["treffer"] == []


@pytest.mark.parametrize("zeile", ["API_KEY=abc123", "token: xyz", "Authorization: Bearer abc", "my_secret = 1",
                                   "Passwort=hunter2", "password: x", "secret_key=1", "private key: x", "sk-ant-api03-abcdef", "ghp_abcdefghijkl",
                                   "github_pat_11AB", "-----BEGIN OPENSSH PRIVATE KEY-----"])
def test_auszug_geschwaerzt(zeile):
    assert auszug_schwaerzen(zeile) == "[ausgeblendet]"


def test_auszug_normal_gekuerzt():
    assert auszug_schwaerzen("  Hallo Welt  ") == "Hallo Welt"
    assert len(auszug_schwaerzen("x" * 500)) == 200


def test_zeitlimit_schneidet_ab(tmp_path):
    for i in range(5):
        (tmp_path / f"a{i}.md").write_text("x", encoding="utf-8")
    zeiten = iter([0.0])

    def springende_uhr() -> float:
        return next(zeiten, 11.0)

    r = suchen("a", "name", "", 20, E(tmp_path), uhr=springende_uhr)
    assert r["ok"] is True and r["abgeschnitten"] is True and len(r["treffer"]) < 5


def test_max_treffer(tmp_path):
    for i in range(5):
        (tmp_path / f"datei{i}.md").write_text("x", encoding="utf-8")
    r = suchen("datei", "name", "", 3, E(tmp_path))
    assert len(r["treffer"]) == 3 and r["abgeschnitten"] is True
    r = suchen("datei", "name", "", 5, E(tmp_path))
    assert len(r["treffer"]) == 5 and r["abgeschnitten"] is False
    assert len(suchen("datei", "name", "", 999, E(tmp_path, max_treffer=4))["treffer"]) == 4


@pytest.mark.parametrize("anfrage,art", [("", "name"), ("a" * 201, "name"), ("x", "rm"), ("  ", "inhalt")])
def test_ungueltige_anfrage(baum, anfrage, art):
    assert suchen(anfrage, art, "", 20, E(baum))["ok"] is False


# --- HTTP ----------------------------------------------------------------------------------------

SCHLUESSEL = "richtiger-schluessel"


@pytest.fixture
def dienst(baum):
    def starten(vm_ip: str = "127.0.0.1"):
        from http.server import ThreadingHTTPServer

        einst = Einstellungen(wurzeln=(baum,), host="127.0.0.1", port=0, vm_ip=vm_ip)
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler_bauen(einst, SCHLUESSEL))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        gestartet.append(server)
        return f"http://127.0.0.1:{server.server_port}"

    gestartet: list = []
    yield starten
    for s in gestartet:
        s.shutdown()
        s.server_close()


def _anfrage(url: str, methode: str = "POST", schluessel: str | None = SCHLUESSEL, koerper: dict | None = None):
    daten = json.dumps(koerper if koerper is not None else {"anfrage": "bericht", "art": "name", "ordner": "",
                                                            "max_treffer": 5}).encode()
    anfrage = urllib.request.Request(url, data=daten if methode in ("POST", "PUT") else None, method=methode)
    if schluessel is not None:
        anfrage.add_header("X-PC-Suche-Key", schluessel)
    anfrage.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(anfrage, timeout=10) as a:
            return a.status, a.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def test_http_falsche_ip_403(dienst):
    url = dienst(vm_ip="100.67.177.45")
    assert _anfrage(url + "/suchen") == (403, b"")
    assert _anfrage(url + "/health", "GET")[0] == 403


@pytest.mark.parametrize("schluessel", ["falsch", None, ""])
def test_http_falscher_schluessel_403(dienst, schluessel):
    assert _anfrage(dienst() + "/suchen", schluessel=schluessel) == (403, b"")


def test_http_richtig_200(dienst, baum):
    url = dienst()
    status, roh = _anfrage(url + "/suchen")
    assert status == 200
    r = json.loads(roh)
    assert r["ok"] is True and [t["pfad"] for t in r["treffer"]] == [str(baum / "a" / "Bericht.md")]
    assert _anfrage(url + "/health", "GET") == (200, b'{"status": "ok"}')


def test_http_kaputter_koerper_400(dienst):
    url = dienst()
    anfrage = urllib.request.Request(url + "/suchen", data=b"kein json", method="POST")
    anfrage.add_header("X-PC-Suche-Key", SCHLUESSEL)
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(anfrage, timeout=10)
    assert e.value.code == 400


@pytest.mark.parametrize("methode,pfad", [("PUT", "/suchen"), ("DELETE", "/suchen"), ("GET", "/suchen"),
                                          ("POST", "/health"), ("GET", "/anders")])
def test_http_andere_methoden_405(dienst, methode, pfad):
    assert _anfrage(dienst() + pfad, methode)[0] == 405


def test_main_ohne_schluessel_exit_2(monkeypatch, tmp_path):
    pausen: list[float] = []
    code = pc_suche.main(["--log", str(tmp_path / "pc_suche.log")], tresor=lambda name: None,
                         schlafen=pausen.append)
    assert code == 2
    assert len(pausen) == 9 and set(pausen) == {30}
    log = (tmp_path / "pc_suche.log").read_text(encoding="utf-8")
    assert "Schlüssel fehlt im Tresor" in log


def test_main_startet_mit_schluessel(tmp_path):
    gesehen: dict = {}

    class FalscherServer:
        def __init__(self, adresse, handler):
            gesehen["adresse"] = adresse
            gesehen["handler"] = handler

        def serve_forever(self):
            gesehen["laeuft"] = True

        def server_close(self):
            pass

    versuche = iter([None, "  k\r\n"])
    code = pc_suche.main(["--log", str(tmp_path / "l.log")], tresor=lambda name: next(versuche),
                         schlafen=lambda s: None, server_klasse=FalscherServer)
    assert code == 0 and gesehen["adresse"] == ("100.84.127.84", 8149) and gesehen["laeuft"]
    assert "k\r" not in (tmp_path / "l.log").read_text(encoding="utf-8").replace("Schlüssel", "")


def test_aufgabe_skript_vertrag():
    skript = (Path(__file__).resolve().parents[1] / "pc_suche" / "pc_suche_aufgabe.ps1").read_text(encoding="utf-8")
    assert "$taskName = 'VibeMind PC-Suche'" in skript
    for teil in ("-RestartCount 999", "-RestartInterval (New-TimeSpan -Minutes 1)",
                 "-ExecutionTimeLimit (New-TimeSpan -Seconds 0)", "-MultipleInstances IgnoreNew", "-AtLogOn",
                 "Stop-ScheduledTask -TaskName $taskName", "pythonw.exe", r"logs\rowboat\pc_suche.log"):
        assert teil in skript, teil
    # Nie per Prozessmuster beenden (traf schon die eigene Shell)
    assert "Stop-Process" not in skript and "taskkill" not in skript.lower()
    assert "New-NetFirewallRule" not in skript      # Firewall ist ein Betreiber-Schritt (Task 8)


# --- Fix-Runde 1 ---------------------------------------------------------------------------------

@pytest.mark.parametrize("zeile", [
    '"password": "x"', "'api_key': 'x'", '"apiKey": "abc"', 'ANTHROPIC_KEY="x"', "OPENAI_API_KEY=sk-x",
    "client_secret: abc", "Authorization: Bearer abcdefgh123", "curl -H 'Authorization: Bearer abc'",
    "Bearer eyJhbGciOiJIUzI1NiJ9", "git clone https://user:pass@github.com/x.git",
    "postgres://admin:geheim@db:5432/x", "tool --token abc123", "tool --password=hunter2", "tool --api-key x",
    "sk-proj-abcdefghijklmnop", "AIzaSyA1234567890abcdefghijk", "x = sk-abcdefghij1234",
])
def test_auszug_geschwaerzt_fix1(zeile):
    assert auszug_schwaerzen(zeile) == "[ausgeblendet]"


@pytest.mark.parametrize("zeile", ["Umsatz steigt", "https://example.com/seite", "Termin am 3.11.", "--verbose"])
def test_auszug_harmlos_bleibt(zeile):
    assert auszug_schwaerzen(zeile) == zeile


@pytest.mark.parametrize("name", ["id_ecdsa", "id_dsa.pub", "putty.ppk", ".netrc", ".npmrc", ".pypirc", ".pgpass",
                                  "vpn.ovpn"])
def test_weitere_geheimnisdateien(tmp_path, name):
    (tmp_path / name).write_text("geheimwort\n", encoding="utf-8")
    assert ist_ausgeschlossen(tmp_path / name)
    assert suchen(name, "name", "", 50, E(tmp_path))["treffer"] == []


def _fs_zugriffe_zaehlen(monkeypatch) -> list[str]:
    """Zaehlt Dateisystem-Zugriffe (Path.resolve/is_dir/exists/stat, os.scandir), ohne sie zu verhindern."""
    zugriffe: list[str] = []
    for name in ("resolve", "is_dir", "exists", "stat"):
        original = getattr(Path, name)

        def zaehlen(self, *a, _o=original, _n=name, **k):
            zugriffe.append(f"{_n}({self})")
            return _o(self, *a, **k)

        monkeypatch.setattr(Path, name, zaehlen)
    original_scandir = os.scandir

    def scandir(pfad, *a, **k):
        zugriffe.append(f"scandir({pfad})")
        return original_scandir(pfad, *a, **k)

    monkeypatch.setattr(os, "scandir", scandir)
    return zugriffe


@pytest.mark.parametrize("ordner", ["//evil/share", r"\\evil\share", r"\\?\C:\Windows", "//evil/share/x",
                                    r"\\100.67.177.45\c$"])
def test_unc_ohne_dateisystemzugriff(baum, monkeypatch, ordner):
    zugriffe = _fs_zugriffe_zaehlen(monkeypatch)
    ergebnis_aufloesen = ordner_aufloesen(ordner, (baum,))
    ergebnis_suche = suchen("x", "name", ordner, 20, E(baum))
    monkeypatch.undo()
    assert ergebnis_aufloesen is None and ergebnis_suche["ok"] is False
    assert zugriffe == []


def test_ausserhalb_lexikalisch_ohne_dateisystemzugriff(baum, monkeypatch):
    zugriffe = _fs_zugriffe_zaehlen(monkeypatch)
    a = ordner_aufloesen("C:/Windows", (baum,))
    b = ordner_aufloesen(str(baum.parent / "anderes"), (baum,))
    monkeypatch.undo()
    assert a is None and b is None and zugriffe == []


def test_handler_zeitlimit_und_hoechstens_drei_suchen(baum):
    from http.server import ThreadingHTTPServer

    handler = handler_bauen(Einstellungen(wurzeln=(baum,), vm_ip="127.0.0.1"), SCHLUESSEL)
    assert handler.timeout == 15
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_port}"
    try:
        # Drei laufende Suchen simulieren: die vierte wird sofort abgewiesen, nicht gestaut.
        for _ in range(3):
            assert handler.suche_sperre.acquire(blocking=False)
        assert not handler.suche_sperre.acquire(blocking=False)
        status, roh = _anfrage(url + "/suchen")
        assert status == 503 and json.loads(roh)["ok"] is False
        for _ in range(3):
            handler.suche_sperre.release()
        assert _anfrage(url + "/suchen")[0] == 200
    finally:
        server.shutdown()
        server.server_close()
