"""MCP-Dienst ``vibemind-werkzeuge`` (Container ``rowboat-werkzeuge`` auf dem Mini-PC).

Stellt Rowboat genau acht Werkzeuge bereit, alle nur lesend: sieben fuer GitHub (je Besitzer ein
fein granulierter Nur-Lese-Token, beim Start per Schreibprobe geprueft, fail-closed) und
``pc_suchen`` (Weiterleitung an den Suchdienst am PC). Bindet nur ``127.0.0.1`` (Host-Netz),
Pfad ``/mcp``, dazu ``GET /health``. Die Geheimnisse kommen aus ``werkzeuge.env`` (env_file) und
sind fuer den Rowboat-Container unsichtbar; in Rowboats ``mcp.json`` steht nur die URL.
"""
import functools
import logging
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Mapping

sys.path.insert(0, str(Path(__file__).resolve().parent))

import pc_suche_relay as relay  # noqa: E402
from github_lesen import (  # noqa: E402
    GithubLeser,
    HttpGet,
    HttpSenden,
    Zugang,
    besitzer_aus_umgebung,
    http_get,
    http_senden,
    nur_lesend_pruefen,
)

log = logging.getLogger("werkzeuge")

HOST = "127.0.0.1"
PORT_VORGABE = 8150
PC_SUCHE_URL_VORGABE = "http://100.84.127.84:8149"
# Ein "unklar" gepruefter Zugang (z. B. GitHub beim Start nicht erreichbar) wird nach 30 min neu geprueft.
NACHPRUEFEN_S = 30 * 60


def zugang_pruefen(besitzer: str, zugang: Zugang, get: HttpGet, senden: HttpSenden) -> None:
    """Setzt ``zugang.zustand``: privates Repo aus der Liste des Tokens holen, dann Schreibprobe.

    Ohne privates Repo ``unklar`` (gesperrt): an einem fremden oeffentlichen Repo saehe auch ein
    Schreib-Token nur 403. Genau eine Logzeile je Pruefung, nie mit Token (Ablauf nur als Datum)."""
    ablauf = None
    if zugang.token:
        probe = GithubLeser({besitzer: Zugang(besitzer, zugang.token, "bereit")}, get)
        liste = probe.repos(besitzer)
        repos = (liste.get("repos") or []) if liste.get("ok") else []
        privat = next((r["name"] for r in repos if r.get("privat") is True and r.get("name")), None)
        if privat:
            zugang.zustand, ablauf = nur_lesend_pruefen(zugang.token, f"{besitzer}/{privat}", senden)
        else:
            zugang.zustand = "unklar"
    if ablauf:
        log.info("Zugang %s: %s (Token läuft ab %s)", besitzer, zugang.zustand, ablauf)
    else:
        log.info("Zugang %s: %s", besitzer, zugang.zustand)


def zugaenge_beim_start(umgebung: Mapping[str, str], get: HttpGet, senden: HttpSenden) -> dict[str, Zugang]:
    """Prueft jeden Token (``zugang_pruefen``). Nur ``bereit`` wird benutzt, alles andere ist gesperrt."""
    zugaenge = besitzer_aus_umgebung(umgebung)
    for besitzer, zugang in zugaenge.items():
        zugang_pruefen(besitzer, zugang, get, senden)
    return zugaenge


class _Kontext:
    leser: GithubLeser = GithubLeser({}, http_get)
    zugaenge: dict[str, Zugang] = {}
    get: HttpGet = http_get
    senden: HttpSenden = http_senden
    uhr: Callable[[], float] = time.monotonic
    letzte_probe: dict[str, float] = {}
    sperre = threading.Lock()
    pc_url: str = PC_SUCHE_URL_VORGABE
    pc_schluessel: str | None = None
    post_json: relay.HttpPostJson = relay.http_post_json


_K = _Kontext()


def _unklare_nachpruefen() -> None:
    """Selbstheilung: ``unklar`` gesperrte Zugaenge hoechstens alle 30 min neu pruefen.
    ``darf_schreiben``, ``abgelehnt`` und ``fehlt`` bleiben gesperrt bis zum Neustart."""
    with _K.sperre:
        jetzt = _K.uhr()
        for besitzer, zugang in _K.zugaenge.items():
            if zugang.zustand != "unklar" or not zugang.token:
                continue
            if jetzt - _K.letzte_probe.get(besitzer, jetzt) < NACHPRUEFEN_S:
                continue
            _K.letzte_probe[besitzer] = jetzt
            zugang_pruefen(besitzer, zugang, _K.get, _K.senden)


# --- Werkzeuge (Docstrings = Beschreibung fuer das Modell) ---------------------------------------

def github_repos(besitzer: str) -> dict:
    """Listet die Repos eines freigegebenen GitHub-Besitzers (z. B. Flissel, Vibemind-LAB), zuletzt
    geaenderte zuerst: Name, privat, letzter Push, offene Issues, Standard-Branch. Nur lesend."""
    _unklare_nachpruefen()
    return _K.leser.repos(besitzer)


def github_issues(repo: str, zustand: str = "open", max_anzahl: int = 20) -> dict:
    """Issues eines Repos (``besitzer/name``) ohne Pull Requests. zustand: open, closed oder all;
    max_anzahl hoechstens 50. Nur lesend."""
    _unklare_nachpruefen()
    return _K.leser.issues(repo, zustand, max_anzahl)


def github_prs(repo: str, zustand: str = "open", max_anzahl: int = 20) -> dict:
    """Pull Requests eines Repos (``besitzer/name``): Titel, Branches, Entwurf, Autor. zustand: open,
    closed oder all; max_anzahl hoechstens 50. Nur lesend."""
    _unklare_nachpruefen()
    return _K.leser.prs(repo, zustand, max_anzahl)


def github_commits(repo: str, branch: str = "", max_anzahl: int = 20) -> dict:
    """Letzte Commits eines Repos (``besitzer/name``), optional eines Branches: Kurz-SHA, erste
    Zeile der Nachricht, Autor, Datum. max_anzahl hoechstens 50. Nur lesend."""
    _unklare_nachpruefen()
    return _K.leser.commits(repo, branch, max_anzahl)


def github_ci(repo: str, branch: str = "", max_anzahl: int = 10) -> dict:
    """CI-Stand eines Repos (``besitzer/name``): letzte GitHub-Actions-Laeufe, optional eines
    Branches (Workflow, Status, Ergebnis wie success/failure, Commit, Link) und je Workflow der
    aktuelle Stand. max_anzahl hoechstens 50. Nur lesend, startet nie einen Lauf."""
    _unklare_nachpruefen()
    return _K.leser.ci(repo, branch, max_anzahl)


def github_datei(repo: str, pfad: str = "README.md", ref: str = "") -> dict:
    """Liest eine Datei (Text, hoechstens 100 000 Zeichen) oder listet einen Ordner aus einem Repo
    (``besitzer/name``). pfad relativ ohne ``..``; ref = Branch, Tag oder SHA. Nur lesend."""
    _unklare_nachpruefen()
    return _K.leser.datei(repo, pfad, ref)


def github_ueberblick(tage: int = 7) -> dict:
    """GitHub-Lagebild: je freigegebenem Besitzer die Repos mit Push in den letzten ``tage`` Tagen
    (mit offenen Issues), dazu gesperrte Zugaenge. Nur lesend."""
    _unklare_nachpruefen()
    return _K.leser.ueberblick(tage)


def pc_suchen(anfrage: str, art: str = "name", ordner: str = "", max_treffer: int = 20) -> dict:
    """Sucht auf dem PC des Nutzers (Desktop und Dokumente) nach Dateinamen (art="name") oder
    Textinhalten (art="inhalt"). ordner optional (muss darin liegen). Ist der PC aus, kommt die
    Meldung "PC ist aus". Bei abgeschnitten=true ist die Suche unvollstaendig. Nur lesend."""
    return relay.pc_suchen(anfrage, art, ordner, max_treffer, url=_K.pc_url, schluessel=_K.pc_schluessel,
                           post=_K.post_json)


WERKZEUGE: list[Callable[..., dict]] = [github_repos, github_issues, github_prs, github_commits, github_ci,
                                        github_datei, github_ueberblick, pc_suchen]


def im_thread(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Synchrones Werkzeug als async-Huelle im Arbeiter-Thread (Muster spaces/marketing/claw/mcp_huelle):
    langsame GitHub- oder PC-Anfragen halten /health und andere Aufrufe nicht an."""
    import anyio

    @functools.wraps(fn)
    async def huelle(**kwargs: Any) -> Any:
        return await anyio.to_thread.run_sync(functools.partial(fn, **kwargs))

    return huelle


def bauen(umgebung: Mapping[str, str] = os.environ, *, get: HttpGet = http_get, senden: HttpSenden = http_senden,
          post_json: relay.HttpPostJson = relay.http_post_json, uhr: Callable[[], float] = time.monotonic):
    """Server mit den acht Werkzeugen; prueft vorher die GitHub-Zugaenge (Startprobe)."""
    from mcp.server.fastmcp import FastMCP
    from starlette.responses import JSONResponse

    _K.zugaenge = zugaenge_beim_start(umgebung, get, senden)
    _K.leser = GithubLeser(_K.zugaenge, get)
    _K.get, _K.senden, _K.uhr = get, senden, uhr
    _K.letzte_probe = {b: uhr() for b in _K.zugaenge}
    _K.pc_url = (umgebung.get("PC_SUCHE_URL") or "").strip() or PC_SUCHE_URL_VORGABE
    _K.pc_schluessel = (umgebung.get("PC_SUCHE_KEY") or "").strip() or None
    _K.post_json = post_json
    port = int((umgebung.get("WERKZEUGE_PORT") or "").strip() or PORT_VORGABE)

    mcp = FastMCP("vibemind-werkzeuge", host=HOST, port=port, log_level="WARNING",
                  stateless_http=True, json_response=True, streamable_http_path="/mcp")
    for fn in WERKZEUGE:
        mcp.tool()(im_thread(fn))

    @mcp.custom_route("/health", methods=["GET"])
    async def _gesund(_request):  # noqa: ANN001
        return JSONResponse({"status": "ok"})

    return mcp


def main() -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    log.addHandler(handler)
    log.setLevel(logging.INFO)
    log.propagate = False
    mcp = bauen(os.environ)
    log.info("vibemind-werkzeuge: %s:%d/mcp", HOST, mcp.settings.port)
    mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
