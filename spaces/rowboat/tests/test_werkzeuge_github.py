"""GitHub nur lesend (deploy/headless/werkzeuge/github_lesen.py) mit Attrappen, ohne Netz."""
from __future__ import annotations

import base64
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "deploy" / "headless" / "werkzeuge"))

from github_lesen import (  # noqa: E402
    GithubFehler,
    GithubLeser,
    Zugang,
    besitzer_aus_umgebung,
    nur_lesend_pruefen,
)

TOKEN = "github_pat_GEHEIM123"
JETZT = datetime(2026, 10, 26, 12, 0, tzinfo=timezone.utc)


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
        # laengster passender Anfang gewinnt
        for anfang in sorted(self.antworten, key=len, reverse=True):
            if url.startswith(anfang):
                return self.antworten[anfang]
        return 200, []


API = "https://api.github.com"


def _repo(name: str, tage_alt: int) -> dict:
    return {"name": name, "private": True, "pushed_at": (JETZT - timedelta(days=tage_alt)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "open_issues_count": 2, "default_branch": "master", "description": "d", "html_url": "x"}


def _leser(http: FalschesHttp, **zustaende: str) -> GithubLeser:
    zustaende = zustaende or {"Flissel": "bereit"}
    zugaenge = {b.replace("_", "-"): Zugang(b.replace("_", "-"), TOKEN, z) for b, z in zustaende.items()}
    return GithubLeser(zugaenge, http.get, jetzt=lambda: JETZT)


def test_nur_get_anfragen():
    inhalt = base64.b64encode(b"# Hallo").decode()
    http = FalschesHttp({f"{API}/user": (200, {"login": "Flissel"}),
                         f"{API}/user/repos": (200, [_repo("vibemind", 1)]),
                         f"{API}/repos/Flissel/vibemind/contents/": (200, {"encoding": "base64", "content": inhalt})})
    leser = _leser(http)
    assert leser.repos("Flissel")["ok"]
    assert leser.issues("Flissel/vibemind")["ok"]
    assert leser.prs("Flissel/vibemind")["ok"]
    assert leser.commits("Flissel/vibemind")["ok"]
    assert leser.datei("Flissel/vibemind")["ok"]
    assert leser.ueberblick()["ok"]
    assert {m for m, _, _ in http.aufrufe} == {"GET"}
    for _, url, kopf in http.aufrufe:
        assert url.startswith(API + "/")
        assert kopf["Authorization"] == f"Bearer {TOKEN}"
        assert kopf["Accept"] == "application/vnd.github+json"
        assert kopf["X-GitHub-Api-Version"] == "2022-11-28"
        assert kopf["User-Agent"] == "vibemind-werkzeuge"


def test_fremder_besitzer_abgelehnt():
    http = FalschesHttp()
    leser = _leser(http)
    r = leser.issues("evil/repo")
    assert r["ok"] is False and "nicht freigegeben" in r["meldung"]
    assert http.aufrufe == []
    assert "nicht freigegeben" in leser.repos("evil")["meldung"]
    assert http.aufrufe == []


@pytest.mark.parametrize("repo", ["../x", "a/b/c", "Flissel", "Flissel/..", "Flissel/x y", "Flissel/.", ""])
def test_repo_muster_abgelehnt(repo):
    http = FalschesHttp()
    assert _leser(http).commits(repo)["ok"] is False
    assert http.aufrufe == []


@pytest.mark.parametrize("pfad", ["../../etc/passwd", "/etc/passwd", "docs/../../x", "a\\..\\b", ""])
def test_datei_pfad_ohne_punktpunkt(pfad):
    http = FalschesHttp()
    assert _leser(http).datei("Flissel/vibemind", pfad)["ok"] is False
    assert http.aufrufe == []


def test_datei_dekodiert_und_schneidet_ab():
    gross = base64.b64encode(("x" * 100_050).encode()).decode()
    http = FalschesHttp({f"{API}/repos/Flissel/vibemind/contents/": (200, {"encoding": "base64", "content": gross})})
    r = _leser(http).datei("Flissel/vibemind", "docs/a.md", ref="master")
    assert r["ok"] and r["abgeschnitten"] is True and len(r["inhalt"]) == 100_000
    assert http.aufrufe[0][1] == f"{API}/repos/Flissel/vibemind/contents/docs/a.md?ref=master"


def test_datei_ordner_listet_eintraege():
    http = FalschesHttp({f"{API}/repos/Flissel/vibemind/contents/": (200, [{"name": "a.md", "type": "file"}])})
    r = _leser(http).datei("Flissel/vibemind", "docs")
    assert r["ok"] and r["eintraege"] == [{"name": "a.md", "typ": "file"}]


def test_issues_ohne_prs():
    http = FalschesHttp({f"{API}/repos/Flissel/vibemind/issues": (200, [
        {"number": 1, "title": "Fehler", "state": "open", "user": {"login": "a"}, "updated_at": "t",
         "comments": 0, "labels": [{"name": "bug"}], "html_url": "u1"},
        {"number": 2, "title": "PR", "state": "open", "pull_request": {}, "user": {"login": "a"}}])})
    r = _leser(http).issues("Flissel/vibemind")
    assert [i["nummer"] for i in r["issues"]] == [1]
    assert r["issues"][0]["labels"] == ["bug"]


def test_max_anzahl_begrenzt():
    http = FalschesHttp()
    leser = _leser(http)
    leser.prs("Flissel/vibemind", max_anzahl=500)
    assert "per_page=50" in http.aufrufe[-1][1]
    leser.commits("Flissel/vibemind", max_anzahl=0)
    assert "per_page=1" in http.aufrufe[-1][1]


def test_unbekannter_zustand_abgelehnt():
    http = FalschesHttp()
    assert _leser(http).issues("Flissel/vibemind", zustand="offen; rm")["ok"] is False
    assert http.aufrufe == []


def test_commits_mit_branch():
    http = FalschesHttp({f"{API}/repos/Flissel/vibemind/commits": (200, [
        {"sha": "abcdef1234567890", "commit": {"message": "feat: x\n\nkoerper",
                                               "author": {"name": "F", "date": "d"}}}])})
    r = _leser(http).commits("Flissel/vibemind", branch="feat/x")
    assert r["commits"] == [{"sha": "abcdef123456", "nachricht": "feat: x", "autor": "F", "datum": "d"}]
    assert "sha=feat/x" in http.aufrufe[0][1]


def test_token_nie_in_ergebnis_oder_fehler():
    http = FalschesHttp(fehler=RuntimeError(f"bad {TOKEN}"))
    r = _leser(http).repos("Flissel")
    assert TOKEN not in json.dumps(r)
    assert r["ok"] is False and r["meldung"].endswith("RuntimeError")


def test_github_fehler_ohne_token():
    assert TOKEN not in str(GithubFehler("GitHub antwortet 500"))


def test_http_status_fehler_gemeldet():
    http = FalschesHttp({f"{API}/repos/Flissel/nix/commits": (404, {"message": "Not Found"})})
    r = _leser(http).commits("Flissel/nix")
    assert r == {"ok": False, "meldung": "GitHub: nicht gefunden (404)"}


@pytest.mark.parametrize("zustand,text", [("darf_schreiben", "gesperrt: Token darf schreiben"),
                                          ("fehlt", "kein Token"), ("abgelehnt", "abgelehnt"),
                                          ("unklar", "unklar")])
def test_gesperrter_zugang_meldet_ohne_anfrage(zustand, text):
    http = FalschesHttp()
    leser = _leser(http, Flissel=zustand)
    assert text in leser.repos("Flissel")["meldung"]
    assert text in leser.issues("Flissel/vibemind")["meldung"]
    assert http.aufrufe == []


def test_repos_eigener_login_und_org():
    http = FalschesHttp({f"{API}/user": (200, {"login": "Flissel"}),
                         f"{API}/user/repos": (200, [_repo("a", 1)]),
                         f"{API}/orgs/vibemind-space/repos": (200, [_repo("b", 1)])})
    leser = GithubLeser({"Flissel": Zugang("Flissel", TOKEN, "bereit"),
                         "vibemind-space": Zugang("vibemind-space", "t2", "bereit")}, http.get)
    assert leser.repos("Flissel")["repos"][0] == {"name": "a", "privat": True, "pushed_at": _repo("a", 1)["pushed_at"],
                                                  "offene_issues": 2, "standard_branch": "master",
                                                  "beschreibung": "d"}
    assert leser.repos("flissel")["ok"]          # Gross/klein egal, Login zwischengespeichert
    urls = [u for _, u, _ in http.aufrufe]
    assert urls.count(f"{API}/user") == 1
    assert f"{API}/user/repos?affiliation=owner&sort=pushed&per_page=100&page=1" in urls
    http.antworten[f"{API}/user"] = (200, {"login": "Flissel"})
    assert leser.repos("vibemind-space")["repos"][0]["name"] == "b"
    assert f"{API}/orgs/vibemind-space/repos?sort=pushed&per_page=100&page=1" in [u for _, u, _ in http.aufrufe]


def test_repos_blaettert_alle_seiten():
    basis = f"{API}/user/repos?affiliation=owner&sort=pushed&per_page=100"
    http = FalschesHttp({f"{API}/user": (200, {"login": "Flissel"}),
                         f"{basis}&page=1": (200, [_repo(f"a{i}", 1) for i in range(100)]),
                         f"{basis}&page=2": (200, [_repo(f"b{i}", 1) for i in range(38)])})
    r = _leser(http).repos("Flissel")
    assert len(r["repos"]) == 138 and r["abgeschnitten"] is False
    urls = [u for _, u, _ in http.aufrufe]
    assert f"{basis}&page=1" in urls and f"{basis}&page=2" in urls and f"{basis}&page=3" not in urls


def test_repos_hoechstens_zehn_seiten():
    http = FalschesHttp({f"{API}/user": (200, {"login": "Flissel"}),
                         f"{API}/user/repos": (200, [_repo("x", 1) for _ in range(100)])})
    r = _leser(http).repos("Flissel")
    assert len(r["repos"]) == 1000 and r["abgeschnitten"] is True
    assert sum(1 for _, u, _ in http.aufrufe if "/user/repos" in u) == 10


def test_besitzer_aus_umgebung_crlf_und_fehlend():
    z = besitzer_aus_umgebung({"GITHUB_BESITZER": "Flissel=A,Vibemind-LAB=B", "A": "tok\r\n"})
    assert z["Flissel"].token == "tok" and z["Flissel"].zustand == "unklar"
    assert z["Vibemind-LAB"].zustand == "fehlt" and z["Vibemind-LAB"].token is None


def test_besitzer_aus_umgebung_leer():
    assert besitzer_aus_umgebung({}) == {}
    z = besitzer_aus_umgebung({"GITHUB_BESITZER": " Flissel = A ", "A": "  \r\n"})
    assert z["Flissel"].zustand == "fehlt"


class FalschesSenden:
    """Schreibprobe-Attrappe: Status je (Methode, Pfad-Ende), Vorgabe 403."""

    def __init__(self, status: dict[str, int] | None = None, vorgabe: int = 403, fehler: Exception | None = None,
                 kopf: dict[str, str] | None = None) -> None:
        self.status, self.vorgabe, self.fehler = status or {}, vorgabe, fehler
        self.kopf = kopf or {}
        self.aufrufe: list[tuple[str, str, dict, dict]] = []

    def __call__(self, methode: str, url: str, kopf: dict[str, str], koerper: dict) -> tuple[int, dict]:
        self.aufrufe.append((methode, url, kopf, koerper))
        if self.fehler is not None:
            raise self.fehler
        pfad = url.split("/repos/Flissel/vibemind/", 1)[-1]
        return self.status.get(f"{methode} {pfad}", self.vorgabe), self.kopf


PROBEN = ["POST labels", "PUT contents/.vibemind-probe", "POST git/refs", "POST pulls", "POST hooks",
          "PUT actions/permissions"]


def test_schreibproben_legen_nie_etwas_an():
    senden = FalschesSenden()
    assert nur_lesend_pruefen(TOKEN, "Flissel/vibemind", senden) == ("bereit", None)
    assert [f"{m} {u.split('/repos/Flissel/vibemind/', 1)[1]}" for m, u, _, _ in senden.aufrufe] == PROBEN
    koerper = {f"{m} {u.split('/repos/Flissel/vibemind/', 1)[1]}": k for m, u, _, k in senden.aufrufe}
    # git/refs prueft bei leerem Koerper erst das Schema (422 auch fuer Nur-Lese-Tokens, gemessen 26.10.):
    # gueltig geformt, aber mit einem SHA, den es nie gibt -> Nur-Lese 403, Schreib-Token nie ein Ref.
    assert koerper.pop("POST git/refs") == {"ref": "refs/heads/vibemind-probe", "sha": "0" * 40}
    assert all(k == {} for k in koerper.values())
    assert all(kopf["Authorization"] == f"Bearer {TOKEN}" for _, _, kopf, _ in senden.aufrufe)


@pytest.mark.parametrize("probe", PROBEN)
@pytest.mark.parametrize("status,erwartet", [(422, "darf_schreiben"), (201, "darf_schreiben"),
                                             (200, "darf_schreiben"), (401, "abgelehnt"), (404, "unklar"),
                                             (500, "unklar")])
def test_jede_probe_sperrt(probe, status, erwartet):
    senden = FalschesSenden({probe: status})
    assert nur_lesend_pruefen(TOKEN, "Flissel/vibemind", senden)[0] == erwartet


def test_nur_lesend_pruefen_ausnahme_ist_unklar():
    assert nur_lesend_pruefen(TOKEN, "Flissel/vibemind", FalschesSenden(fehler=OSError("x")))[0] == "unklar"


def test_nur_lesend_pruefen_falsches_repo_ohne_anfrage():
    senden = FalschesSenden()
    assert nur_lesend_pruefen(TOKEN, "../x", senden)[0] == "unklar" and senden.aufrufe == []


def test_ablaufdatum_nur_datum():
    senden = FalschesSenden(kopf={"github-authentication-token-expiration": "2027-01-24 13:05:00 +0100"})
    assert nur_lesend_pruefen(TOKEN, "Flissel/vibemind", senden) == ("bereit", "2027-01-24")
    senden = FalschesSenden(kopf={"github-authentication-token-expiration": "kaputt"})
    assert nur_lesend_pruefen(TOKEN, "Flissel/vibemind", senden) == ("bereit", None)


def test_ueberblick_nur_bereite_besitzer_und_tage():
    http = FalschesHttp({f"{API}/user": (200, {"login": "Flissel"}),
                         f"{API}/user/repos": (200, [_repo("neu", 2), _repo("alt", 30)])})
    leser = _leser(http, Flissel="bereit", Vibemind_LAB="darf_schreiben")
    r = leser.ueberblick(tage=7)
    assert r["ok"] is True
    assert [x["name"] for x in r["besitzer"]["Flissel"]["repos"]] == ["neu"]
    assert r["gesperrt"] == {"Vibemind-LAB": "darf_schreiben"}
    assert all("Vibemind-LAB" not in u for _, u, _ in http.aufrufe)


def _lauf(nummer: int, name: str, status: str, ergebnis: str | None, branch: str = "master") -> dict:
    return {"id": 9000 + nummer, "run_number": nummer, "name": name, "event": "push", "status": status,
            "conclusion": ergebnis, "head_branch": branch, "head_sha": "abcdef1234567890",
            "display_title": f"feat: lauf {nummer}", "created_at": "c", "updated_at": "u",
            "html_url": f"https://github.com/Flissel/vibemind/actions/runs/{9000 + nummer}"}


def test_ci_laeufe_mit_ergebnis_und_zusammenfassung():
    http = FalschesHttp({f"{API}/repos/Flissel/vibemind/actions/runs": (200, {"total_count": 3, "workflow_runs": [
        _lauf(3, "Tests", "in_progress", None), _lauf(2, "Tests", "completed", "failure"),
        _lauf(1, "Lint", "completed", "success")]})})
    r = _leser(http).ci("Flissel/vibemind")
    assert r["ok"] is True and r["repo"] == "Flissel/vibemind"
    assert r["laeufe"][1] == {"nummer": 2, "workflow": "Tests", "titel": "feat: lauf 2", "anlass": "push",
                              "status": "completed", "ergebnis": "failure", "branch": "master",
                              "sha": "abcdef123456", "gestartet": "c", "geaendert": "u",
                              "url": "https://github.com/Flissel/vibemind/actions/runs/9002"}
    # je Workflow der juengste Lauf: Tests laeuft noch, Lint gruen
    assert r["letzter_je_workflow"] == {"Tests": "in_progress", "Lint": "success"}
    assert http.aufrufe[0][1] == f"{API}/repos/Flissel/vibemind/actions/runs?per_page=10"
    assert {m for m, _, _ in http.aufrufe} == {"GET"}


def test_ci_mit_branch_und_begrenzt():
    http = FalschesHttp({f"{API}/repos/Flissel/vibemind/actions/runs": (200, {"workflow_runs": []})})
    r = _leser(http).ci("Flissel/vibemind", branch="feat/x", max_anzahl=500)
    assert r["ok"] and r["laeufe"] == [] and r["letzter_je_workflow"] == {}
    assert http.aufrufe[0][1] == f"{API}/repos/Flissel/vibemind/actions/runs?per_page=50&branch=feat/x"


@pytest.mark.parametrize("branch", ["../x", "a b", "x&per_page=100", "a..b"])
def test_ci_branch_unzulaessig_ohne_anfrage(branch):
    http = FalschesHttp()
    assert _leser(http).ci("Flissel/vibemind", branch=branch)["ok"] is False
    assert http.aufrufe == []


def test_ci_fremder_besitzer_und_403():
    http = FalschesHttp({f"{API}/repos/Flissel/vibemind/actions/runs": (403, {"message": "x"})})
    leser = _leser(http)
    assert "nicht freigegeben" in leser.ci("evil/repo")["meldung"]
    assert http.aufrufe == []
    assert leser.ci("Flissel/vibemind") == {"ok": False, "meldung": "GitHub: kein Zugriff oder Abruf-Limit (403)"}
