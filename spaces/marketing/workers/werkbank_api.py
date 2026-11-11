"""Client der Werkbank-API (Marketing-API auf der VM, /api/werkbank/arbeiter, X-Bild-Key) fuer den PC-Arbeiter:
je Route eine Methode, dazu `hochladen` (Upload in Teilen mit Wiederaufnahme). Keine Fachlogik, kein Modell."""
from __future__ import annotations

import hashlib
import http.client
import json
import shutil
import time
import urllib.error
import urllib.request
from pathlib import Path

from spaces.marketing.workers.bild_worker import ApiFehler, tls_kontext

TEIL_BYTES = 8 * 1024 * 1024     # Teilgroesse des Uploads (die API nimmt hoechstens 8 MB je Teil)
WIEDERHOLUNGEN = 3               # je Teil
UPLOAD_PAUSE_S = 2


class WerkbankApi:
    """Die Routen der Werkbank-API (Task 4); jede Methode entspricht einer Route."""
    PRAEFIX = "/api/werkbank/arbeiter"

    def __init__(self, basis: str, schluessel: str):
        self.basis, self.schluessel = basis.rstrip("/"), schluessel
        self.tls = tls_kontext()
        self.schlafen = time.sleep

    def _senden(self, methode: str, pfad: str, koerper: bytes | None = None,
                typ: str = "application/json", zeit: int = 60) -> tuple[int, dict]:
        """(Status, JSON-Objekt). HTTP-Fehlerstatus kommen als Rueckgabe; Netzfehler (OSError) als Ausnahme."""
        req = urllib.request.Request(self.basis + self.PRAEFIX + pfad, data=koerper, method=methode,
                                     headers={"Content-Type": typ})
        req.add_unredirected_header("X-Bild-Key", self.schluessel)
        try:
            with urllib.request.urlopen(req, timeout=zeit, context=self.tls) as r:
                daten = json.loads(r.read() or b"{}")
                return r.status, daten if isinstance(daten, dict) else {}
        except urllib.error.HTTPError as e:
            try:
                daten = json.loads(e.read() or b"{}")
            except (ValueError, OSError):
                daten = {}
            return e.code, daten if isinstance(daten, dict) else {}

    def _anfrage(self, methode: str, pfad: str, daten: dict | None = None) -> dict:
        koerper = json.dumps(daten, ensure_ascii=False).encode("utf-8") if daten is not None else None
        status, antwort = self._senden(methode, pfad, koerper)
        if status >= 400:
            fehler = ApiFehler(status, str(antwort.get("detail", ""))[:300])
            fehler.laenge = antwort.get("laenge")
            raise fehler
        return antwort

    def naechster(self) -> dict | None:
        return self._anfrage("POST", "/naechster").get("auftrag")

    def arbeit(self) -> list[dict]:
        return self._anfrage("GET", "/arbeit").get("werke") or []

    def weiter(self, aid: str) -> bool:
        return bool(self._anfrage("POST", f"/{aid}/weiter").get("ok"))

    def frist(self, aid: str) -> bool:
        return bool(self._anfrage("POST", f"/{aid}/frist").get("ok"))

    def gliederung(self, aid: str, gliederung: dict, quellen: list) -> str:
        return self._anfrage("POST", f"/{aid}/gliederung", {"gliederung": gliederung, "quellen": quellen}).get("kennung", "")

    def szene_text(self, sid: str, koerper: dict) -> str:
        return self._anfrage("POST", f"/szene/{sid}/text", koerper).get("kennung", "")

    def szene_bild(self, sid: str, bildart: str, bild: dict, quellen: list | None = None,
                   belege: list | None = None) -> bool:
        """Nur das Bild neu; ohne `quellen`/`belege` bleiben die bisherigen Listen der Szene stehen."""
        koerper: dict = {"bildart": bildart, "bild": bild}
        if quellen is not None:
            koerper["quellen"] = quellen
        if belege is not None:
            koerper["belege"] = belege
        return bool(self._anfrage("POST", f"/szene/{sid}/bild", koerper).get("ok"))

    def szene_aufgegeben(self, sid: str, grund: str, wunsch: str) -> bool:
        """C1: die Szene liess sich nicht schreiben; `wunsch` = der Wunsch, mit dem es versucht wurde."""
        return bool(self._anfrage("POST", f"/szene/{sid}/aufgegeben", {"grund": grund, "wunsch": wunsch}).get("ok"))

    def gliederung_aufgegeben(self, aid: str, grund: str, wunsch: str) -> bool:
        return bool(self._anfrage("POST", f"/{aid}/gliederung-aufgegeben",
                                  {"grund": grund, "wunsch": wunsch}).get("ok"))

    def video_gescheitert(self, aid: str, versuch: int, grund: str) -> bool:
        """I1: das Zusammenfuegen des Versuchs `versuch` ist gescheitert."""
        return bool(self._anfrage("POST", f"/{aid}/video-gescheitert", {"grund": grund, "versuch": versuch}).get("ok"))

    def szene_nehmen(self, aid: str) -> dict | None:
        return self._anfrage("POST", f"/{aid}/szene-nehmen").get("szene")

    def szene_clip(self, sid: str, variante: int, datei: str, dauer_s: float, stimme: str, engine: str) -> str:
        return self._anfrage("POST", f"/szene/{sid}/clip", {"variante": variante, "datei": datei, "dauer_s": dauer_s,
                                                              "stimme": stimme, "engine": engine}).get("kennung", "")

    def szene_fehler(self, sid: str, grund: str) -> bool:
        return bool(self._anfrage("POST", f"/szene/{sid}/fehler", {"grund": grund}).get("ok"))

    def hinweis(self, aid: str, key: str, satz: str) -> bool:
        return bool(self._anfrage("POST", f"/{aid}/hinweis", {"key": key, "satz": satz}).get("ok"))

    def aufruf(self, aid: str) -> int:
        return int(self._anfrage("POST", f"/{aid}/aufruf").get("aufrufe") or 0)

    def fertig(self, aid: str, teil: dict) -> bool:
        return bool(self._anfrage("POST", f"/{aid}/fertig", teil).get("ok"))

    def _oeffnen(self, pfad: str, zeit: int = 300):
        """GET als Datenstrom (Antwortobjekt); HTTP-Fehler und Netzfehler kommen als Ausnahme."""
        req = urllib.request.Request(self.basis + self.PRAEFIX + pfad, method="GET")
        req.add_unredirected_header("X-Bild-Key", self.schluessel)
        return urllib.request.urlopen(req, timeout=zeit, context=self.tls)

    def holen(self, aid: str, name: str, ziel: Path) -> int:
        """Eine Datei des Auftrags in `ziel` laden: als Strom in eine .teil-Datei (nie ganz im Speicher), erst am
        Ende umbenannt. Rueckgabe: Bytes. Ein abgebrochener Abruf hinterlaesst nichts."""
        ziel = Path(ziel)
        teil = ziel.with_name(ziel.name + ".teil")
        try:
            with self._oeffnen(f"/{aid}/datei/{name}") as r, teil.open("wb") as f:
                erwartet = r.headers.get("Content-Length")
                shutil.copyfileobj(r, f, 1024 * 1024)
            geladen = teil.stat().st_size
            if erwartet and erwartet.isdigit() and int(erwartet) != geladen:
                raise ApiFehler(0, "Der Abruf brach ab.")
            teil.replace(ziel)
        except urllib.error.HTTPError as e:
            teil.unlink(missing_ok=True)
            raise ApiFehler(e.code, "Die Datei ließ sich nicht abrufen.") from None
        except BaseException:
            teil.unlink(missing_ok=True)
            raise
        return geladen

    def hochladen(self, aid: str, name: str, pfad: Path) -> None:
        """beginnen, Teile zu 8 MB, abschliessen mit sha256. Bei 409 auf einem Teil geht es ab der gemeldeten
        Laenge weiter; je Teil hoechstens WIEDERHOLUNGEN Wiederholungen (Netzfehler zaehlen ebenso)."""
        pfad = Path(pfad)
        groesse = pfad.stat().st_size
        pruefsumme = hashlib.sha256()
        with pfad.open("rb") as f:
            for stueck in iter(lambda: f.read(1024 * 1024), b""):
                pruefsumme.update(stueck)
        basis = f"/{aid}/datei/{name}"
        offset = int(self._anfrage("POST", basis + "/beginnen", {"bytes": groesse}).get("laenge") or 0)
        versuche = 0
        with pfad.open("rb") as f:
            while offset < groesse:
                f.seek(offset)
                teil = f.read(TEIL_BYTES)
                alt = offset
                try:
                    status, antwort = self._senden("PUT", f"{basis}/teil?offset={offset}", teil,
                                                   "application/octet-stream", 300)
                    if status == 409 and isinstance(antwort.get("laenge"), int) and 0 <= antwort["laenge"] <= groesse:
                        offset = antwort["laenge"]          # der Server weiss besser, wo wir stehen
                    elif status >= 500:
                        self.schlafen(UPLOAD_PAUSE_S)
                    elif status >= 400:
                        raise ApiFehler(status, str(antwort.get("detail", ""))[:300])
                    else:
                        offset = int(antwort["laenge"])
                except (OSError, http.client.HTTPException, KeyError, ValueError, TypeError):
                    self.schlafen(UPLOAD_PAUSE_S)
                if offset > alt:
                    versuche = 0
                else:
                    versuche += 1
                    if versuche > WIEDERHOLUNGEN:
                        raise ApiFehler(0, "Der Upload kam nicht voran.")
        self._anfrage("POST", basis + "/abschliessen", {"sha256": pruefsumme.hexdigest()})
