"""Umzug des Rowboat-Arbeitsordners `~/.rowboat` vom PC nach `offload-vm:/home/debian/rowboat-data/`.

Aufruf als Datei (nicht per `-m`, `spaces/rowboat/__init__.py` ist kaputt):
`python spaces/rowboat/scripts/rowboat_umzug.py [--trocken]`.

Ablauf (einmalig, Task 7 der Rowboat-Zentrale Teil A):
1. Sperre des Wissens-Spiegels nehmen (`lauf.lock`), damit kein Spiegellauf dazwischenfunkt.
2. `auswahl`: nur `MITNEHMEN`, nie `NIE`, keine `*.bak` in `config/`, in `knowledge/` nie `_konflikte/` und
   `.spiegel-eingang/`, keine System-/Temp-Dateien (wie der Spiegel).
3. `config/` wird in einen temporaeren Ordner kopiert und dort angepasst (`config_anpassen`); die Live-Config
   am PC bleibt unberuehrt. Zeigt ein lokaler Anbieter in `models.json` nicht auf :8121, bricht der Umzug ab.
4. Vorbedingungen: kein laufendes PC-Rowboat (`server.lock`/`embed.json` mit lebender PID), VM-Ordner
   existiert, enthaelt weder `knowledge/` noch `todo.md` noch eine der Zieldateien, genug Platz. Kein
   Ueberschreiben, keine Ausnahme-Option: einen belegten Zielordner raeumt der Betreiber von Hand.
5. Upload ueber den gestuften Mechanismus des Spiegels (`wissens_spiegel.hochladen`, Eingang
   `.umzug-eingang/`, Einsetzen per `os.replace` nur auf freie Ziele); `config/` mit Modus 0600.
6. Voller sha256-Vergleich PC <-> VM fuer jede uebertragene Datei (VM-Seite in einem `ssh ... python3 -`).
7. Nur wenn alles gleich ist: Zustand des Spiegels (`zuletzt.json`) = uebertragener `knowledge`-Bestand.

`--trocken` liest nur (ein ssh-Aufruf, der nichts anlegt), zeigt Anzahl/Groessen und die Vorbedingungen.
Am PC wird nie etwas geloescht. Geheimnisse stehen nie in argv oder im Log (nur Pfade und Zahlen).

Exit: 0 ok, 1 lokaler Fehler/PC-Ordner fehlt, 2 VM nicht erreichbar/Uebertragung abgebrochen,
3 verweigert (Vorbedingung, Spiegel laeuft, `models.json`), 4 Pruefsummen-Abweichung oder Ziel waehrend des
Uploads belegt (Zustand nicht geschrieben).
"""
from __future__ import annotations

import argparse
import hashlib
import inspect
import json
import logging
import logging.handlers
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))

import wissens_spiegel as ws  # noqa: E402

MITNEHMEN = ("knowledge", "todo", "todo.md", "storage", "bg-tasks", "agents", "skills", "projects", "config")
NIE = (
    "server-key", "cipher-key", "server.lock", "embed.json", ".electron-data/", "engines/",
    "config/client.json", "config/oauth.json", "config/oauth-clients.json", "config/gmail-credentials.json",
    "config/gdrive-credentials.json", "config/composio.json", "config/slack.json",
    "gmail_sync/", "calendar_sync/", "Videos/",
)
BELEGT_WENN = ("knowledge", "todo.md")  # gibt es eins davon auf der VM, ist der Arbeitsordner schon live
EINGANG = ".umzug-eingang"
SHIM_PORT = 8121
SERVER = {"lanEnabled": True, "port": 3220}
TIMEOUT_PRUEFSUMMEN = 120

_BAK = re.compile(r"\.bak($|[-._])")

log = logging.getLogger("rowboat_umzug")


class ConfigFehler(Exception):
    """Die Config laesst sich nicht sicher fuer die VM anpassen (Meldung ohne Geheimnisse)."""


# --- Auswahl -------------------------------------------------------------------------------------------


def _nie(rel: str) -> bool:
    teile = rel.split("/")
    if rel in NIE or any(n.endswith("/") and rel.startswith(n) for n in NIE):
        return True
    if teile[0] == "config" and _BAK.search(teile[-1]):
        return True
    if teile[0] == "knowledge" and len(teile) >= 2 and teile[1] in ws.NIE_SPIEGELN:
        return True
    name = teile[-1]
    return name in ws.IGNORIERTE_NAMEN or name.endswith(ws.IGNORIERTE_ENDUNGEN)


def auswahl(wurzel: Path) -> list[Path]:
    """Alle Dateien unter `wurzel`, die mit umziehen (sortiert, absolute Pfade, keine Links)."""
    wurzel = Path(wurzel)
    gewaehlt: list[Path] = []
    for eintrag in MITNEHMEN:
        start = wurzel / eintrag
        if start.is_file() and not start.is_symlink():
            if not _nie(eintrag):
                gewaehlt.append(start)
            continue
        if not start.is_dir() or start.is_symlink():
            continue
        for ordner, unter, namen in os.walk(start):
            unter.sort()
            for name in sorted(namen):
                pfad = Path(ordner) / name
                rel = pfad.relative_to(wurzel).as_posix()
                if pfad.is_symlink() or not pfad.is_file() or _nie(rel):
                    continue
                gewaehlt.append(pfad)
    return sorted(gewaehlt, key=lambda p: p.relative_to(wurzel).as_posix())


# --- Config fuer die VM --------------------------------------------------------------------------------


def _json_lesen(pfad: Path, vorgabe: Any) -> Any:
    if not pfad.exists():
        return vorgabe
    try:
        return json.loads(pfad.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise ConfigFehler(f"{pfad.name}: kein gueltiges JSON") from exc


def _json_schreiben(pfad: Path, daten: Any) -> None:
    tmp = pfad.with_name(pfad.name + ".umzug.tmp")
    tmp.write_text(json.dumps(daten, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, pfad)


def _modelle_pruefen(config_ordner: Path) -> None:
    modelle = _json_lesen(config_ordner / "models.json", None)
    if modelle is None:
        log.warning("config/models.json fehlt - Rowboat auf der VM hat dann kein Modell")
        return
    anbieter = modelle.get("providers") if isinstance(modelle, dict) else None
    for name, eintrag in (anbieter or {}).items():
        url = eintrag.get("baseURL") if isinstance(eintrag, dict) else None
        if not isinstance(url, str) or not url:
            continue
        teile = urlsplit(url)
        if (teile.hostname or "").lower() not in ("127.0.0.1", "localhost"):
            continue
        try:
            port = teile.port
        except ValueError:
            port = None
        if port != SHIM_PORT:
            raise ConfigFehler(
                f"models.json: Anbieter '{name}' zeigt auf {teile.hostname}:{port or 'Standardport'}, auf der VM "
                f"lauscht nur die Shim auf :{SHIM_PORT}. Bitte models.json am PC pruefen (wird nicht umgeschrieben)."
            )


def config_anpassen(config_ordner: Path) -> None:
    """Passt eine (temporaere!) Kopie von `config/` fuer die VM an.

    `channels.json`: Telegram aus und ohne Bot-Schluessel (der neue Bot kommt in Task 7), WhatsApp aus.
    `knowledge_sources.json`: Quelle `gmail` aus (Eintrag notfalls anlegen), andere bleiben; Windows-Pfade in
    `artifactDir` werden zu `/`. `server.json`: LAN an, Port 3220. `models.json`: bleibt, aber jeder lokale
    Anbieter muss auf :8121 zeigen, sonst `ConfigFehler`.
    """
    config_ordner = Path(config_ordner)
    _modelle_pruefen(config_ordner)

    kanaele = _json_lesen(config_ordner / "channels.json", {})
    if not isinstance(kanaele, dict):
        raise ConfigFehler("channels.json: kein Objekt")
    telegram = kanaele.get("telegram") if isinstance(kanaele.get("telegram"), dict) else {}
    kanaele["telegram"] = {**{"allowFrom": []}, **telegram, "enabled": False, "botToken": ""}
    whatsapp = kanaele.get("whatsapp") if isinstance(kanaele.get("whatsapp"), dict) else {}
    kanaele["whatsapp"] = {**{"allowFrom": []}, **whatsapp, "enabled": False}
    _json_schreiben(config_ordner / "channels.json", kanaele)

    quellen = _json_lesen(config_ordner / "knowledge_sources.json", {"sources": []})
    if not isinstance(quellen, dict) or not isinstance(quellen.get("sources", []), list):
        raise ConfigFehler("knowledge_sources.json: unerwarteter Aufbau")
    liste = quellen.setdefault("sources", [])
    for q in liste:
        if isinstance(q, dict) and isinstance(q.get("artifactDir"), str):
            q["artifactDir"] = q["artifactDir"].replace("\\", "/")
    gmail = [q for q in liste if isinstance(q, dict) and q.get("id") == "gmail"]
    for q in gmail:
        q["enabled"] = False
    if not gmail:
        liste.append({"id": "gmail", "provider": "gmail", "enabled": False, "artifactDir": "gmail_sync",
                      "syncMode": "file", "scopes": []})
    _json_schreiben(config_ordner / "knowledge_sources.json", quellen)

    _json_schreiben(config_ordner / "server.json", dict(SERVER))


# --- Vorbedingungen ------------------------------------------------------------------------------------


def rowboat_laeuft(wurzel: Path, pid_lebt: Callable[[int], bool] = ws._pid_lebt) -> list[str]:
    """Gruende, warum ein PC-Rowboat den Arbeitsordner noch haelt (leer = frei). Nur PIDs, keine Tokens."""
    gruende: list[str] = []
    try:
        text = (Path(wurzel) / "server.lock").read_text(encoding="utf-8", errors="replace").strip()
        if text.isdigit() and pid_lebt(int(text)):
            gruende.append(f"server.lock: Prozess {text} laeuft")
    except OSError:
        pass
    try:
        pid = json.loads((Path(wurzel) / "embed.json").read_text(encoding="utf-8")).get("pid")
        if isinstance(pid, int) and pid_lebt(pid):
            gruende.append(f"embed.json: Prozess {pid} laeuft (Einbettung, Stop-RowboatEmbed)")
    except (OSError, ValueError, AttributeError):
        pass
    return gruende


_VM_ZIEL_PRUEFEN = """
import os, shutil
w = P['wurzel']
da = os.path.isdir(w)
frei = None
try:
    frei = shutil.disk_usage(w if da else (os.path.dirname(w.rstrip('/')) or '/')).free
except OSError:
    pass
json.dump({
    'da': da,
    'belegt': [n for n in P['belegt_wenn'] if os.path.lexists(os.path.join(w, n))] if da else [],
    'vorhanden': [n for n in P['ziele'] if os.path.lexists(os.path.join(w, n))] if da else [],
    'frei': frei,
}, sys.stdout)
"""

_VM_PRUEFSUMMEN = inspect.getsource(ws._datei_sha) + """
import os
json.dump({n: _datei_sha(os.path.join(P['wurzel'], n)) for n in P['namen']}, sys.stdout)
"""


def _sha(pfad: Path) -> str:
    h = hashlib.sha256()
    with open(pfad, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _groesse(n: float) -> str:
    for einheit in ("B", "KB", "MB", "GB"):
        if n < 1024 or einheit == "GB":
            return f"{n:.0f} {einheit}" if einheit == "B" else f"{n:.1f} {einheit}"
        n /= 1024
    return f"{n:.1f} GB"


def _config_modus(info: tarfile.TarInfo) -> tarfile.TarInfo:
    """`config/` enthaelt Schluessel (models.json): auf der VM nur fuer debian lesbar."""
    if info.name.startswith("config/"):
        info.mode = 0o600
    info.uid = info.gid = 0
    info.uname = info.gname = ""
    return info


# --- Lauf ----------------------------------------------------------------------------------------------


def umzug(
    pc_wurzel: Path,
    ssh_ziel: str,
    vm_wurzel: str,
    zustand: Path,
    trocken: bool = False,
    laeufer: ws.Laeufer = subprocess.run,
    pid_lebt: Callable[[int], bool] = ws._pid_lebt,
) -> int:
    """Ein Umzug (oder Trockenlauf). Exit-Codes siehe Moduldoku."""
    pc_wurzel = Path(pc_wurzel)
    if not pc_wurzel.is_dir():
        log.error("PC-Arbeitsordner fehlt: %s", pc_wurzel)
        return 1
    sperre = Path(zustand).parent / ws.SPERRDATEI
    if not ws._sperre_nehmen(sperre):
        log.error("Wissens-Spiegel laeuft gerade (Sperre %s) - Umzug verweigert, spaeter erneut", sperre)
        return 3
    try:
        with tempfile.TemporaryDirectory(prefix="rowboat-umzug-") as tmp:
            return _umzug_gesperrt(pc_wurzel, ssh_ziel, vm_wurzel, Path(zustand), trocken, laeufer, pid_lebt,
                                   Path(tmp))
    finally:
        ws._sperre_freigeben(sperre)


def _umzug_gesperrt(
    pc_wurzel: Path, ssh_ziel: str, vm_wurzel: str, zustand: Path, trocken: bool, laeufer: ws.Laeufer,
    pid_lebt: Callable[[int], bool], tmp: Path,
) -> int:
    vorsilbe = "[trocken] " if trocken else ""
    gewaehlt = auswahl(pc_wurzel)

    # (lokale Quelle, Name auf der VM); config/ kommt aus der angepassten temporaeren Kopie
    quellen: list[tuple[Path, str]] = []
    try:
        for pfad in gewaehlt:
            name = pfad.relative_to(pc_wurzel).as_posix()
            if name.startswith("config/"):
                kopie = tmp / name
                kopie.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(pfad, kopie)
                quellen.append((kopie, name))
            else:
                quellen.append((pfad, name))
        config_anpassen(tmp / "config")
    except ConfigFehler as exc:
        log.error("Umzug verweigert: %s", exc)
        return 3
    except OSError as exc:
        log.error("Lokaler Fehler beim Vorbereiten: %s: %s", type(exc).__name__, exc)
        return 1

    # config_anpassen kann fehlende Dateien anlegen (channels.json, knowledge_sources.json, server.json)
    vorhanden = {n for _, n in quellen}
    for neu in sorted((tmp / "config").glob("*.json")):
        name = f"config/{neu.name}"
        if name not in vorhanden:
            quellen.append((neu, name))

    groessen: dict[str, list[int]] = {}
    gesamt = 0
    for quelle, name in quellen:
        g = quelle.stat().st_size
        gesamt += g
        zaehler = groessen.setdefault(name.split("/")[0], [0, 0])
        zaehler[0] += 1
        zaehler[1] += g
    for eintrag in MITNEHMEN:
        anzahl, g = groessen.get(eintrag, [0, 0])
        log.info("%s%-10s %5d Dateien  %s", vorsilbe, eintrag, anzahl, _groesse(g))
    log.info("%sgesamt     %5d Dateien  %s", vorsilbe, len(quellen), _groesse(gesamt))

    gruende = rowboat_laeuft(pc_wurzel, pid_lebt=pid_lebt)
    try:
        ziel = ws._vm_python(ssh_ziel, _VM_ZIEL_PRUEFEN,
                             {"wurzel": vm_wurzel, "belegt_wenn": list(BELEGT_WENN), "ziele": [n for _, n in quellen]},
                             laeufer, ws.TIMEOUT_BESTAND)
    except ws.VmFehler as exc:
        log.error("VM nicht erreichbar, nichts geaendert: %s", exc)
        return 2
    if not ziel.get("da"):
        gruende.append(f"VM-Ordner {vm_wurzel} fehlt (anlegen mit Besitzer debian, Modus 0700, z. B. bauen.ps1)")
    if ziel.get("belegt"):
        gruende.append(f"VM-Ordner {vm_wurzel} ist schon belegt ({', '.join(ziel['belegt'])}) - von Hand leeren")
    andere = [n for n in ziel.get("vorhanden", []) if n.split("/")[0] not in ziel.get("belegt", [])]
    if andere:
        gruende.append(f"{len(andere)} Zieldatei(en) gibt es auf der VM schon, z. B. {', '.join(andere[:5])}")
    frei = ziel.get("frei")
    log.info("%sVM %s: %s frei", vorsilbe, vm_wurzel, "?" if frei is None else _groesse(frei))
    if isinstance(frei, int) and frei < 2 * gesamt:
        gruende.append(f"zu wenig Platz auf der VM ({_groesse(frei)} frei, {_groesse(gesamt)} noetig)")

    for grund in gruende:
        log.error("%sVorbedingung nicht erfuellt: %s", vorsilbe, grund)
    if trocken:
        log.info("Trockenlauf: %d Dateien (%s) wuerden uebertragen, %s; nichts geschrieben",
                 len(quellen), _groesse(gesamt), "Vorbedingungen erfuellt" if not gruende else
                 f"{len(gruende)} Vorbedingung(en) offen")
        return 3 if gruende else 0
    if gruende:
        log.error("Umzug verweigert, nichts uebertragen")
        return 3

    soll = {name: _sha(quelle) for quelle, name in quellen}
    try:
        belegt = ws.hochladen([(q, n, None) for q, n in quellen], ssh_ziel, vm_wurzel, laeufer,
                              eingang_ordner=EINGANG, tar_filter=_config_modus)
    except ws.VmFehler as exc:
        log.error("Uebertragung abgebrochen (eingesetzte Dateien bleiben, Zustand nicht geschrieben): %s", exc)
        return 2
    except OSError as exc:
        log.error("Uebertragung abgebrochen (lokal): %s: %s", type(exc).__name__, exc)
        return 1
    if belegt:
        log.error("%d Ziel(e) waehrend des Uploads belegt, nicht ueberschrieben: %s", len(belegt), ", ".join(belegt))
        return 4

    try:
        ist = ws._vm_python(ssh_ziel, _VM_PRUEFSUMMEN, {"wurzel": vm_wurzel, "namen": sorted(soll)},
                            laeufer, TIMEOUT_PRUEFSUMMEN)
    except ws.VmFehler as exc:
        log.error("Pruefsummen nicht lesbar, Zustand nicht geschrieben: %s", exc)
        return 2
    abweichend = sorted(n for n, s in soll.items() if not isinstance(ist, dict) or ist.get(n) != s)
    log.info("Pruefsummen: %d Dateien verglichen, Abweichungen %d", len(soll), len(abweichend))
    if abweichend:
        for name in abweichend:
            log.error("Abweichung PC<->VM: %s", name)
        log.error("Zustand des Wissens-Spiegels nicht geschrieben")
        return 4

    wissen = [[name[len("knowledge/"):], soll[name], quelle.stat().st_mtime]
              for quelle, name in quellen if name.startswith("knowledge/")]
    bestand, _ = ws._bestand_aus(wissen, "pc", [])
    ws.zustand_schreiben(zustand, bestand)
    log.info("Umzug fertig: %d Dateien, %d im Spiegel-Zustand %s", len(quellen), len(bestand), zustand)
    return 0


def _log_einrichten(root: Path) -> None:
    ordner = root / "logs" / "rowboat"
    ordner.mkdir(parents=True, exist_ok=True)
    datei = logging.handlers.RotatingFileHandler(
        ordner / "rowboat_umzug.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8"
    )
    datei.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    for logger in (log, ws.log):
        logger.addHandler(datei)
        if sys.stderr is not None:
            logger.addHandler(logging.StreamHandler())
        logger.setLevel(logging.INFO)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Rowboat-Arbeitsordner einmalig auf den Mini-PC umziehen.")
    ap.add_argument("--trocken", action="store_true", help="nur lesen: Anzahl, Groessen, Vorbedingungen")
    ap.add_argument("--pc-wurzel", type=Path, default=Path.home() / ".rowboat")
    ap.add_argument("--ssh-ziel", default="offload-vm")
    ap.add_argument("--vm-wurzel", default="/home/debian/rowboat-data")
    ap.add_argument("--zustand", type=Path, default=None)
    args = ap.parse_args(argv)
    root = Path(os.environ.get("VIBEMIND_ROOT") or "C:/Users/User/Desktop/Vibemind_V1")
    zustand = args.zustand or ws.zustand_standard()
    _log_einrichten(root)
    try:
        return umzug(args.pc_wurzel, args.ssh_ziel, args.vm_wurzel, zustand, trocken=args.trocken)
    except Exception:  # noqa: BLE001 - lesbarer Grund im Log
        log.exception("Umzug abgebrochen")
        return 1


if __name__ == "__main__":
    sys.exit(main())
