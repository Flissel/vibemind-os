"""Gitea nur lesend fuer den Werkzeug-Container ``rowboat-werkzeuge`` (Mini-PC).

Gitea laeuft auf derselben VM (``debian-coding-gitea-1``, Port 3002) und wird spaeter Ziel der
GitHub-Sicherung; ``gitea_repos`` zeigt deshalb auch den Spiegel-Stand. Ein Token mit nur
``read:*``-Scopes (``rowboat-zugang gitea-token``). Wie bei GitHub: ausschliesslich GET, beim Start
eine Schreibprobe mit leerem Koerper; gemessen an Gitea 1.23 (26.10.) bekommt ein Lese-Token ueberall
403, ein Schreib-Token 422. Jede andere Antwort als 403 sperrt (fail-closed).
Der Token erscheint nie in Ergebnissen, Meldungen oder Logs; Fehler nennen nur den Ausnahme-Typ.
"""
from __future__ import annotations

import base64
import re
import urllib.parse
from typing import Any, Callable, Mapping

from github_lesen import _REF, MAX_ZEICHEN, HttpGet, HttpSenden, Zustand, _fehler, _klemmen

BASIS_VORGABE = "http://127.0.0.1:3002"
_REPO = re.compile(r"^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$")
_ZUSTAENDE = ("open", "closed", "all")
_KEIN_DATUM = "0001-01-01"

# Je eine schreibende Anfrage pro Scope-Bereich (repository, organization, admin; issue nur mit Repo).
SCHREIBPROBEN = ("/user/repos", "/repos/migrate", "/orgs", "/admin/users")
SCHREIBPROBEN_REPO = ("issues", "labels")

_SPERRGRUND = {
    "darf_schreiben": "gesperrt: Token darf schreiben",
    "fehlt": "fehlt: kein Token eingetragen",
    "abgelehnt": "gesperrt: Token abgelehnt (401)",
    "unklar": "gesperrt: Nur-Lese-Probe unklar",
}


class GiteaFehler(Exception):
    """Fehler beim Lesen; die Meldung enthaelt nie einen Token."""


def gitea_aus_umgebung(umgebung: Mapping[str, str]) -> tuple[str, str | None, Zustand]:
    """``GITEA_URL`` (ohne Schraegstrich am Ende) und ``GITEA_LESEN`` -> (basis, token, zustand).

    Ohne Token ``fehlt``, sonst vorerst ``unklar`` (erst die Startprobe macht ihn ``bereit``)."""
    basis = (umgebung.get("GITEA_URL") or "").strip().rstrip("/") or BASIS_VORGABE
    token = (umgebung.get("GITEA_LESEN") or "").strip() or None
    return basis, token, "unklar" if token else "fehlt"


def _kopf(token: str) -> dict[str, str]:
    return {"Authorization": f"token {token}", "Accept": "application/json", "User-Agent": "vibemind-werkzeuge"}


def _repo_gueltig(repo: str) -> bool:
    if not isinstance(repo, str) or not _REPO.match(repo):
        return False
    return all(teil not in (".", "..") for teil in repo.split("/"))


def nur_lesend_pruefen(basis: str, token: str, repo: str | None, senden: HttpSenden) -> Zustand:
    """Alle Schreibproben (``repo`` optional fuer die Repo-Proben) muessen 403 liefern.

    422 oder 2xx -> ``darf_schreiben``; 401 -> ``abgelehnt``; anderes oder Ausnahme -> ``unklar``."""
    if repo is not None and not _repo_gueltig(repo):
        return "unklar"
    pfade = list(SCHREIBPROBEN) + ([f"/repos/{repo}/{p}" for p in SCHREIBPROBEN_REPO] if repo else [])
    for pfad in pfade:
        try:
            status, _ = senden("POST", f"{basis}/api/v1{pfad}", _kopf(token), {})
        except Exception:  # noqa: BLE001 - jede Ausnahme heisst: nicht sicher, also gesperrt
            return "unklar"
        if status == 403:
            continue
        if status == 422 or 200 <= status < 300:
            return "darf_schreiben"
        if status == 401:
            return "abgelehnt"
        return "unklar"
    return "bereit"


class GiteaLeser:
    """Lesende Gitea-Werkzeuge; jede Methode liefert ``{"ok": True, ...}`` oder ``{"ok": False, "meldung"}``."""

    def __init__(self, basis: str, token: str | None, zustand: Zustand, get: HttpGet) -> None:
        self.basis = basis
        self.token = token
        self.zustand: Zustand = zustand
        self._get = get

    def _holen(self, pfad: str) -> Any:
        if self.zustand != "bereit" or not self.token:
            raise GiteaFehler(f"Gitea-Zugang {_SPERRGRUND.get(self.zustand, _SPERRGRUND['unklar'])}")
        try:
            status, daten = self._get(f"{self.basis}/api/v1{pfad}", _kopf(self.token))
        except Exception as e:  # noqa: BLE001 - Meldung nur mit Typ, nie mit Text (Token-Leck)
            raise GiteaFehler(f"Gitea-Anfrage fehlgeschlagen: {type(e).__name__}") from None
        if 200 <= status < 300:
            return daten
        if status == 404:
            raise GiteaFehler("Gitea: nicht gefunden (404)")
        if status == 401:
            raise GiteaFehler("Gitea: Token abgelehnt (401)")
        if status == 403:
            raise GiteaFehler("Gitea: kein Zugriff (403)")
        raise GiteaFehler(f"Gitea antwortet {status}")

    @staticmethod
    def _sicher(fn: Callable[[], dict]) -> dict:
        try:
            return fn()
        except GiteaFehler as e:
            return _fehler(str(e))
        except Exception as e:  # noqa: BLE001
            return _fehler(f"Fehler beim Auswerten: {type(e).__name__}")

    @staticmethod
    def _pruefe_repo(repo: str) -> None:
        if not _repo_gueltig(repo):
            raise GiteaFehler("Repo muss die Form besitzer/name haben")

    @staticmethod
    def _pruefe_ref(ref: str, name: str) -> str:
        if not _REF.match(ref) or ".." in ref:
            raise GiteaFehler(f"{name} enthaelt unzulaessige Zeichen")
        return urllib.parse.quote(ref, safe="/")

    def repos(self) -> dict:
        def tun() -> dict:
            daten = self._holen("/repos/search?limit=50&sort=updated&order=desc") or {}
            repos = []
            for r in daten.get("data") or []:
                spiegel_zeit = r.get("mirror_updated")
                repos.append({"name": r.get("full_name"), "privat": r.get("private"), "spiegel": r.get("mirror"),
                              "spiegel_aktualisiert": None if not spiegel_zeit or
                              str(spiegel_zeit).startswith(_KEIN_DATUM) else spiegel_zeit,
                              "geaendert": r.get("updated_at"), "leer": r.get("empty"),
                              "archiviert": r.get("archived"), "standard_branch": r.get("default_branch"),
                              "groesse_kb": r.get("size"), "beschreibung": r.get("description")})
            return {"ok": True, "repos": repos}
        return self._sicher(tun)

    def issues(self, repo: str, zustand: str = "open", max_anzahl: int = 20) -> dict:
        def tun() -> dict:
            self._pruefe_repo(repo)
            if zustand not in _ZUSTAENDE:
                raise GiteaFehler("zustand muss open, closed oder all sein")
            daten = self._holen(f"/repos/{repo}/issues?state={zustand}&type=issues&limit={_klemmen(max_anzahl)}")
            eintraege = [{"nummer": i.get("number"), "titel": i.get("title"), "zustand": i.get("state"),
                          "von": (i.get("user") or {}).get("login"), "geaendert": i.get("updated_at"),
                          "kommentare": i.get("comments"),
                          "labels": [lab.get("name") for lab in i.get("labels") or []], "url": i.get("html_url")}
                         for i in daten or []]
            return {"ok": True, "repo": repo, "issues": eintraege}
        return self._sicher(tun)

    def commits(self, repo: str, branch: str = "", max_anzahl: int = 20) -> dict:
        def tun() -> dict:
            self._pruefe_repo(repo)
            pfad = f"/repos/{repo}/commits?limit={_klemmen(max_anzahl)}&stat=false&verification=false&files=false"
            if branch:
                pfad += f"&sha={self._pruefe_ref(branch, 'branch')}"
            eintraege = []
            for c in self._holen(pfad) or []:
                commit = c.get("commit") or {}
                autor = commit.get("author") or {}
                eintraege.append({"sha": str(c.get("sha", ""))[:12],
                                  "nachricht": str(commit.get("message", "")).split("\n", 1)[0],
                                  "autor": autor.get("name"), "datum": autor.get("date")})
            return {"ok": True, "repo": repo, "commits": eintraege}
        return self._sicher(tun)

    def datei(self, repo: str, pfad: str = "README.md", ref: str = "") -> dict:
        def tun() -> dict:
            self._pruefe_repo(repo)
            if (not pfad or pfad.startswith("/") or "\\" in pfad
                    or any(teil in (".", "..") for teil in pfad.split("/"))):
                raise GiteaFehler("pfad muss relativ sein und darf kein .. enthalten")
            url = f"/repos/{repo}/contents/{urllib.parse.quote(pfad, safe='/')}"
            if ref:
                url += f"?ref={self._pruefe_ref(ref, 'ref')}"
            daten = self._holen(url)
            if isinstance(daten, list):
                return {"ok": True, "repo": repo, "pfad": pfad,
                        "eintraege": [{"name": e.get("name"), "typ": e.get("type")} for e in daten]}
            if (daten or {}).get("encoding") != "base64":
                raise GiteaFehler("Datei ist zu gross oder kein Text (kein base64-Inhalt)")
            text = base64.b64decode(daten.get("content", "")).decode("utf-8", errors="replace")
            return {"ok": True, "repo": repo, "pfad": pfad, "inhalt": text[:MAX_ZEICHEN],
                    "abgeschnitten": len(text) > MAX_ZEICHEN}
        return self._sicher(tun)
