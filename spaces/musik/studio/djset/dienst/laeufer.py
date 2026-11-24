"""Auftrags-Laeufer des Musik-Dienstes.

Arbeitet die Auftragsablage nacheinander ab: `video`, `set` und `aufnehmen` laufen als
Unterprozess von `dj-set`, `laura_render` ruft eine injizierte Funktion. stdout und
stderr des Unterprozesses gehen in Dateien im Auftragsordner, nie in eine Pipe: sonst
erben Kindprozesse die Leitung und `wait` haengt.
"""
from __future__ import annotations

import importlib.util
import logging
import os
import re
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from djset.dienst.auftraege import Ablage, Auftrag, musik_offen

LOG = logging.getLogger("djset.dienst.laeufer")

FEHLER_MAXLAENGE = 300
MIN_FREI_GB = 8.0
SPEICHER_PAUSE_S = 60.0   # nach "zu wenig Speicher" so lange nicht neu messen (jede Messung startet PowerShell)
SPEICHER_GRUND = "zu wenig Arbeitsspeicher frei"   # der Video-Claw erkennt daran das Warten auf Speicher
# Wie lange ein Auftrag hoechstens wartet, gemessen ab `erstellt`; danach `gescheitert`.
WARTEGRENZEN_S: dict[str, float] = {
    "video": 30 * 60.0,
    "set": 3 * 3600.0,
    "aufnehmen": 3 * 3600.0,
    "laura_render": 2 * 3600.0,
}
# Was dj-set nicht braucht und darum nicht erbt (geprueft per grep: dj-set liest nur MUSIK_DATEN).
_KIND_OHNE = ("LAURA_TOKEN", "MUSIK_LINK_SCHLUESSEL", "MUSIK_TELEGRAM_TOKEN")
_KIND_OHNE_PRAEFIX = ("TELEGRAM_BOT_TOKEN",)
BUDGET_AGENT = "musik-kette"
ZEITLIMITS_S: dict[str, float] = {
    "video": 600.0,
    "set": 2700.0,
    "aufnehmen": 1800.0,
    "laura_render": 900.0,
}
_RUN_ZEILE = re.compile(r"^Run:\s*(\S+)\s*$", re.MULTILINE)
_ENDE_ZUSTAND = {0: "fertig", 2: "fertig_mit_befund"}
_BUCHEN_ARTEN = {"set", "video"}
_PAUSE_S = 0.05


def _jetzt() -> datetime:
    return datetime.now(UTC)


def _kuerzen(text: str) -> str:
    return text[:FEHLER_MAXLAENGE]


def kind_umgebung(daten: Path) -> dict[str, str]:
    """Umgebung fuer dj-set: alles ausser den Schluesseln fuer Laura, Datei-Link und Telegram."""
    umgebung = {
        k: v for k, v in os.environ.items()
        if k.upper() not in _KIND_OHNE and not k.upper().startswith(_KIND_OHNE_PRAEFIX)
    }
    umgebung.setdefault("MUSIK_DATEN", str(daten))
    return umgebung


# ---------------------------------------------------------------- Prozess-Helfer


def _prozess_lebt(pid: int) -> bool:
    """Lebt ein dj-set-Prozess mit dieser pid? Unter Windows nur python/dj-set-Abbilder,
    damit eine wiederverwendete pid keinen fremden Prozess trifft."""
    if sys.platform != "win32":
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        return True
    try:
        ausgabe = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
            capture_output=True, timeout=20, check=False,
        ).stdout.decode("ascii", errors="replace")
    except (OSError, subprocess.SubprocessError):
        return False
    for zeile in ausgabe.splitlines():
        felder = [f.strip('"') for f in zeile.split('","')]
        if len(felder) > 1 and felder[1].strip('"') == str(pid):
            name = felder[0].strip('"').lower()
            return name.startswith(("python", "dj-set", "pythonw"))
    return False


def _prozess_beenden(pid: int) -> None:
    """Beendet den Prozess samt Kindern."""
    try:
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/T", "/F", "/PID", str(pid)],
                capture_output=True, timeout=20, check=False,
            )
        else:
            os.kill(pid, 9)
    except (OSError, subprocess.SubprocessError) as fehler:
        LOG.warning("Prozess %s liess sich nicht beenden: %s", pid, fehler)


# ---------------------------------------------------------------- echte Vorgaben


def speicher_frei_gb() -> float:
    """CommitLimit minus CommittedBytes in GB. Bei jedem Fehler 0.0 (Auftrag wartet)."""
    befehl = (
        "$m = Get-CimInstance Win32_PerfFormattedData_PerfOS_Memory; "
        "[int64]($m.CommitLimit - $m.CommittedBytes)"
    )
    try:
        ausgabe = subprocess.run(
            ["powershell", "-NoProfile", "-Command", befehl],
            capture_output=True, timeout=20, check=False,
        ).stdout.decode("ascii", errors="replace")
        return int(ausgabe.strip().splitlines()[-1]) / 1024**3
    except (OSError, subprocess.SubprocessError, ValueError, IndexError):
        return 0.0


_BUDGET_CACHE: dict[str, Any] = {}


def _budget_modul() -> Any:
    pfad = os.environ.get("VIBEMIND_BUDGET_MODUL", "").strip()
    if pfad not in _BUDGET_CACHE:
        spec = importlib.util.spec_from_file_location("claude_budget_ext", pfad)
        if spec is None or spec.loader is None:
            raise ImportError(pfad)
        modul = importlib.util.module_from_spec(spec)
        # @dataclass sucht sys.modules[cls.__module__]; ohne Eintrag scheitert das Laden.
        sys.modules[spec.name] = modul
        try:
            spec.loader.exec_module(modul)
        except BaseException:
            sys.modules.pop(spec.name, None)
            raise
        _BUDGET_CACHE[pfad] = modul
    return _BUDGET_CACHE[pfad]


def budget_aus_umgebung() -> tuple[Callable[[str], tuple[bool, str]], Callable[[str, str, float], None]]:
    """(budget, buchen) ueber VIBEMIND_BUDGET_MODUL. Fehlt die Variable oder laesst sich
    das Modul nicht laden, ist die Pruefung aus (alles erlaubt, nichts gebucht)."""

    def modul() -> Any | None:
        if not os.environ.get("VIBEMIND_BUDGET_MODUL", "").strip():
            return None
        try:
            return _budget_modul()
        except Exception as fehler:  # noqa: BLE001 - Laden darf den Dienst nicht kippen
            LOG.warning("Budget-Modul nicht ladbar: %s", fehler)
            return None

    def budget(agent: str) -> tuple[bool, str]:
        geladen = modul()
        if geladen is None:
            return True, "aus"
        try:
            entscheidung = geladen.pruefen(agent)
        except Exception:  # noqa: BLE001 - wie die Shim: Waechterfehler sperrt
            return False, "waechter_fehler"
        return bool(entscheidung.erlaubt), str(entscheidung.grund)

    def buchen(agent: str, ergebnis: str, dauer_s: float) -> None:
        geladen = modul()
        if geladen is None:
            return
        try:
            geladen.buchen(agent, ergebnis, dauer_s)
        except Exception as fehler:  # noqa: BLE001 - Buchung darf den Dienst nicht kippen
            LOG.warning("Budget-Buchung fehlgeschlagen: %s", fehler)

    return budget, buchen


# ---------------------------------------------------------------- Laeufer


def _argumente(auftrag: Auftrag) -> list[str]:
    """Argumentliste fuer dj-set. Nur Werte aus der Eingabe, nie ein Dateipfad."""
    e = auftrag.eingabe
    art = auftrag.art

    def opt(name: str, wert: object) -> str:
        # `--name=wert`: ein Wert mit fuehrendem Bindestrich bleibt ein Wert.
        return f"--{name}={wert}"

    def songs_opt() -> list[str]:
        songs = e.get("songs")
        if not songs:
            return []
        if not isinstance(songs, list) or not all(isinstance(s, str) and s for s in songs):
            raise ValueError("songs muss eine Liste von Texten sein")
        return [opt("songs", ",".join(songs))]

    if art == "video":
        arg = ["video", opt("wunsch", e["wunsch"])]
        if e.get("frames") is not None:
            arg += [opt("frames", e["frames"]), opt("fps", e["fps"])]
        else:
            arg.append(opt("sekunden", e["sekunden"]))
        arg += songs_opt()
        if e.get("mit_gesang") is True and not e.get("ohne_gesang"):
            arg.append("--mit-gesang")
        elif e.get("ohne_gesang") or e.get("mit_gesang") is False:
            arg.append("--ohne-gesang")
        return arg
    if art == "set":
        arg = ["set", opt("wunsch", e["wunsch"]), opt("length", e["length"])]
        arg += songs_opt()
        if e.get("ohne_gesang"):
            arg.append("--ohne-gesang")
        return arg
    if art == "aufnehmen":
        datei = e["datei"]
        if (
            not isinstance(datei, str)
            or not datei
            or any(z in datei for z in ("/", "\\", ":"))
            or ".." in datei
            or datei != Path(datei).name
        ):
            raise ValueError("ungueltiger Dateiname")
        arg = [
            "aufnehmen", opt("datei", datei), opt("titel", e["titel"]),
            opt("herkunft", e["herkunft"]),
        ]
        if e.get("suno_id"):
            arg.append(opt("suno-id", e["suno_id"]))
        return arg
    raise ValueError(f"keine dj-set-Art: {art}")


class Laeufer:
    def __init__(
        self,
        ablage: Ablage,
        befehl: list[str],
        speicher_frei_gb: Callable[[], float],
        budget: Callable[[str], tuple[bool, str]],
        buchen: Callable[[str, str, float], None],
        laura_render: Callable[[Auftrag], dict],
        uhr: Callable[[], float] = time.monotonic,
        *,
        zeitlimits: dict[str, float] | None = None,
        wartegrenzen: dict[str, float] | None = None,
        speicher_pause_s: float = SPEICHER_PAUSE_S,
        wanduhr: Callable[[], datetime] = _jetzt,
    ) -> None:
        self.ablage = ablage
        self.befehl = list(befehl)
        self.speicher_frei_gb = speicher_frei_gb
        self.budget = budget
        self.buchen = buchen
        # Oeffentlich und spaet gebunden: der Server setzt sie nach dem Bau der Werkzeuge.
        self.laura_render = laura_render
        self.uhr = uhr
        self.zeitlimits = {**ZEITLIMITS_S, **(zeitlimits or {})}
        self.wartegrenzen = {**WARTEGRENZEN_S, **(wartegrenzen or {})}
        self.speicher_pause_s = speicher_pause_s
        self.wanduhr = wanduhr
        self._speicher_pause_bis: float | None = None
        self._sperre = threading.RLock()
        self._aktiv: tuple[str, subprocess.Popen[bytes]] | None = None
        self._abbruch: set[str] = set()

    # -- Lebenszyklus

    def beim_start(self) -> None:
        for auftrag in self.ablage.alle():
            if auftrag.zustand != "laeuft":
                continue
            if auftrag.pid and _prozess_lebt(auftrag.pid):
                _prozess_beenden(auftrag.pid)
            self._beenden(auftrag, "gescheitert", "Dienst neu gestartet")

    def schleife(self, stopp: threading.Event) -> None:
        while not stopp.is_set():
            try:
                getan = self.ein_schritt()
            except Exception:  # noqa: BLE001 - die Schleife darf nie sterben
                LOG.exception("Laeufer-Schritt gescheitert")
                getan = False
            if not getan:
                stopp.wait(1.0)

    def abbrechen(self, auftrag_id: str) -> bool:
        with self._sperre:
            try:
                auftrag = self.ablage.hole(auftrag_id)
            except KeyError:
                return False
            if auftrag.zustand == "wartet":
                self._beenden(auftrag, "abgebrochen", "abgebrochen")
                return True
            if auftrag.zustand == "laeuft":
                if auftrag.art == "laura_render":
                    return False  # laeuft in der injizierten Funktion, nicht unterbrechbar
                self._abbruch.add(auftrag_id)
                if self._aktiv and self._aktiv[0] == auftrag_id:
                    _prozess_beenden(self._aktiv[1].pid)
                return True
            return False

    # -- ein Schritt

    def _zu_lange_wartende(self) -> bool:
        """Wartende Auftraege ueber ihrer Wartegrenze (ab `erstellt`) enden als `gescheitert`,
        mit dem letzten Grund. Unter der Sperre rufen. -> ob einer beendet wurde."""
        jetzt = self.wanduhr()
        alle = self.ablage.alle()
        zustaende = {a.id: a.zustand for a in alle}
        beendet = False
        for a in alle:
            grenze = self.wartegrenzen.get(a.art)
            if a.zustand != "wartet" or grenze is None:
                continue
            if (jetzt - a.erstellt).total_seconds() <= grenze:
                continue
            musik = musik_offen(a, zustaende)
            letzter = a.grund or (f"wartete auf Musik-Auftrag {musik}" if musik else "kein Grund vermerkt")
            self._beenden(a, "gescheitert", _kuerzen(f"Wartete zu lange ({grenze / 60:.0f} min) – {letzter}"))
            beendet = True
        return beendet

    def ein_schritt(self) -> bool:
        with self._sperre:
            beendet = self._zu_lange_wartende()
            auftrag = self.ablage.naechster()
            if auftrag is None:
                return beendet
            if auftrag.art == "laura_render":
                self._starten(auftrag)
            elif self._speicher_pause_bis is not None and self.uhr() < self._speicher_pause_bis:
                return beendet  # zuletzt zu wenig Speicher: erst nach der Pause neu messen
        if auftrag.art != "laura_render":
            # Gemessen wird ausserhalb der Sperre (PowerShell braucht Sekunden); danach wird
            # der Auftrag neu gelesen, denn er kann inzwischen abgebrochen worden sein.
            try:
                frei = float(self.speicher_frei_gb())
            except Exception:  # noqa: BLE001 - unklarer Speicher heisst warten
                frei = 0.0
            with self._sperre:
                try:
                    aktuell = self.ablage.hole(auftrag.id)
                except KeyError:
                    return True
                if frei < MIN_FREI_GB:
                    self._speicher_pause_bis = self.uhr() + self.speicher_pause_s
                    grund = (f"Speicher: {SPEICHER_GRUND} ({frei:.1f} GB frei, "
                             f"mindestens {MIN_FREI_GB:.0f} GB noetig)")
                    if aktuell.zustand == "wartet" and aktuell.grund != grund:
                        aktuell.grund = grund
                        self.ablage.speichern(aktuell)
                    return beendet
                self._speicher_pause_bis = None
                if aktuell.zustand != "wartet":
                    return True  # inzwischen abgebrochen (o. ae.): nichts starten
                auftrag = aktuell
                if auftrag.art in _BUCHEN_ARTEN:
                    erlaubt, grund = self.budget(BUDGET_AGENT)
                    if not erlaubt:
                        self._beenden(auftrag, "gescheitert", _kuerzen(f"Budget: {grund}"))
                        return True
                self._starten(auftrag)
        if auftrag.art == "laura_render":
            self._laura_ausfuehren(auftrag)
        else:
            try:
                self._unterprozess_abwarten(auftrag)
            finally:
                with self._sperre:
                    self._abbruch.discard(auftrag.id)
        return True

    def _abschliessen(self, auftrag: Auftrag, zustand: str, grund: str = "") -> str:
        """Schreibt den Endzustand unter der Sperre; ein vorgemerkter Abbruch gewinnt."""
        with self._sperre:
            if auftrag.id in self._abbruch:
                zustand, grund = "abgebrochen", "abgebrochen"
            self._abbruch.discard(auftrag.id)
            self._beenden(auftrag, zustand, grund)
        return zustand

    def _starten(self, auftrag: Auftrag) -> None:
        auftrag.zustand = "laeuft"
        auftrag.grund = ""
        auftrag.gestartet = _jetzt()
        self.ablage.speichern(auftrag)

    def _beenden(self, auftrag: Auftrag, zustand: str, grund: str = "") -> None:
        auftrag.zustand = zustand  # type: ignore[assignment]
        auftrag.grund = grund
        auftrag.beendet = _jetzt()
        self.ablage.speichern(auftrag)

    def _laura_ausfuehren(self, auftrag: Auftrag) -> None:
        try:
            antwort = self.laura_render(auftrag)
            if antwort.get("ok"):
                auftrag.ergebnis = dict(antwort)
                self._beenden(auftrag, "fertig")
            else:
                self._beenden(auftrag, "gescheitert", _kuerzen(str(antwort.get("fehler", "unbekannt"))))
        except Exception as fehler:  # noqa: BLE001 - Dienstgrenze, fail-soft
            self._beenden(auftrag, "gescheitert", _kuerzen(str(fehler) or type(fehler).__name__))

    def _unterprozess_abwarten(self, auftrag: Auftrag) -> None:
        """Startet dj-set, wartet (Zeitlimit, Abbruch) und schreibt den Endzustand. Jede Ausnahme
        nach dem Start beendet den Prozessbaum und endet als `gescheitert` (nie haengt `laeuft`)."""
        ordner = self.ablage.ordner(auftrag.id)
        ausgabe, fehler = ordner / "ausgabe.log", ordner / "fehler.log"
        start = self.uhr()
        try:
            argumente = _argumente(auftrag)
        except ValueError as problem:
            self._abschliessen(auftrag, "gescheitert", _kuerzen(str(problem)))
            return
        except (KeyError, TypeError) as problem:
            self._abschliessen(auftrag, "gescheitert", _kuerzen(f"Eingabe unvollstaendig: {problem}"))
            return
        umgebung = kind_umgebung(self.ablage.daten)
        prozess: subprocess.Popen[bytes] | None = None
        try:
            with ausgabe.open("wb") as aus, fehler.open("wb") as err:
                prozess = subprocess.Popen(
                    [*self.befehl, *argumente],
                    stdin=subprocess.DEVNULL, stdout=aus, stderr=err, env=umgebung,
                )
                with self._sperre:
                    self._aktiv = (auftrag.id, prozess)
                    auftrag.pid = prozess.pid
                    self.ablage.speichern(auftrag)
                zeitlimit = self.zeitlimits[auftrag.art]
                gerissen = False
                while prozess.poll() is None:
                    if auftrag.id in self._abbruch:
                        _prozess_beenden(prozess.pid)
                    elif self.uhr() - start > zeitlimit:
                        gerissen = True
                        _prozess_beenden(prozess.pid)
                    try:
                        prozess.wait(timeout=_PAUSE_S)
                    except subprocess.TimeoutExpired:
                        continue
            with self._sperre:
                self._aktiv = None  # der Prozess ist zu Ende: abbrechen() darf ihn nicht mehr treffen
            self._auswerten(auftrag, prozess, start, gerissen, zeitlimit, ausgabe, fehler)
        except Exception as problem:  # noqa: BLE001 - kein Auftrag bleibt `laeuft`
            self._nach_fehler(auftrag, prozess, problem)
        finally:
            with self._sperre:
                self._aktiv = None

    def _nach_fehler(self, auftrag: Auftrag, prozess: subprocess.Popen[bytes] | None,
                     problem: Exception) -> None:
        """Jede Ausnahme ab dem Start: Prozessbaum beenden, Auftrag `gescheitert`."""
        if prozess is None:
            grund = f"Start fehlgeschlagen: {problem}"
        else:
            if prozess.poll() is None:
                _prozess_beenden(prozess.pid)
                try:
                    prozess.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    LOG.warning("Prozess %s lebt nach dem Beenden noch", prozess.pid)
            grund = f"Laeufer-Fehler: {type(problem).__name__}: {problem}"
            LOG.warning("Auftrag %s: %s", auftrag.id, grund)
        try:
            self._abschliessen(auftrag, "gescheitert", _kuerzen(grund))
        except Exception:  # noqa: BLE001 - bleibt die Datei unschreibbar, raeumt beim_start auf
            LOG.exception("Auftrag %s liess sich nicht als gescheitert speichern", auftrag.id)

    def _auswerten(self, auftrag: Auftrag, prozess: subprocess.Popen[bytes], start: float,
                   gerissen: bool, zeitlimit: float, ausgabe: Path, fehler: Path) -> None:
        dauer = self.uhr() - start
        text = ausgabe.read_text(encoding="utf-8", errors="replace")
        treffer = _RUN_ZEILE.findall(text)
        if treffer:
            auftrag.lauf = treffer[-1]
        buchen = True
        if gerissen:
            zustand, grund = "gescheitert", f"Zeitlimit von {zeitlimit:.0f} s erreicht"
        elif prozess.returncode == 2 and not treffer:
            # argparse-Fehler (Exit 2) ohne Lauf ist kein Befund.
            zustand = "gescheitert"
            grund = _kuerzen(f"Aufruf abgelehnt: {self._letzte_zeile(fehler, ausgabe)}")
            buchen = False
        elif prozess.returncode in _ENDE_ZUSTAND:
            zustand, grund = _ENDE_ZUSTAND[prozess.returncode], ""
        else:
            zustand = "gescheitert"
            grund = _kuerzen(f"Exit {prozess.returncode}: {self._letzte_zeile(fehler, ausgabe)}")
        zustand = self._abschliessen(auftrag, zustand, grund)
        if buchen and auftrag.art in _BUCHEN_ARTEN:
            try:
                self.buchen(BUDGET_AGENT, zustand, dauer)
            except Exception as problem:  # noqa: BLE001 - Buchung darf nichts kippen
                LOG.warning("Buchung fehlgeschlagen: %s", problem)

    @staticmethod
    def _letzte_zeile(*dateien: Path) -> str:
        for datei in dateien:
            try:
                zeilen = [z.strip() for z in datei.read_text(encoding="utf-8", errors="replace").splitlines()]
            except OSError:
                continue
            zeilen = [z for z in zeilen if z]
            if zeilen:
                return zeilen[-1]
        return "keine Ausgabe"
