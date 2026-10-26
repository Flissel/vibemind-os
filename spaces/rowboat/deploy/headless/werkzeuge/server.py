"""MCP-Dienst ``vibemind-werkzeuge`` (Container ``rowboat-werkzeuge`` auf dem Mini-PC).

Stellt Rowboat genau sieben Werkzeuge bereit, alle nur lesend: sechs fuer GitHub (je Besitzer ein
fein granulierter Nur-Lese-Token, beim Start per Schreibprobe geprueft, fail-closed) und
``pc_suchen`` (Weiterleitung an den Suchdienst am PC). Bindet nur ``127.0.0.1`` (Host-Netz),
Pfad ``/mcp``, dazu ``GET /health``. Die Geheimnisse kommen aus ``werkzeuge.env`` (env_file) und
sind fuer den Rowboat-Container unsichtbar; in Rowboats ``mcp.json`` steht nur die URL.
"""
import functools
import logging
import os
import sys
from pathlib import Path
from typing import Any, Callable, Mapping

sys.path.insert(0, str(Path(__file__).resolve().parent))

import pc_suche_relay as relay  # noqa: E402
from github_lesen import (  # noqa: E402
    GithubLeser,
    HttpGet,
    HttpPost,
    Zugang,
    besitzer_aus_umgebung,
    http_get,
    http_post,
    nur_lesend_pruefen,
)

log = logging.getLogger("werkzeuge")

HOST = "127.0.0.1"
PORT_VORGABE = 8150
PC_SUCHE_URL_VORGABE = "http://100.84.127.84:8149"


def zugaenge_beim_start(umgebung: Mapping[str, str], get: HttpGet, post: HttpPost) -> dict[str, Zugang]:
    """Prueft jeden Token: erstes Repo lesen, dann Schreibprobe. Nur ``bereit`` wird benutzt.

    Ohne lesbares Repo ist der Zustand ``unklar`` (gesperrt). Je Besitzer genau eine Logzeile,
    nie mit Token."""
    zugaenge = besitzer_aus_umgebung(umgebung)
    for besitzer, zugang in zugaenge.items():
        if zugang.token:
            probe = GithubLeser({besitzer: Zugang(besitzer, zugang.token, "bereit")}, get)
            liste = probe.repos(besitzer)
            repos = liste.get("repos") or [] if liste.get("ok") else []
            if repos and repos[0].get("name"):
                zugang.zustand = nur_lesend_pruefen(zugang.token, f"{besitzer}/{repos[0]['name']}", post)
            else:
                zugang.zustand = "unklar"
        log.info("Zugang %s: %s", besitzer, zugang.zustand)
    return zugaenge


class _Kontext:
    leser: GithubLeser = GithubLeser({}, http_get)
    pc_url: str = PC_SUCHE_URL_VORGABE
    pc_schluessel: str | None = None
    post_json: relay.HttpPostJson = relay.http_post_json


_K = _Kontext()


# --- Werkzeuge (Docstrings = Beschreibung fuer das Modell) ---------------------------------------

def github_repos(besitzer: str) -> dict:
    """Listet die Repos eines freigegebenen GitHub-Besitzers (z. B. Flissel, Vibemind-LAB), zuletzt
    geaenderte zuerst: Name, privat, letzter Push, offene Issues, Standard-Branch. Nur lesend."""
    return _K.leser.repos(besitzer)


def github_issues(repo: str, zustand: str = "open", max_anzahl: int = 20) -> dict:
    """Issues eines Repos (``besitzer/name``) ohne Pull Requests. zustand: open, closed oder all;
    max_anzahl hoechstens 50. Nur lesend."""
    return _K.leser.issues(repo, zustand, max_anzahl)


def github_prs(repo: str, zustand: str = "open", max_anzahl: int = 20) -> dict:
    """Pull Requests eines Repos (``besitzer/name``): Titel, Branches, Entwurf, Autor. zustand: open,
    closed oder all; max_anzahl hoechstens 50. Nur lesend."""
    return _K.leser.prs(repo, zustand, max_anzahl)


def github_commits(repo: str, branch: str = "", max_anzahl: int = 20) -> dict:
    """Letzte Commits eines Repos (``besitzer/name``), optional eines Branches: Kurz-SHA, erste
    Zeile der Nachricht, Autor, Datum. max_anzahl hoechstens 50. Nur lesend."""
    return _K.leser.commits(repo, branch, max_anzahl)


def github_datei(repo: str, pfad: str = "README.md", ref: str = "") -> dict:
    """Liest eine Datei (Text, hoechstens 100 000 Zeichen) oder listet einen Ordner aus einem Repo
    (``besitzer/name``). pfad relativ ohne ``..``; ref = Branch, Tag oder SHA. Nur lesend."""
    return _K.leser.datei(repo, pfad, ref)


def github_ueberblick(tage: int = 7) -> dict:
    """GitHub-Lagebild: je freigegebenem Besitzer die Repos mit Push in den letzten ``tage`` Tagen
    (mit offenen Issues), dazu gesperrte Zugaenge. Nur lesend."""
    return _K.leser.ueberblick(tage)


def pc_suchen(anfrage: str, art: str = "name", ordner: str = "", max_treffer: int = 20) -> dict:
    """Sucht auf dem PC des Nutzers (Desktop und Dokumente) nach Dateinamen (art="name") oder
    Textinhalten (art="inhalt"). ordner optional (muss darin liegen). Ist der PC aus, kommt die
    Meldung "PC ist aus". Bei abgeschnitten=true ist die Suche unvollstaendig. Nur lesend."""
    return relay.pc_suchen(anfrage, art, ordner, max_treffer, url=_K.pc_url, schluessel=_K.pc_schluessel,
                           post=_K.post_json)


WERKZEUGE: list[Callable[..., dict]] = [github_repos, github_issues, github_prs, github_commits, github_datei,
                                        github_ueberblick, pc_suchen]


def im_thread(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Synchrones Werkzeug als async-Huelle im Arbeiter-Thread (Muster spaces/marketing/claw/mcp_huelle):
    langsame GitHub- oder PC-Anfragen halten /health und andere Aufrufe nicht an."""
    import anyio

    @functools.wraps(fn)
    async def huelle(**kwargs: Any) -> Any:
        return await anyio.to_thread.run_sync(functools.partial(fn, **kwargs))

    return huelle


def bauen(umgebung: Mapping[str, str] = os.environ, *, get: HttpGet = http_get, post: HttpPost = http_post,
          post_json: relay.HttpPostJson = relay.http_post_json):
    """Server mit den sieben Werkzeugen; prueft vorher die GitHub-Zugaenge (Startprobe)."""
    from mcp.server.fastmcp import FastMCP
    from starlette.responses import JSONResponse

    _K.leser = GithubLeser(zugaenge_beim_start(umgebung, get, post), get)
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
