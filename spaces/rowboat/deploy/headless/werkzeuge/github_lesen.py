"""GitHub nur lesend fuer den Werkzeug-Container ``rowboat-werkzeuge`` (Mini-PC).

Je Besitzer ein fein granulierter Nur-Lese-Token. Der Leser stellt ausschliesslich GET-Anfragen;
die einzige Ausnahme ist die Schreibprobe ``nur_lesend_pruefen`` beim Start (POST mit leerem
Koerper, die ein Nur-Lese-Token mit 403 ablehnt). Ein Token, der schreiben darf, wird gesperrt.
Tokens erscheinen nie in Ergebnissen, Meldungen oder Logs; Fehler nennen nur den Ausnahme-Typ.
"""
from __future__ import annotations

import base64
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Literal, Mapping

HttpGet = Callable[[str, dict[str, str]], tuple[int, Any]]
HttpPost = Callable[[str, dict[str, str], dict], tuple[int, Any]]
Zustand = Literal["bereit", "fehlt", "darf_schreiben", "abgelehnt", "unklar"]

API = "https://api.github.com"
MAX_SEITE = 50
MAX_ZEICHEN = 100_000
ZEITLIMIT_S = 15.0

_REPO = re.compile(r"^[A-Za-z0-9-]+/[A-Za-z0-9._-]+$")
_REF = re.compile(r"^[A-Za-z0-9._/-]{1,200}$")
_ZUSTAENDE = ("open", "closed", "all")

_SPERRGRUND = {
    "darf_schreiben": "gesperrt: Token darf schreiben",
    "fehlt": "fehlt: kein Token eingetragen",
    "abgelehnt": "gesperrt: Token abgelehnt (401)",
    "unklar": "gesperrt: Nur-Lese-Probe unklar",
}


class GithubFehler(Exception):
    """Fehler beim Lesen; die Meldung enthaelt nie einen Token."""


@dataclass
class Zugang:
    besitzer: str
    token: str | None
    zustand: Zustand


def besitzer_aus_umgebung(umgebung: Mapping[str, str]) -> dict[str, Zugang]:
    """``GITHUB_BESITZER="Flissel=GITHUB_LESEN_FLISSEL,..."`` -> Zugang je Besitzer.

    Ohne Token (Variable fehlt oder leer) ist der Zustand ``fehlt``, sonst vorerst ``unklar``
    (erst die Startprobe macht ihn ``bereit``). Werte werden gestrippt (CRLF aus Env-Dateien)."""
    zugaenge: dict[str, Zugang] = {}
    for paar in (umgebung.get("GITHUB_BESITZER") or "").split(","):
        if "=" not in paar:
            continue
        besitzer, variable = (t.strip() for t in paar.split("=", 1))
        if not besitzer or not variable:
            continue
        token = (umgebung.get(variable) or "").strip() or None
        zugaenge[besitzer] = Zugang(besitzer, token, "unklar" if token else "fehlt")
    return zugaenge


def _kopf(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "vibemind-werkzeuge"}


def _repo_gueltig(repo: str) -> bool:
    if not isinstance(repo, str) or not _REPO.match(repo):
        return False
    return all(teil not in (".", "..") for teil in repo.split("/"))


def nur_lesend_pruefen(token: str, repo: str, post: HttpPost) -> Zustand:
    """Schreibprobe: POST /repos/{repo}/labels mit leerem Koerper.

    403 -> ``bereit`` (darf nicht schreiben); 422 oder 2xx -> ``darf_schreiben`` (Schreibrecht da,
    nur der Koerper ist ungueltig); 401 -> ``abgelehnt``; sonst oder Ausnahme -> ``unklar``."""
    if not _repo_gueltig(repo):
        return "unklar"
    try:
        status, _ = post(f"{API}/repos/{repo}/labels", _kopf(token), {})
    except Exception:  # noqa: BLE001 - jede Ausnahme heisst: nicht sicher, also gesperrt
        return "unklar"
    if status == 403:
        return "bereit"
    if status == 422 or 200 <= status < 300:
        return "darf_schreiben"
    if status == 401:
        return "abgelehnt"
    return "unklar"


def _fehler(meldung: str) -> dict:
    return {"ok": False, "meldung": meldung}


def _klemmen(anzahl: int) -> int:
    try:
        return max(1, min(MAX_SEITE, int(anzahl)))
    except (TypeError, ValueError):
        return 20


def _zeit(wert: str | None) -> datetime | None:
    if not wert:
        return None
    try:
        return datetime.fromisoformat(wert.replace("Z", "+00:00"))
    except ValueError:
        return None


class GithubLeser:
    """Lesende GitHub-Werkzeuge; jede Methode liefert ``{"ok": True, ...}`` oder ``{"ok": False, "meldung"}``."""

    def __init__(self, zugaenge: dict[str, Zugang], get: HttpGet,
                 jetzt: Callable[[], datetime] = lambda: datetime.now(timezone.utc)) -> None:
        self._zugaenge = zugaenge
        self._get = get
        self._jetzt = jetzt
        self._login: dict[str, str] = {}

    # --- Hilfen ---------------------------------------------------------------------------------

    def _zugang(self, besitzer: str) -> Zugang:
        for name, zugang in self._zugaenge.items():
            if name.lower() == (besitzer or "").lower():
                if zugang.zustand != "bereit" or not zugang.token:
                    grund = _SPERRGRUND.get(zugang.zustand, _SPERRGRUND["unklar"])
                    raise GithubFehler(f"Zugang für {name} {grund}")
                return zugang
        raise GithubFehler(f"Besitzer {besitzer!r} ist nicht freigegeben")

    def _zugang_fuer_repo(self, repo: str) -> Zugang:
        if not _repo_gueltig(repo):
            raise GithubFehler("Repo muss die Form besitzer/name haben")
        return self._zugang(repo.split("/", 1)[0])

    def _holen(self, zugang: Zugang, pfad: str) -> Any:
        assert zugang.token
        try:
            status, daten = self._get(f"{API}{pfad}", _kopf(zugang.token))
        except Exception as e:  # noqa: BLE001 - Meldung nur mit Typ, nie mit Text (Token-Leck)
            raise GithubFehler(f"GitHub-Anfrage fehlgeschlagen: {type(e).__name__}") from None
        if 200 <= status < 300:
            return daten
        if status == 404:
            raise GithubFehler("GitHub: nicht gefunden (404)")
        if status == 401:
            raise GithubFehler("GitHub: Token abgelehnt (401)")
        if status == 403:
            raise GithubFehler("GitHub: kein Zugriff oder Abruf-Limit (403)")
        raise GithubFehler(f"GitHub antwortet {status}")

    @staticmethod
    def _sicher(fn: Callable[[], dict]) -> dict:
        try:
            return fn()
        except GithubFehler as e:
            return _fehler(str(e))
        except Exception as e:  # noqa: BLE001
            return _fehler(f"Fehler beim Auswerten: {type(e).__name__}")

    def _repos_liste(self, besitzer: str) -> list[dict]:
        zugang = self._zugang(besitzer)
        if zugang.besitzer not in self._login:
            ich = self._holen(zugang, "/user")
            self._login[zugang.besitzer] = str((ich or {}).get("login", ""))
        if self._login[zugang.besitzer].lower() == zugang.besitzer.lower():
            daten = self._holen(zugang, f"/user/repos?affiliation=owner&sort=pushed&per_page={MAX_SEITE}")
        else:
            org = urllib.parse.quote(zugang.besitzer, safe="")
            daten = self._holen(zugang, f"/orgs/{org}/repos?sort=pushed&per_page={MAX_SEITE}")
        return [{"name": r.get("name"), "privat": r.get("private"), "pushed_at": r.get("pushed_at"),
                 "offene_issues": r.get("open_issues_count"), "standard_branch": r.get("default_branch"),
                 "beschreibung": r.get("description")} for r in (daten or [])]

    # --- Werkzeuge ------------------------------------------------------------------------------

    def repos(self, besitzer: str) -> dict:
        return self._sicher(lambda: {"ok": True, "besitzer": besitzer, "repos": self._repos_liste(besitzer)})

    def issues(self, repo: str, zustand: str = "open", max_anzahl: int = 20) -> dict:
        def tun() -> dict:
            zugang = self._zugang_fuer_repo(repo)
            if zustand not in _ZUSTAENDE:
                raise GithubFehler("zustand muss open, closed oder all sein")
            daten = self._holen(zugang, f"/repos/{repo}/issues?state={zustand}&per_page={_klemmen(max_anzahl)}")
            eintraege = [{"nummer": i.get("number"), "titel": i.get("title"), "zustand": i.get("state"),
                          "von": (i.get("user") or {}).get("login"), "geaendert": i.get("updated_at"),
                          "kommentare": i.get("comments"),
                          "labels": [lab.get("name") for lab in i.get("labels") or []],
                          "url": i.get("html_url")}
                         for i in daten or [] if "pull_request" not in i]
            return {"ok": True, "repo": repo, "issues": eintraege}
        return self._sicher(tun)

    def prs(self, repo: str, zustand: str = "open", max_anzahl: int = 20) -> dict:
        def tun() -> dict:
            zugang = self._zugang_fuer_repo(repo)
            if zustand not in _ZUSTAENDE:
                raise GithubFehler("zustand muss open, closed oder all sein")
            daten = self._holen(zugang, f"/repos/{repo}/pulls?state={zustand}&per_page={_klemmen(max_anzahl)}")
            eintraege = [{"nummer": p.get("number"), "titel": p.get("title"), "zustand": p.get("state"),
                          "entwurf": p.get("draft"), "von": (p.get("user") or {}).get("login"),
                          "geaendert": p.get("updated_at"), "kopf": (p.get("head") or {}).get("ref"),
                          "basis": (p.get("base") or {}).get("ref"), "url": p.get("html_url")}
                         for p in daten or []]
            return {"ok": True, "repo": repo, "prs": eintraege}
        return self._sicher(tun)

    def commits(self, repo: str, branch: str = "", max_anzahl: int = 20) -> dict:
        def tun() -> dict:
            zugang = self._zugang_fuer_repo(repo)
            pfad = f"/repos/{repo}/commits?per_page={_klemmen(max_anzahl)}"
            if branch:
                if not _REF.match(branch) or ".." in branch:
                    raise GithubFehler("branch enthaelt unzulaessige Zeichen")
                pfad += f"&sha={urllib.parse.quote(branch, safe='/')}"
            daten = self._holen(zugang, pfad)
            eintraege = []
            for c in daten or []:
                commit = c.get("commit") or {}
                autor = commit.get("author") or {}
                eintraege.append({"sha": str(c.get("sha", ""))[:12],
                                  "nachricht": str(commit.get("message", "")).split("\n", 1)[0],
                                  "autor": autor.get("name"), "datum": autor.get("date")})
            return {"ok": True, "repo": repo, "commits": eintraege}
        return self._sicher(tun)

    def datei(self, repo: str, pfad: str = "README.md", ref: str = "") -> dict:
        def tun() -> dict:
            zugang = self._zugang_fuer_repo(repo)
            if (not pfad or pfad.startswith("/") or "\\" in pfad
                    or any(teil in (".", "..") for teil in pfad.split("/"))):
                raise GithubFehler("pfad muss relativ sein und darf kein .. enthalten")
            url = f"/repos/{repo}/contents/{urllib.parse.quote(pfad, safe='/')}"
            if ref:
                if not _REF.match(ref) or ".." in ref:
                    raise GithubFehler("ref enthaelt unzulaessige Zeichen")
                url += f"?ref={urllib.parse.quote(ref, safe='/')}"
            daten = self._holen(zugang, url)
            if isinstance(daten, list):
                return {"ok": True, "repo": repo, "pfad": pfad,
                        "eintraege": [{"name": e.get("name"), "typ": e.get("type")} for e in daten]}
            if (daten or {}).get("encoding") != "base64":
                raise GithubFehler("Datei ist zu gross oder kein Text (kein base64-Inhalt)")
            text = base64.b64decode(daten.get("content", "")).decode("utf-8", errors="replace")
            return {"ok": True, "repo": repo, "pfad": pfad, "inhalt": text[:MAX_ZEICHEN],
                    "abgeschnitten": len(text) > MAX_ZEICHEN}
        return self._sicher(tun)

    def ueberblick(self, tage: int = 7) -> dict:
        def tun() -> dict:
            grenze = self._jetzt() - timedelta(days=max(1, min(365, int(tage))))
            besitzer: dict[str, dict] = {}
            gesperrt: dict[str, str] = {}
            for name, zugang in self._zugaenge.items():
                if zugang.zustand != "bereit" or not zugang.token:
                    gesperrt[name] = zugang.zustand
                    continue
                try:
                    repos = self._repos_liste(name)
                except GithubFehler as e:
                    besitzer[name] = {"fehler": str(e)}
                    continue
                aktiv = [r for r in repos if (_zeit(r.get("pushed_at")) or grenze) > grenze]
                besitzer[name] = {"repos": aktiv[:20], "anzahl_aktiv": len(aktiv)}
            return {"ok": True, "tage": tage, "besitzer": besitzer, "gesperrt": gesperrt}
        return self._sicher(tun)


# --- Standard-HTTP (stdlib) fuer den Container --------------------------------------------------

def _antwort(roh: bytes) -> Any:
    try:
        return json.loads(roh.decode("utf-8")) if roh else None
    except ValueError:
        return None


def http_get(url: str, kopf: dict[str, str]) -> tuple[int, Any]:
    anfrage = urllib.request.Request(url, headers=kopf, method="GET")
    try:
        with urllib.request.urlopen(anfrage, timeout=ZEITLIMIT_S) as antwort:  # noqa: S310 - feste API-URL
            return antwort.status, _antwort(antwort.read())
    except urllib.error.HTTPError as e:
        return e.code, _antwort(e.read())


def http_post(url: str, kopf: dict[str, str], koerper: dict) -> tuple[int, Any]:
    daten = json.dumps(koerper).encode("utf-8")
    anfrage = urllib.request.Request(url, data=daten, headers={**kopf, "Content-Type": "application/json"},
                                     method="POST")
    try:
        with urllib.request.urlopen(anfrage, timeout=ZEITLIMIT_S) as antwort:  # noqa: S310
            return antwort.status, _antwort(antwort.read())
    except urllib.error.HTTPError as e:
        return e.code, _antwort(e.read())
