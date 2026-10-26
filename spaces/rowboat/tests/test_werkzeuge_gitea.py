"""Gitea nur lesend (deploy/headless/werkzeuge/gitea_lesen.py) ohne Netz."""
from __future__ import annotations

import base64
import json
import sys
from pathlib import Path
from typing import Any

import pytest

ORDNER = Path(__file__).resolve().parents[1] / "deploy" / "headless" / "werkzeuge"
sys.path.insert(0, str(ORDNER))

from gitea_lesen import (  # noqa: E402
    SCHREIBPROBEN,
    SCHREIBPROBEN_REPO,
    GiteaLeser,
    gitea_aus_umgebung,
    nur_lesend_pruefen,
)

TOKEN = "0123456789abcdef0123456789abcdef01234567"
BASIS = "http://127.0.0.1:3002"
API = BASIS + "/api/v1"


class FalschesHttp:
    """Protokolliert (methode, url, kopf) und liefert vorbereitete Antworten je URL-Anfang."""

    def __init__(self, antworten: dict[str, tuple[int, Any]] | None = None,
                 fehler: Exception | None = None) -> None:
        self.antworten = antworten or {}
        self.fehler = fehler
        self.aufrufe: list[tuple[str, str, dict[str, str]]] = []

    def get(self, url: str, kopf: dict[str, str]) -> tuple[int, Any]:
        self.aufrufe.append(("GET", url, kopf))
        if self.fehler is not None:
            raise self.fehler
        for anfang in sorted(self.antworten, key=len, reverse=True):
            if url.startswith(anfang):
                return self.antworten[anfang]
        return 200, []


def _leser(http: FalschesHttp, zustand: str = "bereit") -> GiteaLeser:
    return GiteaLeser(BASIS, TOKEN, zustand, http.get)


def _repo(name: str, spiegel: bool = False) -> dict:
    return {"full_name": name, "private": True, "mirror": spiegel, "empty": False, "archived": False,
            "mirror_updated": "2026-10-26T10:00:00Z" if spiegel else "0001-01-01T00:00:00Z",
            "updated_at": "2026-08-05T12:00:00Z", "default_branch": "main", "size": 120, "description": "d"}


def test_umgebung_crlf_basis_und_fehlend():
    assert gitea_aus_umgebung({"GITEA_LESEN": f" {TOKEN}\r\n", "GITEA_URL": "http://x:1/\r\n"}) == \
        ("http://x:1", TOKEN, "unklar")
    assert gitea_aus_umgebung({}) == (BASIS, None, "fehlt")
    assert gitea_aus_umgebung({"GITEA_LESEN": "  "})[2] == "fehlt"


def test_nur_get_mit_token_kopf():
    inhalt = base64.b64encode(b"# Hallo").decode()
    http = FalschesHttp({f"{API}/repos/search": (200, {"ok": True, "data": [_repo("vibemind/secondbrain")]}),
                         f"{API}/repos/vibemind/secondbrain/contents/": (200, {"encoding": "base64",
                                                                               "content": inhalt})})
    leser = _leser(http)
    assert leser.repos()["ok"]
    assert leser.issues("vibemind/secondbrain")["ok"]
    assert leser.commits("vibemind/secondbrain")["ok"]
    assert leser.datei("vibemind/secondbrain")["inhalt"] == "# Hallo"
    assert {m for m, _, _ in http.aufrufe} == {"GET"}
    for _, url, kopf in http.aufrufe:
        assert url.startswith(API + "/")
        assert kopf["Authorization"] == f"token {TOKEN}"
        assert kopf["Accept"] == "application/json"


def test_repos_mit_spiegelstand():
    http = FalschesHttp({f"{API}/repos/search": (200, {"ok": True, "data": [
        _repo("vibemind/secondbrain"), _repo("spiegel/vibemind-os", spiegel=True)]})})
    r = _leser(http).repos()
    assert http.aufrufe[0][1] == f"{API}/repos/search?limit=50&sort=updated&order=desc"
    assert r["repos"][0] == {"name": "vibemind/secondbrain", "privat": True, "spiegel": False,
                             "spiegel_aktualisiert": None, "geaendert": "2026-08-05T12:00:00Z",
                             "leer": False, "archiviert": False, "standard_branch": "main",
                             "groesse_kb": 120, "beschreibung": "d"}
    assert r["repos"][1]["spiegel"] is True and r["repos"][1]["spiegel_aktualisiert"] == "2026-10-26T10:00:00Z"


def test_issues_nur_issues_und_begrenzt():
    http = FalschesHttp({f"{API}/repos/vibemind/forgectl/issues": (200, [
        {"number": 4, "title": "Fehler", "state": "open", "user": {"login": "a"}, "updated_at": "t",
         "comments": 1, "labels": [{"name": "bug"}], "html_url": "u"}])})
    r = _leser(http).issues("vibemind/forgectl", zustand="all", max_anzahl=500)
    assert r["issues"] == [{"nummer": 4, "titel": "Fehler", "zustand": "open", "von": "a", "geaendert": "t",
                            "kommentare": 1, "labels": ["bug"], "url": "u"}]
    assert http.aufrufe[0][1] == f"{API}/repos/vibemind/forgectl/issues?state=all&type=issues&limit=50"


def test_commits_mit_branch():
    http = FalschesHttp({f"{API}/repos/vibemind/forgectl/commits": (200, [
        {"sha": "abcdef1234567890", "commit": {"message": "feat: x\n\nk", "author": {"name": "F", "date": "d"}}}])})
    r = _leser(http).commits("vibemind/forgectl", branch="feat/x", max_anzahl=0)
    assert r["commits"] == [{"sha": "abcdef123456", "nachricht": "feat: x", "autor": "F", "datum": "d"}]
    assert http.aufrufe[0][1] == (f"{API}/repos/vibemind/forgectl/commits?limit=1&stat=false&verification=false"
                                  "&files=false&sha=feat/x")


def test_datei_ordner_und_pfadschutz():
    http = FalschesHttp({f"{API}/repos/vibemind/forgectl/contents/": (200, [{"name": "a.md", "type": "file"}])})
    leser = _leser(http)
    assert leser.datei("vibemind/forgectl", "docs", ref="main")["eintraege"] == [{"name": "a.md", "typ": "file"}]
    assert http.aufrufe[0][1] == f"{API}/repos/vibemind/forgectl/contents/docs?ref=main"
    for pfad in ("../x", "/etc/passwd", "a\\..\\b", ""):
        assert leser.datei("vibemind/forgectl", pfad)["ok"] is False
    assert len(http.aufrufe) == 1


@pytest.mark.parametrize("repo", ["../x", "a/b/c", "vibemind", "vibemind/..", "x y/z", ""])
def test_repo_muster_abgelehnt(repo):
    http = FalschesHttp()
    assert _leser(http).commits(repo)["ok"] is False
    assert http.aufrufe == []


@pytest.mark.parametrize("zustand,text", [("darf_schreiben", "darf schreiben"), ("fehlt", "kein Token"),
                                          ("abgelehnt", "abgelehnt"), ("unklar", "unklar")])
def test_gesperrt_ohne_anfrage(zustand, text):
    http = FalschesHttp()
    r = _leser(http, zustand).repos()
    assert r["ok"] is False and text in r["meldung"]
    assert http.aufrufe == []


def test_token_nie_in_fehler():
    http = FalschesHttp(fehler=RuntimeError(f"bad {TOKEN}"))
    r = _leser(http).repos()
    assert TOKEN not in json.dumps(r) and r["meldung"].endswith("RuntimeError")


def test_http_403_gemeldet():
    http = FalschesHttp({f"{API}/repos/vibemind/x/issues": (403, {"message": "scope"})})
    assert _leser(http).issues("vibemind/x") == {"ok": False, "meldung": "Gitea: kein Zugriff (403)"}


# --- Schreibprobe (gemessen 26.10. an Gitea 1.23: read-Token 403 ueberall, write-Token 422) ---------------

class FalschesSenden:
    def __init__(self, status: dict[str, int] | None = None, standard: int = 403,
                 fehler: Exception | None = None) -> None:
        self.status = status or {}
        self.standard = standard
        self.fehler = fehler
        self.aufrufe: list[tuple[str, str, dict]] = []

    def __call__(self, methode: str, url: str, kopf: dict[str, str], koerper: dict):
        self.aufrufe.append((methode, url, koerper))
        if self.fehler is not None:
            raise self.fehler
        for pfad, status in self.status.items():
            if url.endswith(pfad):
                return status, {}
        return self.standard, {}


def test_schreibproben_mit_leerem_koerper_und_repo():
    senden = FalschesSenden()
    assert nur_lesend_pruefen(BASIS, TOKEN, "vibemind/secondbrain", senden) == "bereit"
    assert len(senden.aufrufe) == len(SCHREIBPROBEN) + len(SCHREIBPROBEN_REPO) == 6
    assert all(k == {} and m == "POST" for m, _, k in senden.aufrufe)
    assert f"{API}/repos/vibemind/secondbrain/issues" in [u for _, u, _ in senden.aufrufe]
    assert f"{API}/admin/users" in [u for _, u, _ in senden.aufrufe]


@pytest.mark.parametrize("status,erwartet", [(422, "darf_schreiben"), (201, "darf_schreiben"),
                                             (401, "abgelehnt"), (404, "unklar"), (500, "unklar")])
def test_jede_probe_sperrt(status, erwartet):
    senden = FalschesSenden({"/orgs": status})
    assert nur_lesend_pruefen(BASIS, TOKEN, "vibemind/secondbrain", senden) == erwartet


def test_probe_ohne_repo_nur_kontoweit_und_ausnahme_unklar():
    senden = FalschesSenden()
    assert nur_lesend_pruefen(BASIS, TOKEN, None, senden) == "bereit"
    assert all("/repos/vibemind" not in u for _, u, _ in senden.aufrufe)
    assert nur_lesend_pruefen(BASIS, TOKEN, "vibemind/x", FalschesSenden(fehler=OSError())) == "unklar"
    assert nur_lesend_pruefen(BASIS, TOKEN, "../x", FalschesSenden()) == "unklar"
