"""MCP-Server vibemind-werkzeuge (deploy/headless/werkzeuge/server.py) ohne Netz."""
from __future__ import annotations

import importlib.util
import json
import logging
import sys
from pathlib import Path
from typing import Any

import anyio
import pytest
from starlette.testclient import TestClient

ORDNER = Path(__file__).resolve().parents[1] / "deploy" / "headless" / "werkzeuge"
sys.path.insert(0, str(ORDNER))

# Eindeutiger Modulname: ein nacktes "server" kollidiert sonst mit anderen Testmodulen.
_spec = importlib.util.spec_from_file_location("werkzeuge_server", ORDNER / "server.py")
server = importlib.util.module_from_spec(_spec)
sys.modules["werkzeuge_server"] = server
_spec.loader.exec_module(server)
from pc_suche_relay import NICHT_EINGERICHTET  # noqa: E402

UMG = {"GITHUB_BESITZER": "Flissel=GITHUB_LESEN_FLISSEL,Vibemind-LAB=GITHUB_LESEN_VIBEMIND_LAB"}
TOKEN = "github_pat_SCHREIBT123"
ZWOELF = {"github_repos", "github_issues", "github_prs", "github_commits", "github_ci", "github_datei",
          "github_ueberblick", "gitea_repos", "gitea_issues", "gitea_commits", "gitea_datei", "pc_suchen"}


def _werkzeuge(umg: dict) -> list:
    return anyio.run(server.bauen(umg).list_tools)


def test_genau_die_zwoelf_werkzeuge():
    assert {t.name for t in _werkzeuge(UMG)} == ZWOELF
    assert {fn.__name__ for fn in server.WERKZEUGE} == ZWOELF and len(server.WERKZEUGE) == 12


def test_keine_schreibwerkzeuge():
    verboten = ("push", "create", "merge", "delete", "update", "write", "comment", "close", "anlegen", "loeschen")
    for t in _werkzeuge(UMG):
        assert not any(w in t.name for w in verboten), t.name


def test_werkzeuge_haben_beschreibung_und_parameter():
    tools = {t.name: t for t in _werkzeuge(UMG)}
    assert set(tools["pc_suchen"].inputSchema["properties"]) == {"anfrage", "art", "ordner", "max_treffer"}
    assert set(tools["github_datei"].inputSchema["properties"]) == {"repo", "pfad", "ref"}
    assert set(tools["github_ci"].inputSchema["properties"]) == {"repo", "branch", "max_anzahl"}
    assert set(tools["gitea_repos"].inputSchema["properties"]) == set()
    assert all(t.description for t in tools.values())


def test_bindet_nur_loopback():
    s = server.bauen(UMG).settings
    assert (s.host, s.port) == ("127.0.0.1", 8150)
    assert server.bauen({**UMG, "WERKZEUGE_PORT": " 8151\r\n"}).settings.port == 8151


def test_health():
    with TestClient(server.bauen(UMG).streamable_http_app()) as c:
        assert c.get("/health").json() == {"status": "ok"}


class Netz:
    def __init__(self, post_status: int = 403, repos: list | None = None, kaputt: bool = False) -> None:
        self.post_status = post_status
        self.repos = [{"name": "oeffentlich", "private": False, "pushed_at": "2026-10-25T00:00:00Z"},
                      {"name": "vibemind", "private": True, "pushed_at": "2026-10-25T00:00:00Z"}] if repos is None else repos
        self.kaputt = kaputt
        self.aufrufe: list[tuple[str, str]] = []

    def get(self, url: str, kopf: dict[str, str]) -> tuple[int, Any]:
        self.aufrufe.append(("GET", url))
        if self.kaputt:
            raise OSError(TOKEN)
        if url.endswith("/user"):
            return 200, {"login": "Flissel"}
        return 200, self.repos

    def senden(self, methode: str, url: str, kopf: dict[str, str], koerper: dict) -> tuple[int, dict]:
        self.aufrufe.append((methode, url))
        return self.post_status, {"github-authentication-token-expiration": "2027-01-24 13:05:00 +0100"}


UMG_TOKEN = {**UMG, "GITHUB_LESEN_FLISSEL": TOKEN + "\r\n"}


def test_start_sperrt_schreibenden_token(caplog):
    caplog.set_level(logging.INFO, logger="werkzeuge")
    netz = Netz(post_status=422)
    z = server.zugaenge_beim_start(UMG_TOKEN, netz.get, netz.senden)
    assert z["Flissel"].zustand == "darf_schreiben"
    assert z["Vibemind-LAB"].zustand == "fehlt"
    assert ("POST", "https://api.github.com/repos/Flissel/vibemind/labels") in netz.aufrufe
    zeilen = [r.getMessage() for r in caplog.records]
    assert "Zugang Flissel: darf_schreiben (Token läuft ab 2027-01-24)" in zeilen
    assert "Zugang Vibemind-LAB: fehlt" in zeilen
    assert TOKEN not in caplog.text
    # Danach laeuft keine Anfrage mit diesem Token mehr
    leser = server.GithubLeser(z, netz.get)
    vorher = len(netz.aufrufe)
    assert "gesperrt: Token darf schreiben" in leser.repos("Flissel")["meldung"]
    assert len(netz.aufrufe) == vorher


def test_start_nur_lesend_ist_bereit_und_prueft_privates_repo():
    netz = Netz(post_status=403)
    assert server.zugaenge_beim_start(UMG_TOKEN, netz.get, netz.senden)["Flissel"].zustand == "bereit"
    proben = [u for m, u in netz.aufrufe if m != "GET"]
    assert len(proben) == 6 and all("/repos/Flissel/vibemind/" in u for u in proben)


def test_start_ohne_privates_repo_ist_unklar():
    netz = Netz(repos=[{"name": "oeffentlich", "private": False}])
    assert server.zugaenge_beim_start(UMG_TOKEN, netz.get, netz.senden)["Flissel"].zustand == "unklar"
    assert all(m == "GET" for m, _ in netz.aufrufe)


def test_start_ohne_repo_ist_unklar_und_gesperrt():
    netz = Netz(repos=[])
    z = server.zugaenge_beim_start(UMG_TOKEN, netz.get, netz.senden)
    assert z["Flissel"].zustand == "unklar"
    assert all(m == "GET" for m, _ in netz.aufrufe)


def test_start_get_fehler_ist_unklar():
    z = server.zugaenge_beim_start(UMG_TOKEN, Netz(kaputt=True).get, Netz().senden)
    assert z["Flissel"].zustand == "unklar"


def test_bauen_nutzt_startprobe():
    netz = Netz(post_status=403)
    server.bauen(UMG_TOKEN, get=netz.get, senden=netz.senden)
    r = server.github_repos("Flissel")
    assert r["ok"] is True and r["repos"][1]["name"] == "vibemind"
    assert server.github_ueberblick()["gesperrt"] == {"Vibemind-LAB": "fehlt"}


def test_unklar_wird_nach_30_min_neu_geprueft():
    netz = Netz(post_status=403, kaputt=True)
    jetzt = [1000.0]
    server.bauen(UMG_TOKEN, get=netz.get, senden=netz.senden, uhr=lambda: jetzt[0])
    assert "unklar" in server.github_repos("Flissel")["meldung"]
    netz.kaputt = False
    jetzt[0] += 1700                       # noch keine 30 min: weiter gesperrt, keine neue Probe
    vorher = len(netz.aufrufe)
    assert server.github_repos("Flissel")["ok"] is False
    assert len(netz.aufrufe) == vorher
    jetzt[0] += 101                        # jetzt > 30 min seit der letzten Probe
    assert server.github_repos("Flissel")["ok"] is True


def test_darf_schreiben_wird_nicht_neu_geprueft():
    netz = Netz(post_status=422)
    jetzt = [0.0]
    server.bauen(UMG_TOKEN, get=netz.get, senden=netz.senden, uhr=lambda: jetzt[0])
    jetzt[0] += 10_000
    vorher = len(netz.aufrufe)
    assert "darf schreiben" in server.github_repos("Flissel")["meldung"]
    assert len(netz.aufrufe) == vorher


def test_pc_suchen_ohne_key_in_umgebung_meldet_nicht_eingerichtet():
    server.bauen(UMG)
    assert server.pc_suchen("bericht") == {"ok": False, "meldung": NICHT_EINGERICHTET}


def test_pc_suchen_nutzt_url_und_key_aus_umgebung():
    gesehen: dict = {}

    def post_json(url, kopf, koerper, c, r):
        gesehen.update(url=url, kopf=kopf)
        return 200, {"ok": True, "treffer": [], "abgeschnitten": False, "dauer_ms": 1}

    server.bauen({**UMG, "PC_SUCHE_URL": "http://pc:8149", "PC_SUCHE_KEY": "GEHEIM\r\n"}, post_json=post_json)
    r = server.pc_suchen("bericht")
    assert r["ok"] is True and gesehen == {"url": "http://pc:8149/suchen", "kopf": {"X-PC-Suche-Key": "GEHEIM"}}
    assert "GEHEIM" not in json.dumps(r)


def test_werkzeug_aufruf_ueber_mcp():
    netz = Netz(post_status=403)
    mcp = server.bauen(UMG_TOKEN, get=netz.get, senden=netz.senden)
    ergebnis = anyio.run(lambda: mcp.call_tool("github_repos", {"besitzer": "Flissel"}))
    text = json.dumps(ergebnis, default=str)
    assert "vibemind" in text and TOKEN not in text


# --- Gitea -------------------------------------------------------------------------------------------

GITEA_TOKEN = "0123456789abcdef0123456789abcdef01234567"


class GiteaNetz:
    def __init__(self, post_status: int = 403, kaputt: bool = False) -> None:
        self.post_status = post_status
        self.kaputt = kaputt
        self.aufrufe: list[tuple[str, str]] = []

    def get(self, url: str, kopf: dict[str, str]) -> tuple[int, Any]:
        self.aufrufe.append(("GET", url))
        if self.kaputt:
            raise OSError(GITEA_TOKEN)
        if "/repos/search" in url:
            return 200, {"ok": True, "data": [{"full_name": "vibemind/secondbrain", "private": True,
                                               "mirror": False, "mirror_updated": "0001-01-01T00:00:00Z"}]}
        return 200, []

    def senden(self, methode: str, url: str, kopf: dict[str, str], koerper: dict) -> tuple[int, dict]:
        self.aufrufe.append((methode, url))
        return self.post_status, {}


UMG_GITEA = {**UMG, "GITEA_LESEN": GITEA_TOKEN + "\r\n"}


def test_gitea_ohne_token_meldet_fehlt_ohne_anfrage():
    netz = GiteaNetz()
    server.bauen(UMG, get=netz.get, senden=netz.senden)
    assert "kein Token" in server.gitea_repos()["meldung"]
    assert not any("3002" in u for _, u in netz.aufrufe)


def test_gitea_start_lesend_bereit(caplog):
    caplog.set_level(logging.INFO, logger="werkzeuge")
    netz = GiteaNetz(post_status=403)
    server.bauen(UMG_GITEA, get=netz.get, senden=netz.senden)
    proben = [u for m, u in netz.aufrufe if m == "POST"]
    assert "http://127.0.0.1:3002/api/v1/repos/vibemind/secondbrain/issues" in proben and len(proben) == 6
    r = server.gitea_repos()
    assert r["ok"] is True and r["repos"][0]["name"] == "vibemind/secondbrain"
    assert "Zugang Gitea: bereit" in [x.getMessage() for x in caplog.records]
    assert GITEA_TOKEN not in caplog.text


def test_gitea_schreibender_token_gesperrt_ohne_weitere_anfrage():
    netz = GiteaNetz(post_status=422)
    server.bauen({**UMG_GITEA, "GITEA_URL": "http://gitea:3000/"}, get=netz.get, senden=netz.senden)
    assert all(u.startswith("http://gitea:3000/api/v1/") for _, u in netz.aufrufe)
    vorher = len(netz.aufrufe)
    assert "darf schreiben" in server.gitea_issues("vibemind/secondbrain")["meldung"]
    assert len(netz.aufrufe) == vorher


def test_gitea_unklar_wird_nach_30_min_neu_geprueft():
    netz = GiteaNetz(kaputt=True)
    jetzt = [0.0]
    server.bauen(UMG_GITEA, get=netz.get, senden=netz.senden, uhr=lambda: jetzt[0])
    assert "unklar" in server.gitea_repos()["meldung"]
    netz.kaputt = False
    jetzt[0] += 1700
    assert server.gitea_repos()["ok"] is False
    jetzt[0] += 101
    assert server.gitea_repos()["ok"] is True


@pytest.fixture(autouse=True)
def _aufraeumen():
    yield
    server.bauen(UMG)   # keinen Zustand mit Attrappen in andere Tests tragen
