"""Ende-zu-Ende-Probe der Oberflaeche: echter Browser (Chromium), echte event-api,
Wegwerf-Postgres aus ``conftest.py``.

Laeuft nur mit ``EVENT_E2E=1`` und vorhandenem ``web/node_modules``.

- ``next build`` legt das API-Ziel der Rewrites (``EVENT_API_URL``) beim Bauen in
  ``.next/routes-manifest.json`` fest; ``next start`` liest es nicht neu. Darum gehoert zu
  jedem Bau ein API-Port. Ein Bau wird wiederverwendet, wenn sein Stempel
  (``.next/e2e-stempel.json``) juenger als alle Quellen ist, das Manifest auf denselben
  Port zeigt und dieser Port frei ist; sonst wird mit einem neuen freien Port gebaut.
- ``event-api`` laeuft je Test in einem Thread (uvicorn) auf diesem Port gegen die frische
  Test-DB; Einladungsmails landen im ``SpeicherMailer``.
- Prozesse schreiben in Logdateien unter basetemp (nie in eine Pipe) und werden nur ueber
  ihr Prozess-Handle beendet.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import threading
import time
from collections.abc import Callable, Iterator
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest

WEB = Path(__file__).resolve().parents[2] / "web"

if os.environ.get("EVENT_E2E") != "1":
    pytest.skip("E2E-Probe nur mit EVENT_E2E=1", allow_module_level=True)
if not (WEB / "node_modules").is_dir():
    pytest.skip("web/node_modules fehlt (npm ci in web/)", allow_module_level=True)

httpx = pytest.importorskip("httpx")
uvicorn = pytest.importorskip("uvicorn")
sync_api = pytest.importorskip("playwright.sync_api")

from spaces.event_management.system import api, zugang  # noqa: E402
from spaces.event_management.system.mailer import SpeicherMailer  # noqa: E402

expect = sync_api.expect
Page = sync_api.Page
BrowserContext = sync_api.BrowserContext

PW = "ein-langes-passwort-1"
GEHEIM = b"e2e-testgeheimnis-0123456789abcdef"  # >= 32 Byte
NEXT_BIN = WEB / "node_modules" / "next" / "dist" / "bin" / "next"
STEMPEL = WEB / ".next" / "e2e-stempel.json"
QUELLEN = ("app", "components", "lib", "next.config.ts", "package.json", "package-lock.json",
           "postcss.config.mjs", "tsconfig.json")
UUID_PFAD = re.compile(r"/events/[0-9a-f-]{36}$")


# --- Hilfen ----------------------------------------------------------------------------


def _freier_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _port_frei(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def _log_ende(log: Path) -> str:
    try:
        return log.read_text(encoding="utf-8", errors="replace")[-3000:]
    except OSError:
        return "(kein Log)"


def _juengste_quelle() -> float:
    zeiten: list[float] = []
    for name in QUELLEN:
        pfad = WEB / name
        if pfad.is_dir():
            zeiten += [f.stat().st_mtime for f in pfad.rglob("*") if f.is_file()]
        elif pfad.is_file():
            zeiten.append(pfad.stat().st_mtime)
    return max(zeiten)


def _api_url(port: int) -> str:
    return f"http://127.0.0.1:{port}"


def _wiederverwendbarer_port() -> int | None:
    """Port des letzten E2E-Baus, wenn der Bau noch passt und der Port frei ist."""
    try:
        stempel = json.loads(STEMPEL.read_text(encoding="utf-8"))
        manifest = json.loads((WEB / ".next" / "routes-manifest.json").read_text(encoding="utf-8"))
        port = int(stempel["api_port"])
        ziele = [r["destination"] for r in manifest["rewrites"]["afterFiles"]]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if not (WEB / ".next" / "BUILD_ID").is_file():
        return None
    if STEMPEL.stat().st_mtime < _juengste_quelle():
        return None
    if ziele != [f"{_api_url(port)}/api/:pfad*"]:
        return None
    return port if _port_frei(port) else None


def _node() -> str:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node fehlt")
    return node


def _umgebung(api_port: int) -> dict[str, str]:
    return {**os.environ, "EVENT_API_URL": _api_url(api_port), "NEXT_TELEMETRY_DISABLED": "1"}


def _bauen(node: str, api_port: int, log: Path) -> None:
    with open(log, "wb") as aus:
        ergebnis = subprocess.run(
            [node, str(NEXT_BIN), "build"], cwd=WEB, env=_umgebung(api_port),
            stdin=subprocess.DEVNULL, stdout=aus, stderr=subprocess.STDOUT,
            timeout=900, check=False,
        )
    if ergebnis.returncode != 0:
        raise RuntimeError(f"next build fehlgeschlagen:\n{_log_ende(log)}")
    STEMPEL.write_text(json.dumps({"api_port": api_port}), encoding="utf-8")


def _warte_auf_http(url: str, prozess: subprocess.Popen[bytes], log: Path) -> None:
    ende = time.monotonic() + 90
    while time.monotonic() < ende:
        if prozess.poll() is not None:
            raise RuntimeError(f"next start beendet ({prozess.returncode}):\n{_log_ende(log)}")
        try:
            if httpx.get(url, timeout=2).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.3)
    raise RuntimeError(f"next start antwortet nicht:\n{_log_ende(log)}")


# --- Fixtures --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def web(tmp_path_factory: pytest.TempPathFactory) -> Iterator[dict[str, Any]]:
    """Gebaute Oberflaeche unter ``next start``: ``{"url": ..., "api_port": ...}``."""
    node = _node()
    logs = tmp_path_factory.mktemp("web")
    api_port = _wiederverwendbarer_port()
    if api_port is None:
        api_port = _freier_port()
        _bauen(node, api_port, logs / "build.log")

    web_port = _freier_port()
    url = f"http://127.0.0.1:{web_port}"
    log = logs / "start.log"
    with open(log, "wb") as aus:
        prozess = subprocess.Popen(
            [node, str(NEXT_BIN), "start", "-p", str(web_port), "-H", "127.0.0.1"],
            cwd=WEB, env=_umgebung(api_port), stdin=subprocess.DEVNULL, stdout=aus,
            stderr=subprocess.STDOUT,
        )
    try:
        _warte_auf_http(f"{url}/anmelden", prozess, log)
        yield {"url": url, "api_port": api_port}
    finally:
        prozess.terminate()
        try:
            prozess.wait(timeout=15)
        except subprocess.TimeoutExpired:
            prozess.kill()
            prozess.wait(timeout=15)


@pytest.fixture(autouse=True)
def _sperre_leeren() -> Iterator[None]:
    zugang._sperre_zuruecksetzen()
    yield
    zugang._sperre_zuruecksetzen()


@pytest.fixture
def event_api(app_dsn: str, web: dict[str, Any]) -> Iterator[SpeicherMailer]:
    """event-api auf dem Port des Baus, gegen die DB dieses Tests; liefert den Mailer."""
    mailer = SpeicherMailer()
    app = api.erstelle_app(app_dsn, GEHEIM, mailer, web["url"], cookie_sicher=False)
    server = uvicorn.Server(uvicorn.Config(
        app, host="127.0.0.1", port=web["api_port"], log_config=None, log_level="warning",
        access_log=False,
    ))
    faden = threading.Thread(target=server.run, name="event-api-e2e", daemon=True)
    faden.start()
    ende = time.monotonic() + 30
    while not server.started:
        if not faden.is_alive() or time.monotonic() > ende:
            server.should_exit = True
            raise RuntimeError(f"event-api startet nicht auf Port {web['api_port']}")
        time.sleep(0.05)
    try:
        yield mailer
    finally:
        server.should_exit = True
        faden.join(timeout=30)


@pytest.fixture(scope="module")
def browser() -> Iterator[Any]:
    with sync_api.sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        try:
            yield b
        finally:
            b.close()


@pytest.fixture
def kontext(browser: Any) -> Iterator[Callable[[], Page]]:
    """``kontext()``: neue Seite in einem eigenen Browser-Kontext (eigene Cookies)."""
    offen: list[BrowserContext] = []

    def neu() -> Page:
        k = browser.new_context(locale="de-DE", timezone_id="Europe/Berlin")
        k.set_default_timeout(15_000)
        offen.append(k)
        return k.new_page()

    try:
        yield neu
    finally:
        for k in offen:
            k.close()


# --- Ablauf-Bausteine ------------------------------------------------------------------


def _person(admin: Any, mail: str, name: str, *, betreiber: bool = False) -> UUID:
    return admin.execute(
        "insert into event.person (mail, name, passwort_hash, betreiber)"
        " values (%s, %s, %s, %s) returning id",
        (mail, name, zugang.passwort_hash(PW), betreiber),
    ).fetchone()["id"]


def _anmelden(seite: Page, basis: str, mail: str) -> None:
    seite.goto(f"{basis}/anmelden")
    seite.get_by_label("Mail", exact=True).fill(mail)
    seite.get_by_label("Passwort", exact=True).fill(PW)
    seite.get_by_role("button", name="Anmelden").click()
    expect(seite.get_by_role("heading", name="Katalog")).to_be_visible()


def _abschnitt(seite: Page, titel: str) -> Any:
    """Abschnitt eines Baustein-Reiters (innere ``section`` mit dieser Ueberschrift)."""
    return seite.locator("section section").filter(
        has=seite.get_by_role("heading", name=titel, exact=True)
    )


def _beginn_in_30_tagen() -> str:
    tag = datetime.now(timezone.utc) + timedelta(days=30)
    return tag.strftime("%Y-%m-%dT10:00")


# --- Tests -----------------------------------------------------------------------------


def test_orga_ablauf_im_browser(
    db_admin: Any, event_api: SpeicherMailer, web: dict[str, Any],
    kontext: Callable[[], Page],
) -> None:
    basis = web["url"]
    # 1. Betreiber per Admin-SQL.
    _person(db_admin, "betreiber@example.org", "Betreiber Beispiel", betreiber=True)

    # 2. Anmelden, Firma anlegen.
    b = kontext()
    _anmelden(b, basis, "betreiber@example.org")
    b.get_by_role("link", name="Firmen").click()
    b.get_by_role("button", name="+ Firma anlegen").click()
    b.get_by_label("Name", exact=True).fill("Beispiel GmbH")
    b.get_by_label("Kürzel", exact=True).fill("beispiel")
    b.get_by_role("button", name="Anlegen", exact=True).click()
    b.get_by_role("link", name=re.compile(r"Beispiel GmbH")).click()
    expect(b.get_by_role("heading", name="Beispiel GmbH")).to_be_visible()

    # 3. Leitung einladen, Token aus dem Mailer, Einladung im Browser einloesen.
    b.get_by_label("Mail", exact=True).fill("leitung@example.org")
    b.get_by_label("Name", exact=True).fill("Lea Leitung")
    b.get_by_label("Rolle", exact=True).select_option("leitung")
    b.get_by_role("button", name="Einladen", exact=True).click()
    expect(b.get_by_text("Einladung verschickt")).to_be_visible()
    assert len(event_api.gesendet) == 1
    mail = event_api.gesendet[0]
    assert mail.an == "leitung@example.org"
    treffer = re.search(r"(https?://\S+/einladung/\S+)", mail.text)
    assert treffer, "Einladungslink fehlt in der Mail"
    link = treffer.group(1)
    assert link.startswith(f"{basis}/einladung/")

    lt = kontext()
    lt.goto(link)
    expect(lt.get_by_role("heading", name="Einladung zu Beispiel GmbH")).to_be_visible()
    lt.get_by_label("Passwort", exact=True).fill(PW)
    lt.get_by_label("Passwort wiederholen", exact=True).fill(PW)
    lt.get_by_role("button", name="Einladung annehmen").click()
    expect(lt.get_by_text("Konto bereit, bitte anmelden")).to_be_visible()

    # 4. Als Leitung anmelden.
    lt.get_by_label("Mail", exact=True).fill("leitung@example.org")
    lt.get_by_label("Passwort", exact=True).fill(PW)
    lt.get_by_role("button", name="Anmelden").click()
    expect(lt.get_by_role("heading", name="Katalog")).to_be_visible()

    # 5. Hackathon anlegen (3 Schritte).
    lt.get_by_role("link", name="+ Neues Event").click()
    lt.get_by_role("button", name=re.compile(r"^Hackathon")).click()
    lt.get_by_label("Name *", exact=True).fill("Beispiel-Hackathon")
    lt.get_by_label("Beginn *", exact=True).fill(_beginn_in_30_tagen())
    lt.get_by_role("button", name="Weiter").click()
    lt.get_by_role("button", name="Event anlegen").click()
    lt.wait_for_url(UUID_PFAD)
    event_url = lt.url
    expect(lt.get_by_role("heading", name="Beispiel-Hackathon")).to_be_visible()

    # 6. Reiter "Aufgaben": Kategorie und Aufgabe anlegen.
    lt.get_by_role("navigation", name="Bereiche").get_by_role("link", name="Aufgaben").click()
    kategorien = _abschnitt(lt, "Kategorien")
    kategorien.get_by_role("button", name="+ Hinzufügen").click()
    kategorien.get_by_label("Name *", exact=True).fill("Technik")
    kategorien.get_by_role("button", name="Speichern").click()
    expect(kategorien.get_by_role("cell", name="Technik", exact=True)).to_be_visible()

    aufgaben = _abschnitt(lt, "Aufgaben")
    aufgaben.get_by_role("button", name="+ Hinzufügen").click()
    aufgaben.get_by_label("Titel *", exact=True).fill("Beamer organisieren")
    aufgaben.get_by_label("Kategorie", exact=True).select_option(label="Technik")
    aufgaben.get_by_role("button", name="Speichern").click()
    zeile = aufgaben.get_by_role("row").filter(has_text="Beamer organisieren")
    expect(zeile).to_be_visible()
    expect(zeile.get_by_role("cell", name="Technik", exact=True)).to_be_visible()

    # 7. Katalog: Kachel unter "Kommend" mit "Aufgaben 0/1".
    lt.get_by_role("navigation", name="Hauptnavigation").get_by_role(
        "link", name="Katalog").click()
    kommend = lt.locator("section").filter(
        has=lt.get_by_role("heading", name=re.compile(r"^Kommend")))
    kachel = kommend.get_by_role("link").filter(has_text="Beispiel-Hackathon")
    expect(kachel).to_be_visible()
    expect(kachel).to_contain_text("Aufgaben 0/1")
    expect(kachel).to_contain_text("Beispiel GmbH")

    # 8. Verlauf zeigt die Eintraege (Event, Kategorie, Aufgabe), Actor = Leitung.
    lt.goto(f"{event_url}?reiter=verlauf")
    zeilen = lt.locator("tbody tr")
    expect(zeilen).to_have_count(3)
    texte = [" | ".join(z) for z in (
        [c.strip() for c in r.locator("td").all_inner_texts()] for r in zeilen.all())]
    assert all("Lea Leitung" in t and "angelegt" in t for t in texte), texte
    assert any("| Event |" in t for t in texte), texte
    assert any("| Aufgaben |" in t for t in texte), texte
    assert any("| Aufgaben-Kategorien |" in t for t in texte), texte


def test_firma_b_sieht_nichts_im_browser(
    db_admin: Any, event_api: SpeicherMailer, web: dict[str, Any],
    kontext: Callable[[], Page],
) -> None:
    basis = web["url"]
    firmen: dict[str, UUID] = {}
    for kurz in ("bsp-a", "bsp-b"):
        firmen[kurz] = db_admin.execute(
            "insert into event.firma (name, kurz) values (%s, %s) returning id",
            ("Beispiel " + kurz, kurz),
        ).fetchone()["id"]
        person = _person(db_admin, f"leitung-{kurz}@example.org", f"Leitung {kurz}")
        db_admin.execute("insert into event.mitgliedschaft values (%s, %s, 'leitung')",
                         (person, firmen[kurz]))
    beginn = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(days=30)
    ea = db_admin.execute(
        "insert into event.event (firma_id, kategorie, name, beginn, ende)"
        " values (%s, 'hackathon', 'Geheimer Hackathon', %s, %s) returning id",
        (firmen["bsp-a"], beginn, beginn + timedelta(hours=3)),
    ).fetchone()["id"]

    # Gegenprobe: Firma A sieht ihr Event (sonst waere das Nicht-Sehen von B wertlos).
    a = kontext()
    _anmelden(a, basis, "leitung-bsp-a@example.org")
    expect(a.get_by_role("link").filter(has_text="Geheimer Hackathon")).to_be_visible()

    b = kontext()
    _anmelden(b, basis, "leitung-bsp-b@example.org")
    expect(b.get_by_text("Keine Events gefunden")).to_be_visible()
    expect(b.get_by_text("Geheimer Hackathon")).to_have_count(0)

    b.goto(f"{basis}/events/{ea}")
    expect(b.locator("main").get_by_role("alert")).to_have_text("Nicht gefunden")
    expect(b.get_by_text("Geheimer Hackathon")).to_have_count(0)
